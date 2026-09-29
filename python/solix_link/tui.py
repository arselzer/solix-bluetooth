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
    "ac_charging_power_limit_w": "AC charging limit (W)",
    "max_charge_percentage": "Upper charge limit (%)",
    "min_charge_percentage": "Lower discharge limit (%)",
    "backup_reserve_percentage": "Backup reserve (%)",
    "usage_mode": "Usage mode",
    "active_tariff": "Active tariff",
    "tou_schedule_slot_count": "Tariff periods",
    "display_timeout_seconds": "Display timeout (s)",
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


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    hint: str


def controls_for(target: Target) -> tuple[Control, ...]:
    """Expose model-supported operations; C2000 never gets an AC switch."""
    if target.native:
        return (
            Control("charge-power", "AC charging power", "300–1800 W, in 100 W steps"),
            Control("charge-cap", "Upper charge limit", "80–100%, in 5% steps"),
            Control("reserve", "Backup reserve", "5–100%, in 5% steps; within current charge limits"),
        )
    limits = {
        Model.C300: "100, 200, 300 or 330 W",
        Model.C1000: "100–1000 W, in 100 W steps; hardware verification pending",
        Model.C1000_GEN2: "300–1200 W, in 100 W steps",
        Model.C2000_GEN2: "300–1800 W, in 100 W steps",
    }
    items = [Control("charge-power", "AC charging power", limits[target.model])]
    items.append(Control("display-timeout", "Display timeout", "30 or 60 seconds"))
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

    def __init__(self, devices: list[DeviceConfig], lab_directory: Path | None = None,
                 *, monitor_factory: Callable[..., Any] = SolixMonitor,
                 requester: Callable[..., Any] | None = None,
                 scanner: Callable[..., Any] | None = None,
                 config_path: Path | None = None) -> None:
        self.targets = [Target(f"ble:{d.name}", f"{d.name} · {d.model.value}", d.model, device=d)
                        for d in devices]
        if lab_directory is not None:
            self.targets.append(Target("native", "Native MQTT · running local lab", Model.C2000_GEN2, True))
        self.directory = lab_directory
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

    async def _native(self, command: str, **fields: Any) -> dict:
        if self.directory is None:
            raise RuntimeError("No local lab directory was selected")
        requester = self.requester
        if requester is None:
            from .lab_service import lab_request
            requester = lab_request
        return await requester(self.directory, command, **fields)

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
                raise ValueError("Choose a saved station or a running local lab")
            if target.native:
                snapshot = await self._native("status")
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
                    raise RuntimeError("Native controls are disabled; start the lab with --allow-control")
                if action == "plan":
                    response = await self._native("set-tou-plan", periods=parse_plan(value), enabled=enabled)
                elif action == "return-grid":
                    response = await self._native("return-grid", timeout=30)
                else:
                    command, field_name = {
                        "charge-power": ("set-charge-power", "watts"),
                        "charge-cap": ("set-charge-cap", "upper"),
                        "reserve": ("set-backup-reserve", "reserve"),
                    }[action]
                    response = await self._native(command, **{field_name: int(value)})
                return public_snapshot(response)
            if action == "ac-output":
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


def create_app(config_path: Path = DEFAULT_CONFIG, lab_directory: Path | None = None,
               *, backend: TuiBackend | None = None) -> Any:
    """Build the dashboard lazily, allowing CLI help without the TUI extra."""
    try:
        from textual import on
        from textual.app import App, ComposeResult
        from textual.containers import Grid, Horizontal, VerticalScroll
        from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Select, Static, TabbedContent, TabPane
    except ImportError:
        raise RuntimeError("Install the terminal UI with: pip install 'solix-link[tui]'") from None

    backend = backend or TuiBackend(load_config(config_path), lab_directory, config_path=config_path)

    class SolixApp(App):
        TITLE = "SOLIX Link"
        SUB_TITLE = "Local station console"
        BINDINGS = [("s", "scan", "Scan"), ("r", "refresh", "Refresh"), ("d", "disconnect", "Disconnect"),
                    ("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]
        CSS = """
        Screen { background: #0c1424; color: #e2ebfa; }
        Header { background: #13233a; color: #77dfc2; }
        Footer { background: #13233a; }
        #body { padding: 1 2; }
        #connection { height: auto; margin-bottom: 1; }
        #station { width: 1fr; margin-right: 1; }
        #connection-buttons { width: 34; height: 3; }
        #connection Button { margin-right: 1; }
        #discovery { height: auto; margin-bottom: 1; }
        #discovery Button { margin-right: 1; }
        #discovery-hint { height: auto; width: 1fr; color: #a8bdd4; padding-top: 1; }
        #connection-status { height: 2; color: #a8bdd4; }
        #cards { grid-size: 3; grid-gutter: 1; height: 5; margin-bottom: 1; }
        .card { border: round #2c4866; background: #13233a; padding: 0 2; content-align: center middle; }
        #tabs { height: auto; min-height: 18; }
        TabPane { padding: 1; }
        #readings { height: 16; }
        #event-log { height: 16; border: round #2c4866; }
        .form-label { margin-top: 1; color: #77dfc2; }
        .hint { color: #a8bdd4; height: auto; margin: 1 0; }
        .form-row { height: auto; margin-bottom: 1; }
        .form-row Input { width: 1fr; margin-right: 1; }
        #apply-setting, #apply-plan { min-width: 18; }
        #plan-text { width: 1fr; }
        #plan-mode { margin-bottom: 1; }
        #notice { color: #ffcc80; height: auto; margin-top: 1; }
        .narrow #connection { layout: vertical; }
        .narrow #station { width: 100%; }
        .narrow #connection-buttons { width: 100%; }
        .narrow #discovery { layout: vertical; }
        #discovery-buttons { width: 34; height: 3; }
        .narrow #discovery-hint { width: 100%; padding-top: 0; }
        .narrow #cards { grid-size: 1; grid-rows: 4; height: 14; }
        .narrow .form-row { layout: vertical; }
        .narrow .form-row Input { width: 100%; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.backend = backend
            self.selected = backend.targets[0].key if backend.targets else None
            self.busy = False
            self.refreshing = False
            self.snapshot: dict = {}

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with VerticalScroll(id="body"):
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
                    yield Static("Scan nearby stations or choose a saved station.", id="discovery-hint", markup=False)
                yield Static("Choose a station, then Connect.", id="connection-status", markup=False)
                with Grid(id="cards"):
                    yield Static("BATTERY\n—", classes="card", id="battery", markup=False)
                    yield Static("POWER\n—", classes="card", id="power", markup=False)
                    yield Static("SUPPLY\nUnknown", classes="card", id="flow", markup=False)
                with TabbedContent(id="tabs"):
                    with TabPane("Overview", id="overview"):
                        yield DataTable(id="readings", zebra_stripes=True)
                    with TabPane("Controls", id="controls"):
                        yield Label("Station setting", classes="form-label")
                        yield Select([], prompt="Connect to choose a setting", id="setting", allow_blank=True)
                        yield Static("Changes apply only when you select Apply.", id="control-hint", classes="hint", markup=False)
                        with Horizontal(classes="form-row"):
                            yield Input(placeholder="Value", id="setting-value")
                            yield Button("Apply setting", id="apply-setting", variant="primary", disabled=True)
                        yield Static("", id="notice", markup=False)
                    with TabPane("Hourly plan", id="plan"):
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
            self.query_one("#readings", DataTable).add_columns("Measurement", "Value")
            self.configure_controls()
            self.set_interval(2, self.poll)

        def on_resize(self, event: Any) -> None:
            self.set_class(event.size.width < 82, "narrow")

        def event_log(self, message: str) -> None:
            self.query_one("#event-log", RichLog).write(f"{time.strftime('%H:%M:%S')}  {message}")

        def current_target(self) -> Target | None:
            return next((t for t in backend.targets if t.key == self.selected), None)

        def configure_controls(self) -> None:
            target = self.current_target()
            options = controls_for(target) if target else ()
            selector = self.query_one("#setting", Select)
            selector.set_options([(item.label, item.key) for item in options])
            selector.value = options[0].key if options else Select.NULL
            note = "Original C1000 controls follow reference mappings; hardware verification is pending." if target and target.model == Model.C1000 else ""
            if target and target.native:
                note = "Controls require the running lab to have been started with --allow-control."
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
            self.query_one("#connection-status", Static).update(state + age)
            self.query_one("#battery", Static).update(f"BATTERY\n{metrics.get('battery_percentage', '—')}% · {metrics.get('battery_status', 'unknown')}")
            incoming = metrics.get("input_power_w", metrics.get("ac_input_power_w", "—"))
            outgoing = metrics.get("output_power_w", metrics.get("ac_output_power_w", "—"))
            self.query_one("#power", Static).update(f"POWER\n{incoming} W in  ·  {outgoing} W out")
            flow = str(snapshot.get("power_flow") or "unknown") if fresh else "unknown"
            self.query_one("#flow", Static).update(f"SUPPLY\n{flow.replace('_', ' ').title()}")
            table = self.query_one("#readings", DataTable)
            table.clear()
            for key, label in METRIC_LABELS.items():
                if key in metrics:
                    table.add_row(label, str(metrics[key]))
            self.update_buttons()

        def launch(self, coroutine: Any, *, control: bool = False) -> None:
            if self.busy:
                coroutine.close()
                return
            self.busy = True
            self.update_buttons()
            if control:
                self.query_one("#connection-status", Static).update("Applying setting — waiting for fresh telemetry…")
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
                    self.query_one("#connection-status", Static).update(safe_error(error))
                    if control:
                        self.event_log("A failed write may have taken effect. Check fresh status before retrying.")
                    snapshot = getattr(error, "snapshot", None)
                    if isinstance(snapshot, dict):
                        self.render_snapshot(public_snapshot(snapshot))
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
                self.event_log("Connecting to the selected local station…")
                self.launch(backend.connect(self.selected))
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
                        self.query_one("#connection-status", Static).update(safe_error(error))
                finally:
                    self.refreshing = False
            self.run_worker(refresh(), group="refresh", exit_on_error=False)

        def action_refresh(self) -> None:
            self.launch(backend.refresh(force=True))

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
                self.query_one("#connection-status", Static).update("Scanning nearby Bluetooth stations…")
                count = await backend.scan()
                self.refresh_targets()
                message = f"Scan complete: {count} new supported station(s)."
                self.event_log(message)
                self.query_one("#connection-status", Static).update(message)
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


def run_tui(config_path: Path = DEFAULT_CONFIG, lab_directory: Path | None = None) -> None:
    """Open the optional dashboard without starting services or changing settings."""
    create_app(config_path, lab_directory).run()
