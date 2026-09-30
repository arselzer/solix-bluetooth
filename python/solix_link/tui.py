"""Optional terminal dashboard; importing this module does not require Textual."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import time
from typing import Any, Callable

from .client import SolixMonitor, discover
from .config import DEFAULT_CONFIG, DeviceConfig, load_config, save_config
from .protocol import Model
from .tou import TouPeriod, power_flow, validate_periods


METRIC_LABELS = {
    "battery_percentage": "Battery (%)",
    "battery_status": "Battery activity",
    "temperature_c": "Temperature (°C)",
    "input_power_w": "Total input (W)",
    "output_power_w": "Total output (W)",
    "ac_input_power_w": "AC input (W)",
    "ac_output_power_w": "AC output (W)",
    "ac_input_connected": "AC input connected",
    "ac_output_enabled": "AC output enabled",
    "dc_output_enabled": "DC output enabled",
    "usb_c1_power_w": "USB-C1 (W)",
    "usb_c2_power_w": "USB-C2 (W)",
    "usb_c3_power_w": "USB-C3 (W)",
    "solar_input_power_w": "Solar input (W)",
    "dc_input_active": "DC/PV input active",
    "dc_input_power_raw": "DC/PV input power (raw)",
    "controller_error_code": "Controller error code (raw)",
    "battery_health_raw": "Battery compatibility byte (raw)",
    "ac_charging_power_limit_w": "AC charging limit (W)",
    "max_charge_percentage": "Upper charge limit (%)",
    "min_charge_percentage": "Lower discharge limit (%)",
    "backup_reserve_percentage": "Backup reserve (%)",
    "usage_mode": "Usage mode",
    "active_tariff": "Active tariff",
    "tou_schedule_slot_count": "Tariff periods",
    "display_timeout_seconds": "Display timeout (s)",
    "device_timeout_minutes": "Device Timeout (min; 0 = Never)",
    "temperature_unit_fahrenheit": "Display uses Fahrenheit",
    "ac_off_grid_alert_enabled": "Off-grid alert enabled",
    "light_mode": "Light mode",
    "time_remaining_minutes": "Remaining time (min)",
    "software_version": "Firmware",
}


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    model: Model
    native: bool = False
    device: DeviceConfig | None = field(default=None, repr=False)
    saved: bool = True
    native_name: str | None = None


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    hint: str


def controls_for(target: Target) -> tuple[Control, ...]:
    """Expose model-supported operations; C2000 never gets an AC switch."""
    if target.native:
        maximum = 1200 if target.model == Model.C1000_GEN2 else 1800
        items = (
            Control("charge-power", "AC charging power", f"300–{maximum} W, in 100 W steps"),
            Control("charge-cap", "Upper charge limit", "80–100%, in 5% steps"),
            Control("reserve", "Backup reserve", "5–100%, in 5% steps; within current charge limits"),
        )
        if target.model == Model.C1000_GEN2:
            items += (
                Control("temperature-unit", "Temperature unit", "celsius or fahrenheit"),
                Control("off-grid-alert", "Off-grid alert", "on or off"),
                Control("discharge-floor", "Lower discharge limit", "1, 5, 10, 15 or 20%; requires reserve at least 5% higher"),
                Control("device-timeout", "Device Timeout", "0 = Never; 30, 60, 120, 240, 360, 720 or 1440 minutes. Finite choices may turn the station off when idle. Never disables this timeout; other sleep behavior may still interrupt remote access."),
            )
        return items
    limits = {
        Model.C300: "100, 200, 300 or 330 W",
        Model.C1000: "100–1000 W, in 100 W steps",
        Model.C1000_GEN2: "300–1200 W, in 100 W steps",
        Model.C2000_GEN2: "300–1800 W, in 100 W steps",
    }
    items = [Control("charge-power", "AC charging power", limits[target.model])]
    items.append(Control("display-timeout", "Display timeout", "30 or 60 seconds"))
    if target.model in (Model.C1000, Model.C1000_GEN2) and (target.device is None or target.device.protocol == (
            "legacy" if target.model == Model.C1000 else "prime")):
        items.append(Control("device-timeout", "Device Timeout", "0 = Never; 30, 60, 120, 240, 360, 720 or 1440 minutes. Finite choices may turn the station off when idle. Never disables this timeout; other sleep behavior may still interrupt remote access."))
    if target.model in (Model.C300, Model.C1000):
        items.extend((
            Control("ac-output", "AC output", "Enter on or off; changes the AC sockets"),
            Control("light", "Light mode", "0 off · 1 low · 2 medium · 3 high"),
        ))
    elif target.model == Model.C2000_GEN2:
        items.append(Control("charge-cap", "Upper charge limit", "80–100%, in 5% steps"))
    elif target.model == Model.C1000_GEN2:
        items.append(Control("charge-limits", "Charge / discharge limits", "Upper,lower — e.g. 100,1"))
    return tuple(items)


def parse_device_timeout(text: str) -> int:
    value = text.strip()
    if not value.isdecimal() or int(value) not in (0, 30, 60, 120, 240, 360, 720, 1440):
        raise ValueError("Device Timeout must be 0 (Never), 30, 60, 120, 240, 360, 720 or 1440 minutes")
    return int(value)


def parse_plan(text: str) -> list[dict[str, Any]]:
    """Parse up to six whole-hour tariff periods; empty text clears the plan."""
    periods = []
    for item in text.split(",") if text.strip() else []:
        parts = item.strip().split(":")
        if len(parts) != 3:
            raise ValueError("Use tariff:start:end, for example off_peak:0:6,peak:6:24")
        try:
            periods.append(TouPeriod(parts[0].strip(), int(parts[1]), int(parts[2])))
        except ValueError as error:
            raise ValueError("Use peak, mid_peak or off_peak and integer hours 0–24") from error
    return [period.to_dict() for period in validate_periods(periods)]


def safe_error(error: BaseException) -> str:
    """Keep actionable errors without reflecting device addresses or identifiers."""
    text = str(error)
    text = re.sub(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", "[device]", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{16,}\b", "[identifier]", text)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def public_snapshot(snapshot: dict) -> dict:
    """Only selected measurements cross into the display or event log."""
    metrics = snapshot.get("metrics", {})
    result = {key: snapshot.get(key) for key in (
        "connected", "available", "last_seen_timestamp", "power_flow", "control_enabled",
    )}
    result["metrics"] = {
        key: value for key, value in metrics.items()
        if key in METRIC_LABELS and isinstance(value, (int, float, str))
    }
    return result


class TuiBackend:
    """Serialize local monitoring and explicit control calls behind the UI."""

    def __init__(self, devices: list[DeviceConfig], ap_service_directory: Path | None = None,
                 *, monitor_factory: Callable[..., Any] = SolixMonitor,
                 requester: Callable[..., Any] | None = None,
                 scanner: Callable[..., Any] | None = None,
                 config_path: Path | None = None) -> None:
        self.targets = [Target(f"ble:{d.name}", f"{d.name} · {d.model.value}", d.model, device=d)
                        for d in devices]
        if ap_service_directory is not None:
            model = self._native_model(ap_service_directory)
            from .ap_service_config import load_ap_service_profiles
            if (ap_service_directory / "ap_service.json").exists():
                profiles = load_ap_service_profiles(ap_service_directory)
                for name, (config, _) in profiles.items():
                    key = "native" if len(profiles) == 1 else f"native:{name}"
                    self.targets.append(Target(key, f"{name} · {config.model.value} · Native MQTT", config.model,
                                               True, native_name=name if len(profiles) > 1 else None))
            else:
                self.targets.append(Target("native", f"Native MQTT · {model.value} · AP service", model, True))
        self.directory = ap_service_directory
        self.monitor_factory = monitor_factory
        self.requester = requester
        self.scanner = scanner
        self.config_path = config_path
        self.target: Target | None = None
        self.monitor: Any = None
        self.last_seen: float | None = None
        self.control_enabled = False
        self._lock = asyncio.Lock()

    @staticmethod
    def _native_model(directory: Path) -> Model:
        """Read only the profile model; fail clearly without reflecting secrets."""
        from .ap_service_config import load_ap_service
        profile = directory / "ap_service.json"
        message = "AP-service profile is invalid or unreadable; check ap_service.json and its owner-only permissions"
        try:
            profile.lstat()
        except FileNotFoundError:
            # Fake transports and uninitialized directories have no profile.
            return Model.C2000_GEN2
        except OSError:
            raise RuntimeError(message) from None
        try:
            return load_ap_service(profile).model
        except (OSError, ValueError, TypeError):
            raise RuntimeError(message) from None

    @staticmethod
    def _name(model: Model, used: set[str]) -> str:
        name, suffix = model.value, 2
        while name in used:
            name = f"{model.value}_{suffix}"
            suffix += 1
        return name

    async def scan(self) -> int:
        """Discover asynchronously, retaining saved entries and private pairing IDs."""
        found = await (self.scanner or discover)(timeout=8)
        async with self._lock:
            addresses = {t.device.address.upper() for t in self.targets if t.device}
            names = {t.device.name for t in self.targets if t.device}
            added = 0
            for device in found:
                if device.address.upper() in addresses:
                    continue
                try:
                    model = Model.from_name(device.name)
                    name = self._name(model, names)
                    config = DeviceConfig(name, device.address, model)
                except ValueError:
                    continue
                self.targets.append(Target(f"ble:{name}", f"{name} · {model.value} · new", model,
                                           device=config, saved=False))
                addresses.add(device.address.upper())
                names.add(name)
                added += 1
            return added

    async def save_target(self, key: str) -> Target:
        """Append one discovered station; never replace an existing saved identity."""
        async with self._lock:
            target = next((t for t in self.targets if t.key == key), None)
            if target is None or target.device is None or target.saved:
                raise ValueError("Choose a newly discovered Bluetooth station")
            if self.config_path is None:
                raise RuntimeError("No configuration path was provided")
            saved = await asyncio.to_thread(load_config, self.config_path)
            device = next((d for d in saved if d.address.upper() == target.device.address.upper()), None)
            if device is None:
                used = {d.name for d in saved} | {
                    item.device.name for item in self.targets if item.device and item.key != key
                }
                name = target.device.name if target.device.name not in used else self._name(target.model, used)
                device = replace(target.device, name=name)
                await asyncio.to_thread(save_config, [*saved, device], self.config_path)
            updated = Target(f"ble:{device.name}", f"{device.name} · {device.model.value}", device.model, device=device)
            self.targets = [updated if item.key == key else item for item in self.targets]
            if self.target and self.target.key == key:
                self.target = updated
            return updated

    async def _native(self, command: str, *, target: Target | None = None, **fields: Any) -> dict:
        if self.directory is None:
            raise RuntimeError("No AP-service directory was selected")
        requester = self.requester
        if requester is None:
            from .ap_service import ap_service_request
            requester = ap_service_request
        target = target or self.target
        if target and target.native_name:
            fields["name"] = target.native_name
        return await requester(self.directory, command, **fields)

    async def register_native(self) -> None:
        """Add the connected paired station to a stopped AP, without device writes."""
        from .ap_service_config import add_ap_service_device, load_ap_service, load_ap_service_profiles
        async with self._lock:
            target = self.target
            if self.directory is None or target is None or target.native or target.device is None:
                raise ValueError("Connect to a paired Gen 2 station over Bluetooth first")
            device = target.device
            if not target.saved or device.model not in (Model.C1000_GEN2, Model.C2000_GEN2) or device.protocol != "prime" or not device.client_id:
                raise ValueError("Save and pair a Gen 2 station before adding it to the AP")
            if not self._ble_snapshot()["available"]:
                raise ConnectionError("Fresh Bluetooth telemetry is required")
            serial = self.monitor.metrics.get("serial_number")
            if not isinstance(serial, str):
                raise RuntimeError("Device serial not reported; no AP profile changed")
            parent = load_ap_service(self.directory / "ap_service.json")
            config = replace(parent, name=device.name, model=device.model, device_serial=serial,
                             account_id=device.client_id, timezone_name=device.timezone_name or "Etc/UTC")
            await asyncio.to_thread(add_ap_service_device, self.directory, config)
            profiles = load_ap_service_profiles(self.directory)
            self.targets = [item for item in self.targets if not item.native]
            for name, (item, _) in profiles.items():
                self.targets.append(Target(f"native:{name}", f"{name} · {item.model.value} · Native MQTT", item.model,
                                           True, native_name=name))

    async def _close(self) -> None:
        monitor, self.monitor = self.monitor, None
        self.target = None
        self.last_seen = None
        self.control_enabled = False
        if monitor is not None:
            await monitor.disconnect()

    async def disconnect(self) -> None:
        async with self._lock:
            await self._close()

    async def connect(self, key: str) -> dict:
        async with self._lock:
            await self._close()
            target = next((item for item in self.targets if item.key == key), None)
            if target is None:
                raise ValueError("Choose a saved station or a running AP service")
            if target.native:
                snapshot = await self._native("status", target=target)
                self.target = target
                self.control_enabled = snapshot.get("control_enabled") is True
                return public_snapshot(snapshot)
            device = target.device
            if device.protocol == "prime" and not device.client_id:
                raise ValueError("Pair this station first with solix-link interactive (or solix-link pair), confirm its main button, then reopen the dashboard")
            def updated(_metrics: dict) -> None:
                self.last_seen = time.time()
            self.monitor = self.monitor_factory(
                device.address, model=device.model, owner_user_id=device.client_id,
                protocol=device.protocol, timezone_name=device.timezone_name, on_update=updated,
            )
            try:
                await self.monitor.connect(timeout=45)
                await self.monitor.wait_for_update(timeout=20)
                self.last_seen = time.time()
                self.target = target
                return self._ble_snapshot()
            except BaseException:
                await self._close()
                raise

    def _ble_snapshot(self) -> dict:
        connected = bool(self.monitor and self.monitor.connected)
        fresh = bool(connected and self.last_seen and time.time() - self.last_seen < 30)
        metrics = self.monitor.metrics if self.monitor else {}
        return public_snapshot({
            "connected": connected, "available": fresh, "last_seen_timestamp": self.last_seen,
            "metrics": metrics, "power_flow": power_flow(metrics) if fresh else "unknown",
        })

    async def refresh(self, *, force: bool = False) -> dict:
        async with self._lock:
            if self.target is None:
                return public_snapshot({"metrics": {}, "connected": False, "available": False})
            if self.target.native:
                snapshot = await self._native("status")
                self.control_enabled = snapshot.get("control_enabled") is True
                return public_snapshot(snapshot)
            if force and self.monitor.connected:
                await self.monitor.request_status()
                await self.monitor.wait_for_update(timeout=20)
            return self._ble_snapshot()

    async def control(self, action: str, value: str = "", *, enabled: bool = False) -> dict:
        async with self._lock:
            target = self.target
            if target is None:
                raise RuntimeError("Connect to a station first")
            allowed = {item.key for item in controls_for(target)}
            if target.native:
                allowed.update(("plan", "return-grid"))
            if action not in allowed:
                raise ValueError("This operation is unavailable for the selected station")
            if target.native:
                if not self.control_enabled:
                    raise RuntimeError("Native controls are disabled; start the AP service with --allow-control")
                if action == "plan":
                    response = await self._native("set-tou-plan", periods=parse_plan(value), enabled=enabled)
                elif action == "return-grid":
                    response = await self._native("return-grid", timeout=30)
                elif action == "temperature-unit":
                    if value.strip().lower() not in ("celsius", "fahrenheit"):
                        raise ValueError("Enter celsius or fahrenheit")
                    response = await self._native("set-temperature-unit", fahrenheit=value.strip().lower() == "fahrenheit")
                elif action == "off-grid-alert":
                    if value.strip().lower() not in ("on", "off"):
                        raise ValueError("Enter on or off")
                    response = await self._native("set-off-grid-alert", enabled=value.strip().lower() == "on")
                elif action == "discharge-floor":
                    response = await self._native("set-discharge-floor", lower=int(value))
                elif action == "device-timeout":
                    response = await self._native("set-device-timeout", minutes=parse_device_timeout(value))
                else:
                    command, field_name = {
                        "charge-power": ("set-charge-power", "watts"),
                        "charge-cap": ("set-charge-cap", "upper"),
                        "reserve": ("set-backup-reserve", "reserve"),
                    }[action]
                    response = await self._native(command, **{field_name: int(value)})
                return public_snapshot(response)
            if action == "device-timeout":
                await self.monitor.set_device_timeout(parse_device_timeout(value))
            elif action == "ac-output":
                if value.strip().lower() not in ("on", "off"):
                    raise ValueError("Enter on or off")
                await self.monitor.set_ac_output_enabled(value.strip().lower() == "on")
            elif action == "charge-limits":
                parts = value.split(",")
                if len(parts) != 2:
                    raise ValueError("Enter upper,lower percentages, for example 100,1")
                await self.monitor.set_charge_limits(*(int(part.strip()) for part in parts))
            else:
                method = {
                    "charge-power": self.monitor.set_ac_charging_power,
                    "charge-cap": self.monitor.set_charge_cap,
                    "display-timeout": self.monitor.set_display_timeout,
                    "light": self.monitor.set_light_mode,
                }[action]
                await method(int(value))
            return self._ble_snapshot()


def create_app(config_path: Path = DEFAULT_CONFIG, ap_service_directory: Path | None = None,
               *, backend: TuiBackend | None = None) -> Any:
    """Build the dashboard lazily, allowing CLI help without the TUI extra."""
    try:
        from textual import on
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
        from textual.screen import ModalScreen
        from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Select, Static, TabbedContent, TabPane
    except ImportError:
        raise RuntimeError("Install the terminal UI with: pip install 'solix-link[tui]'") from None

    backend = backend or TuiBackend(load_config(config_path), ap_service_directory, config_path=config_path)

    class DashboardTabs(TabbedContent):
        def _on_tab_pane_focused(self, event: Any) -> None:
            # Textual queues these messages. An earlier pane's focus message
            # must not reactivate it after focus has moved to another pane.
            event.prevent_default()
            focused = self.screen.focused
            if focused is not None and event.tab_pane in focused.ancestors_with_self:
                super()._on_tab_pane_focused(event)
            else:
                event.stop()

    class HelpScreen(ModalScreen):
        BINDINGS = [("escape", "dismiss", "Close"), ("question_mark", "dismiss", "Close")]
        DEFAULT_CSS = """
        HelpScreen { align: center middle; background: #0c1424 85%; }
        #help-dialog { width: 66; max-width: 95%; height: auto; max-height: 90%;
                       border: round #77dfc2; background: #13233a; padding: 1 2; }
        #help-title { color: #77dfc2; text-style: bold; margin-bottom: 1; }
        #help-close { margin-top: 1; width: 100%; }
        """

        def compose(self) -> ComposeResult:
            with VerticalScroll(id="help-dialog"):
                yield Static("Dashboard keyboard guide", id="help-title")
                yield Static(
                    "Tab / Shift+Tab   Move between fields and buttons\n"
                    "↑ / ↓, Enter      Select a station, setting or option\n"
                    "F1–F4             Overview, Controls, Hourly plan, Events\n"
                    "Ctrl+O            Connect to the selected station\n"
                    "Ctrl+S            Scan nearby Bluetooth stations\n"
                    "Ctrl+R            Request fresh readings\n"
                    "Ctrl+D            Disconnect monitoring\n"
                    "Ctrl+Q / Ctrl+C   Quit after the current operation\n"
                    "? / F10           Open this guide\n\n"
                    "Connection, status and summaries stay in place. Scroll "
                    "inside the selected panel for longer lists.\n\n"
                    "Changing a value does nothing until Apply is selected. "
                    "Closing the dashboard disconnects monitoring and keeps "
                    "station settings. Pairing and AP setup remain available "
                    "through solix-link interactive.", markup=False,
                )
                yield Button("Back to dashboard · Esc", id="help-close", variant="primary")

        @on(Button.Pressed, "#help-close")
        def close_help(self) -> None:
            self.dismiss()

    class SolixApp(App):
        TITLE = "SOLIX Link"
        SUB_TITLE = "Local station console"
        BINDINGS = [
            Binding("f1", "panel('overview')", "Overview", priority=True),
            Binding("f2", "panel('controls')", "Controls", priority=True),
            Binding("f3", "panel('plan')", "Plan", priority=True),
            Binding("f4", "panel('events')", "Events", priority=True),
            Binding("ctrl+o", "connect", "Connect", show=False, priority=True),
            Binding("ctrl+s", "scan", "Scan", priority=True),
            Binding("ctrl+r", "refresh", "Refresh", priority=True),
            Binding("ctrl+d", "disconnect", "Disconnect", show=False, priority=True),
            Binding("question_mark,f10", "help", "Help"),
            Binding("ctrl+q,ctrl+c", "quit", "Quit", priority=True),
            Binding("s", "scan", "Scan", show=False),
            Binding("r", "refresh", "Refresh", show=False),
            Binding("d", "disconnect", "Disconnect", show=False),
            Binding("q", "quit", "Quit", show=False),
        ]
        CSS = """
        Screen { background: #0c1424; color: #e2ebfa; }
        Header { background: #13233a; color: #77dfc2; }
        Footer { background: #13233a; }
        #body { height: 1fr; padding: 0 2; overflow: hidden hidden; }
        #connection { height: 3; }
        #station { width: 1fr; margin-right: 1; }
        #connection-buttons { width: 34; height: 3; }
        #connection Button { margin-right: 1; }
        #discovery { height: auto; }
        #discovery Button { margin-right: 1; }
        #discovery-hint { height: auto; width: 1fr; color: #a8bdd4; padding-top: 1; }
        #connection-status { height: 1; color: #a8bdd4; }
        #connection-status.live { color: #77dfc2; }
        #connection-status.busy, #connection-status.error { color: #ffcc80; }
        #keyboard-hint { height: 1; color: #8298b5; }
        #cards { grid-size: 3; grid-gutter: 1; height: 4; }
        .card { border: round #2c4866; background: #13233a; padding: 0 1; content-align: center middle; }
        #compact-summary { display: none; height: auto; max-height: 2; color: #77dfc2; }
        #tabs { height: 1fr; min-height: 6; }
        #tabs ContentSwitcher { height: 1fr; }
        TabPane { height: 1fr; padding: 0 1; }
        #controls-scroll, #plan-scroll { height: 1fr; }
        #readings { height: 1fr; }
        #event-log { height: 1fr; border: round #2c4866; }
        .form-label { margin-top: 1; color: #77dfc2; }
        .hint { color: #a8bdd4; height: auto; margin: 1 0; }
        .form-row { height: auto; margin-bottom: 1; }
        .form-row Input { width: 1fr; margin-right: 1; }
        #apply-setting, #apply-plan { min-width: 18; }
        #plan-text { width: 1fr; }
        #plan-mode { margin-bottom: 1; }
        #notice { color: #ffcc80; height: auto; margin-top: 1; }
        .narrow #body { padding: 0 1; }
        .narrow #connection { layout: vertical; height: 6; }
        .narrow #station { width: 100%; }
        .narrow #connection-buttons { width: 100%; }
        .narrow #discovery { layout: vertical; }
        #discovery-buttons { width: 52; height: 3; }
        .narrow #discovery-buttons { width: 100%; }
        .compact #add-to-ap { display: none; }
        .narrow #discovery-hint { width: 100%; padding-top: 0; }
        .narrow .form-row { layout: vertical; }
        .narrow .form-row Input { width: 100%; }
        .compact #cards, .compact #discovery-hint { display: none; }
        .compact #compact-summary { display: block; }
        .compact #connection-status { height: 2; }
        Button:focus, Input:focus, Select:focus { border: tall #77dfc2; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.backend = backend
            self.selected = backend.targets[0].key if backend.targets else None
            self.busy = False
            self.refreshing = False
            self.snapshot: dict = {}
            self.reading_keys: tuple[str, ...] = ()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="body"):
                with Horizontal(id="connection"):
                    yield Select([(t.label, t.key) for t in backend.targets],
                                 value=self.selected if self.selected else Select.NULL,
                                 prompt="Scan for nearby stations", allow_blank=True, id="station")
                    with Horizontal(id="connection-buttons"):
                        yield Button("Connect", id="connect", variant="primary", disabled=not backend.targets)
                        yield Button("Disconnect", id="disconnect", disabled=True)
                with Horizontal(id="discovery"):
                    with Horizontal(id="discovery-buttons"):
                        yield Button("Scan Bluetooth", id="scan")
                        yield Button("Save station", id="save-station", disabled=True)
                        yield Button("Add to AP", id="add-to-ap", disabled=True)
                    yield Static("Scan nearby stations or choose a saved station.", id="discovery-hint", markup=False)
                yield Static("Disconnected · Choose a station, then Connect.", id="connection-status", markup=False)
                yield Static("Tab / Shift+Tab navigate · Enter select · F1–F4 panels · ? help", id="keyboard-hint", markup=False)
                with Grid(id="cards"):
                    yield Static("BATTERY\n—", classes="card", id="battery", markup=False)
                    yield Static("POWER\n—", classes="card", id="power", markup=False)
                    yield Static("SUPPLY\nUnknown", classes="card", id="flow", markup=False)
                yield Static("Battery — · Input — · Output — · Supply unknown", id="compact-summary", markup=False)
                with DashboardTabs(id="tabs"):
                    with TabPane("Overview", id="overview"):
                        yield DataTable(id="readings", zebra_stripes=True, cursor_type="row")
                    with TabPane("Controls", id="controls"):
                        with VerticalScroll(id="controls-scroll"):
                            yield Label("Station setting", classes="form-label")
                            yield Select([], prompt="Connect to choose a setting", id="setting", allow_blank=True)
                            yield Static("Changes apply only when you select Apply.", id="control-hint", classes="hint", markup=False)
                            with Horizontal(classes="form-row"):
                                yield Input(placeholder="Value", id="setting-value")
                                yield Button("Apply setting", id="apply-setting", variant="primary", disabled=True)
                            yield Static("", id="notice", markup=False)
                    with TabPane("Hourly plan", id="plan"):
                        with VerticalScroll(id="plan-scroll"):
                            yield Static("Native MQTT only · local station hours · up to six non-overlapping periods", classes="hint", markup=False)
                            yield Label("Periods (empty clears the plan)", classes="form-label")
                            yield Input(placeholder="off_peak:0:6,peak:6:24", id="plan-text")
                            yield Static("Tariffs: peak, mid_peak, off_peak. Split overnight periods at midnight.\nActivating a plan persists until you change it or return to grid.", classes="hint", markup=False)
                            yield Select([("Store in Standard mode", "store"), ("Activate Time-of-Use", "activate")],
                                         value="store", allow_blank=False, id="plan-mode")
                            with Horizontal(classes="form-row"):
                                yield Button("Apply hourly plan", id="apply-plan", variant="primary", disabled=True)
                                yield Button("Return to grid", id="return-grid", disabled=True)
                            yield Static("Return to grid clears the plan and waits for observed grid supply. It keeps AC output enabled.", classes="hint", markup=False)
                    with TabPane("Events", id="events"):
                        yield RichLog(id="event-log", markup=False, wrap=True, max_lines=200)
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#readings", DataTable)
            table.add_column("Measurement", key="measurement")
            table.add_column("Value", key="value")
            self.configure_controls()
            self.query_one("#station", Select).focus()
            self.set_interval(2, self.poll)

        def on_resize(self, event: Any) -> None:
            self.set_class(event.size.width < 82, "narrow")
            self.set_class(event.size.height < 32 or event.size.width < 58, "compact")
            self.query_one("#keyboard-hint", Static).update(
                "Tab / Enter · F1–F4 panels · F10 help" if event.size.width < 82
                else "Tab / Shift+Tab navigate · Enter select · F1–F4 panels · ? help"
            )

        def event_log(self, message: str) -> None:
            self.query_one("#event-log", RichLog).write(f"{time.strftime('%H:%M:%S')}  {message}")

        def status(self, message: str, kind: str = "idle") -> None:
            widget = self.query_one("#connection-status", Static)
            widget.update(message)
            for state in ("live", "busy", "error"):
                widget.set_class(state == kind, state)

        def current_target(self) -> Target | None:
            return next((t for t in backend.targets if t.key == self.selected), None)

        def configure_controls(self) -> None:
            target = self.current_target()
            options = controls_for(target) if target else ()
            selector = self.query_one("#setting", Select)
            selector.set_options([(item.label, item.key) for item in options])
            selector.value = options[0].key if options else Select.NULL
            note = "Original C1000 controls were verified on firmware code 151; record settings before testing other versions." if target and target.model == Model.C1000 else ""
            if target and target.native:
                note = "Controls require the running AP service to have been started with --allow-control."
            self.query_one("#notice", Static).update(note)
            guidance = "Scan nearby stations or choose a saved station."
            if target and not target.saved:
                guidance = f"Save as {target.device.name}, or connect for this session."
            if target and target.device and target.device.protocol == "prime" and not target.device.client_id:
                guidance = "Gen 2 needs pairing: use solix-link interactive, confirm the main button, then reopen this dashboard."
            self.query_one("#discovery-hint", Static).update(guidance)
            self.update_buttons()

        def update_buttons(self) -> None:
            connected = backend.target is not None
            fresh = bool(self.snapshot.get("available"))
            target = self.current_target()
            permitted = not (target and target.native) or self.snapshot.get("control_enabled") is True
            for widget in ("station", "setting", "setting-value", "plan-text", "plan-mode"):
                self.query_one(f"#{widget}").disabled = self.busy
            self.query_one("#connect", Button).disabled = self.busy or not self.selected
            self.query_one("#scan", Button).disabled = self.busy
            self.query_one("#save-station", Button).disabled = self.busy or backend.config_path is None or not target or target.saved
            self.query_one("#add-to-ap", Button).disabled = self.busy or not connected or not fresh or backend.directory is None or not target or target.native or not target.saved or target.model not in (Model.C1000_GEN2, Model.C2000_GEN2)
            self.query_one("#disconnect", Button).disabled = self.busy or not connected
            self.query_one("#apply-setting", Button).disabled = self.busy or not connected or not fresh or not permitted
            for name in ("apply-plan", "return-grid"):
                self.query_one(f"#{name}", Button).disabled = self.busy or not connected or not fresh or not permitted or not (target and target.native)

        def render_snapshot(self, snapshot: dict) -> None:
            self.snapshot = snapshot
            metrics = snapshot.get("metrics", {})
            fresh = bool(snapshot.get("available"))
            latest = snapshot.get("last_seen_timestamp")
            age = f" · {max(0, int(time.time() - latest))}s since update" if isinstance(latest, (int, float)) else ""
            state = "Live" if fresh else "Waiting for fresh telemetry" if snapshot.get("connected") else "Disconnected"
            permission = " · Read only" if backend.target and backend.target.native and not snapshot.get("control_enabled") else ""
            self.status(state + age + permission, "live" if fresh else "idle")
            self.query_one("#battery", Static).update(f"BATTERY\n{metrics.get('battery_percentage', '—')}% · {metrics.get('battery_status', 'unknown')}")
            incoming = metrics.get("input_power_w", metrics.get("ac_input_power_w", "—"))
            outgoing = metrics.get("output_power_w", metrics.get("ac_output_power_w", "—"))
            self.query_one("#power", Static).update(f"POWER\n{incoming} W in  ·  {outgoing} W out")
            flow = str(snapshot.get("power_flow") or "unknown") if fresh else "unknown"
            self.query_one("#flow", Static).update(f"SUPPLY\n{flow.replace('_', ' ').title()}")
            self.query_one("#compact-summary", Static).update(
                f"Battery {metrics.get('battery_percentage', '—')}% · {incoming} W in · {outgoing} W out · {flow}"
            )
            table = self.query_one("#readings", DataTable)
            keys = tuple(key for key in METRIC_LABELS if key in metrics)
            if keys != self.reading_keys:
                row, scroll = table.cursor_row, table.scroll_y
                table.clear()
                for key in keys:
                    table.add_row(METRIC_LABELS[key], str(metrics[key]), key=key)
                if keys:
                    table.move_cursor(row=min(row, len(keys) - 1), column=0, scroll=False)
                    table.scroll_to(y=scroll, animate=False)
                self.reading_keys = keys
            else:
                for key in keys:
                    table.update_cell(key, "value", str(metrics[key]), update_width=True)
            self.update_buttons()

        def launch(self, coroutine: Any, *, control: bool = False) -> None:
            if self.busy:
                coroutine.close()
                return
            self.busy = True
            self.update_buttons()
            if control:
                self.status("Applying setting — waiting for fresh telemetry…", "busy")
                self.event_log("Applying the selected control…")
            async def operation() -> None:
                try:
                    result = await coroutine
                    if isinstance(result, dict):
                        self.render_snapshot(result)
                    if control:
                        self.event_log("Setting confirmed by fresh station telemetry.")
                except Exception as error:
                    self.event_log(safe_error(error))
                    if control:
                        self.event_log("A failed write may have taken effect. Check fresh status before retrying.")
                    snapshot = getattr(error, "snapshot", None)
                    if isinstance(snapshot, dict):
                        self.render_snapshot(public_snapshot(snapshot))
                    self.status(safe_error(error), "error")
                finally:
                    self.busy = False
                    self.update_buttons()
            self.run_worker(operation(), group="operation", exit_on_error=False)

        @on(Select.Changed, "#station")
        def station_changed(self, event: Any) -> None:
            key = event.value if isinstance(event.value, str) else None
            if key == self.selected:
                return
            self.selected = key
            self.snapshot = {}
            self.configure_controls()
            async def changed() -> dict:
                await backend.disconnect()
                return public_snapshot({"metrics": {}})
            self.launch(changed())

        @on(Select.Changed, "#setting")
        def setting_changed(self, event: Any) -> None:
            target = self.current_target()
            if target:
                spec = next((c for c in controls_for(target) if c.key == event.value), None)
                self.query_one("#control-hint", Static).update(spec.hint if spec else "Choose a setting")
                self.query_one("#setting-value", Input).value = ""

        @on(Button.Pressed)
        def button_pressed(self, event: Any) -> None:
            action = event.button.id
            if action == "connect" and self.selected:
                self.action_connect()
            elif action == "disconnect":
                self.action_disconnect()
            elif action == "scan":
                self.action_scan()
            elif action == "save-station" and self.selected:
                async def save() -> None:
                    target = await backend.save_target(self.selected)
                    self.selected = target.key
                    self.refresh_targets()
                    self.event_log(f"Saved station as {target.device.name}.")
                self.launch(save())
            elif action == "add-to-ap":
                async def add_to_ap() -> None:
                    await backend.register_native()
                    self.refresh_targets()
                    self.event_log("Station added to the shared AP. Use ap-service-run --provision --name to join it.")
                self.launch(add_to_ap())
            elif action == "apply-setting":
                key = self.query_one("#setting", Select).value
                if isinstance(key, str):
                    self.launch(backend.control(key, self.query_one("#setting-value", Input).value), control=True)
            elif action == "apply-plan":
                text = self.query_one("#plan-text", Input).value
                enabled = self.query_one("#plan-mode", Select).value == "activate"
                self.launch(backend.control("plan", text, enabled=enabled), control=True)
            elif action == "return-grid":
                self.launch(backend.control("return-grid"), control=True)

        def poll(self) -> None:
            if self.busy or self.refreshing or backend.target is None:
                return
            self.refreshing = True
            async def refresh() -> None:
                try:
                    snapshot = await backend.refresh()
                    if not self.busy:
                        self.render_snapshot(snapshot)
                except Exception as error:
                    if not self.busy:
                        self.render_snapshot(public_snapshot({"metrics": {}}))
                        self.status(safe_error(error), "error")
                finally:
                    self.refreshing = False
            self.run_worker(refresh(), group="refresh", exit_on_error=False)

        def action_refresh(self) -> None:
            self.launch(backend.refresh(force=True))

        def action_connect(self) -> None:
            if self.busy or not self.selected:
                return
            self.status("Connecting to the selected local station…", "busy")
            self.event_log("Connecting to the selected local station…")
            self.launch(backend.connect(self.selected))

        def action_panel(self, panel: str) -> None:
            self.screen.set_focus(None)
            self.query_one("#tabs", TabbedContent).active = panel
            widget = {"overview": "readings", "controls": "setting", "plan": "plan-text", "events": "event-log"}[panel]
            self.screen.set_focus(self.query_one(f"#{widget}"))

        def action_help(self) -> None:
            if not isinstance(self.screen, HelpScreen):
                self.push_screen(HelpScreen())

        def refresh_targets(self) -> None:
            selector = self.query_one("#station", Select)
            selected = self.selected
            if not any(t.key == selected for t in backend.targets):
                selected = backend.targets[0].key if backend.targets else None
            self.selected = selected
            # Rebuilding options emits selection events; suppress temporary
            # NULL values so refreshing the list cannot disconnect a session.
            with self.prevent(Select.Changed):
                selector.set_options([(target.label, target.key) for target in backend.targets])
                selector.value = selected if selected else Select.NULL
            self.configure_controls()

        def action_scan(self) -> None:
            async def scan() -> None:
                self.status("Scanning nearby Bluetooth stations…", "busy")
                count = await backend.scan()
                self.refresh_targets()
                message = f"Scan complete: {count} new supported station(s)."
                self.event_log(message)
                self.status(message)
            self.launch(scan())

        def action_disconnect(self) -> None:
            async def disconnect() -> dict:
                await backend.disconnect()
                self.event_log("Disconnected; station settings are unchanged.")
                return public_snapshot({"metrics": {}})
            self.launch(disconnect())

        async def action_quit(self) -> None:
            if self.busy:
                self.event_log("Wait for the current operation to finish before closing.")
                return
            await backend.disconnect()
            self.exit()

        async def on_unmount(self) -> None:
            await backend.disconnect()

    return SolixApp()


def run_tui(config_path: Path = DEFAULT_CONFIG, ap_service_directory: Path | None = None) -> None:
    """Open the optional dashboard without starting services or changing settings."""
    create_app(config_path, ap_service_directory).run()
