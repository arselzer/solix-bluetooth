"""Terminal workflow using the same validated BLE and native MQTT operations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

from .client import SolixMonitor, discover
from .config import DeviceConfig, load_config, save_config
from .ap_service_config import APServiceConfig, initialize_ap_service, load_ap_service, private_write
from .ap_service import ap_service_request
from .protocol import Model


def choose(title: str, options: list[str]) -> int | None:
    """Return a zero-based selection, or None for Back/EOF."""
    print(f"\n{title}")
    for index, label in enumerate(options, 1):
        print(f"  {index}. {label}")
    print("  0. Back / exit")
    while True:
        try:
            value = input("Select: ").strip()
        except EOFError:
            return None
        if value == "0":
            return None
        if value.isdecimal() and 1 <= int(value) <= len(options):
            return int(value) - 1
        print("Enter one of the listed numbers.")


def prompt(label: str, default: str = "") -> str:
    value = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def select_device(config_path: Path) -> DeviceConfig | None:
    """Combine saved devices with a fresh scan; a missing adapter is recoverable."""
    saved = load_config(config_path)
    print("Scanning Bluetooth for supported SOLIX stations…", flush=True)
    try:
        discovered = asyncio.run(discover(timeout=8))
    except Exception as error:
        print(f"Scan unavailable ({type(error).__name__}); saved devices are still selectable.")
        discovered = []
    by_address = {device.address.upper(): device for device in discovered}
    candidates = list(saved)
    for found in discovered:
        if not any(device.address.upper() == found.address.upper() for device in saved):
            candidates.append(DeviceConfig(f"station_{len(candidates) + 1}", found.address, Model.from_name(found.name)))
    if not candidates:
        print("No stations found. Turn on the station and release other Bluetooth connections, then rescan.")
        return None
    choice = choose("Select a station", [
        f"{device.name} — {device.model.value} ({'advertising' if device.address.upper() in by_address else 'saved; not advertising'})"
        for device in candidates
    ])
    if choice is None:
        return None
    device = candidates[choice]
    if device not in saved:
        name = prompt("Save station as", device.name)
        if any(existing.name == name for existing in saved):
            raise ValueError("That name already belongs to another station")
        timezone_name = prompt("Timezone", "Etc/UTC") if device.protocol == "prime" else None
        device = DeviceConfig(name, device.address, device.model, timezone_name=timezone_name)
        save_config([*saved, device], config_path)
    return device


def ensure_paired(device: DeviceConfig, config_path: Path) -> DeviceConfig:
    if device.protocol != "prime" or device.client_id:
        return device
    action = choose("This station needs a local Bluetooth pairing ID", [
        "Pair locally with a main-button confirmation", "Use an existing pairing ID (hidden input)",
    ])
    if action is None:
        raise ValueError("Pairing cancelled")
    if action == 0:
        from .cli import _pair
        asyncio.run(_pair(argparse.Namespace(name=device.name, address=device.address, model=device.model.value,
                                            client_id=None, timezone=device.timezone_name, config=config_path)))
    else:
        import getpass
        updated = DeviceConfig(device.name, device.address, device.model, getpass.getpass("Pairing ID: "),
                               device.protocol, device.timezone_name)
        save_config([updated if saved.name == device.name else saved for saved in load_config(config_path)], config_path)
    return next(saved for saved in load_config(config_path) if saved.name == device.name)


async def read_serial(device: DeviceConfig) -> str:
    async with SolixMonitor(device.address, model=device.model, owner_user_id=device.client_id,
                            protocol=device.protocol, timezone_name=device.timezone_name) as monitor:
        metrics = await monitor.wait_for_update(timeout=15)
        serial = metrics.get("serial_number")
        if not isinstance(serial, str):
            raise ValueError("Station did not report a serial number")
        return serial


def wifi_adapters() -> list[tuple[str, str]]:
    """List dedicated-adapter candidates without changing NetworkManager state."""
    result = []
    for interface in sorted(Path("/sys/class/net").glob("*")):
        phy = interface / "phy80211"
        try:
            if phy.exists() and not int((interface / "flags").read_text(), 16) & 1:
                result.append((interface.name, phy.resolve().name))
        except OSError:
            continue
    return result


def setup_ap_service(device: DeviceConfig, directory: Path) -> None:
    if device.model not in (Model.C1000_GEN2, Model.C2000_GEN2) or device.protocol != "prime" or not device.client_id:
        raise ValueError("AP setup requires a paired C1000 Gen 2 or C2000 Gen 2")
    print("Local MQTT uses a dedicated Wi-Fi adapter, a private API and an AP without an internet route.")
    adapters = wifi_adapters()
    if adapters:
        selected = choose("Select an unused Wi-Fi adapter (currently DOWN)", [f"{interface} / {phy}" for interface, phy in adapters])
        if selected is None:
            return
        interface, phy = adapters[selected]
    else:
        print("No DOWN Wi-Fi adapter was found. Existing active adapters will be refused.")
        interface, phy = prompt("Dedicated Wi-Fi interface"), prompt("Wi-Fi phy")
    country = prompt("Regulatory country (two letters)").upper()
    try:
        serial = asyncio.run(read_serial(device))
    except Exception as error:
        print(f"Could not read the serial over Bluetooth ({type(error).__name__}).")
        import getpass
        serial = getpass.getpass("Device serial (17 characters, hidden): ").strip()
    import getpass
    account = getpass.getpass("App account ID, or Enter for saved BLE pairing ID (hidden): ").strip() or device.client_id
    config = APServiceConfig(device.name, interface, phy, country, serial, account,
                       timezone_name=device.timezone_name or "Etc/UTC", model=device.model)
    initialize_ap_service(directory, config)
    print(f"Saved local AP credentials and certificates in {directory}. Native setup is experimental ({device.model.value}).")


def _show_status(status: dict) -> None:
    metrics = status.get("metrics", {})
    print(f"Connected: {status.get('connected', False)}; fresh data: {status.get('available', False)}")
    print(f"Power flow: {status.get('power_flow', 'unknown')}")
    for key in ("battery_percentage", "battery_status", "ac_output_enabled", "ac_input_power_w", "ac_output_power_w", "ac_charging_power_limit_w",
                "usage_mode", "active_tariff", "backup_reserve_percentage", "tou_schedule_slot_count"):
        if key in metrics:
            print(f"  {key}: {metrics[key]}")


def native_session(directory: Path, config_path: Path, *, provision: bool, allow_control: bool) -> None:
    config = load_ap_service(directory / "ap_service.json")
    maximum_power = 1200 if config.model == Model.C1000_GEN2 else 1800
    command = [sys.executable, "-m", "solix_link", "ap-service-run", "--directory", str(directory.resolve()),
               "--config", str(config_path.resolve())]
    if provision:
        command.append("--provision")
    if allow_control:
        command.append("--allow-control")
    if os.geteuid() != 0:
        print("Starting the isolated AP needs root to move the dedicated adapter into its namespace. Run:")
        print("sudo " + shlex.join(command))
        print("Then reopen interactive mode to inspect the AP-service status.")
        return
    log_path = directory / f"interactive-{time.time_ns()}.log"
    private_write(log_path, b"")
    with log_path.open("ab") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
    try:
        print(f"Starting the AP service; logs: {log_path}. Radio reconnection can take several minutes.")
        while process.poll() is None:
            selected = choose("AP-service session", ["Show live status", "Read controller readiness",
                                                     "Set charging-power limit" if allow_control else "Charging controls disabled",
                                                     "Set upper charge limit" if allow_control else "Charge-cap controls disabled",
                                                     "Set backup reserve" if allow_control else "Reserve controls disabled",
                                                     "Store or activate hourly tariff plan" if allow_control else "Tariff controls disabled",
                                                     "Clear plan and confirm grid power" if allow_control else "Grid-return control disabled",
                                                     "Stop this AP session"])
            if selected is None or selected == 7:
                break
            try:
                if selected == 0:
                    _show_status(asyncio.run(ap_service_request(directory, "status")))
                elif selected == 1:
                    print(json.dumps(asyncio.run(ap_service_request(directory, "readiness"))))
                elif selected == 2 and allow_control:
                    watts = int(prompt(f"Charging-power limit (300–{maximum_power} W in 100 W steps)"))
                    _show_status(asyncio.run(ap_service_request(directory, "set-charge-power", watts=watts)))
                elif selected == 3 and allow_control:
                    upper = int(prompt("Upper charge limit (80–100% in 5% steps)"))
                    _show_status(asyncio.run(ap_service_request(directory, "set-charge-cap", upper=upper)))
                elif selected == 4 and allow_control:
                    reserve = int(prompt("Backup reserve (5–100% in 5% steps, within charge limits)"))
                    _show_status(asyncio.run(ap_service_request(directory, "set-backup-reserve", reserve=reserve)))
                elif selected == 5 and allow_control:
                    from .tou import TouPeriod
                    mode = choose("Plan mode", ["Store in Standard", "Activate Time-of-Use (persists until changed)"])
                    if mode is None:
                        continue
                    text = prompt("Periods separated by commas, e.g. peak:0:24; empty clears the plan")
                    periods = []
                    for value in text.split(",") if text else []:
                        parts = value.strip().split(":")
                        if len(parts) != 3:
                            raise ValueError("Period format must be TARIFF:START:END")
                        periods.append(TouPeriod(parts[0], int(parts[1]), int(parts[2])).to_dict())
                    _show_status(asyncio.run(ap_service_request(directory, "set-tou-plan", periods=periods, enabled=mode == 1)))
                elif selected == 6 and allow_control:
                    _show_status(asyncio.run(ap_service_request(directory, "return-grid")))
            except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                print(f"{type(error).__name__}: {error}. Check fresh status before retrying a control write.")
        if process.poll() is not None and process.returncode:
            print(f"AP session exited; inspect {log_path}.")
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"AP shutdown is still running; inspect private log {log_path}") from None


def mqtt_menu(device: DeviceConfig, config_path: Path, directory: Path) -> None:
    while True:
        choices = ["Publish BLE telemetry to my MQTT broker"]
        if device.model in (Model.C1000_GEN2, Model.C2000_GEN2):
            choices += ["Create AP-service configuration", "Start saved AP service",
                        "Provision/reconnect to AP service", "Inspect running AP-service status",
                        "Serve native status over HTTP"]
        action = choose("MQTT connection", choices)
        if action is None:
            return
        try:
            if action == 0:
                from .manager import MonitorService
                from .mqtt_bridge import MqttBridge
                host = prompt("Your MQTT broker", "127.0.0.1")
                ca = prompt("TLS CA file (optional)")
                port = int(prompt("Port", "8883" if ca else "1883"))
                username = prompt("MQTT username (optional)")
                password = prompt("MQTT password file (optional)") if username else ""
                print("Publishing BLE telemetry; Ctrl-C stops the bridge.")
                asyncio.run(MqttBridge(MonitorService([device]), host=host, port=port,
                                       username=username or None, password_file=Path(password) if password else None,
                                       ca_file=Path(ca) if ca else None).run())
            elif action == 1:
                setup_ap_service(device, directory)
            elif action in (2, 3):
                config = load_ap_service(directory / "ap_service.json")
                if config.name != device.name or config.model != device.model:
                    raise ValueError("AP service belongs to a different selected station")
                controls = choose("Native control", ["Monitoring only", "Enable explicit charging and tariff commands"])
                if controls is not None:
                    native_session(directory, config_path, provision=action == 3, allow_control=controls == 1)
            elif action == 4:
                _show_status(asyncio.run(ap_service_request(directory, "status")))
            elif action == 5:
                from .ap_service_monitor import APServiceMonitor
                from .server import run_server
                host = prompt("HTTP listen address", "127.0.0.1")
                port = int(prompt("HTTP port", "8765"))
                run_server(APServiceMonitor(load_ap_service(directory / "ap_service.json"), directory), host, port)
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as error:
            print(f"{type(error).__name__}: {error}")


def run_interactive(config_path: Path, ap_service_directory: Path | None = None) -> None:
    print("SOLIX local monitoring — Ctrl-C stops an active monitor; 0 returns to the menu.")
    selected = select_device(config_path)
    while True:
        action = choose(f"Station: {selected.name if selected else 'none selected'}", [
            "Select / rescan a station", "Monitor over Bluetooth", "Connect MQTT / isolated Wi-Fi",
            "Serve Bluetooth status over HTTP",
        ])
        if action is None:
            return
        try:
            if action == 0:
                selected = select_device(config_path)
                continue
            if selected is None:
                print("Select a station first.")
                continue
            selected = ensure_paired(selected, config_path)
            if action == 1:
                from .cli import _monitor
                print("Monitoring Bluetooth; Ctrl-C returns to the menu.")
                asyncio.run(_monitor(argparse.Namespace(name=selected.name, config=config_path)))
            elif action == 2:
                directory = ap_service_directory or config_path.parent / "ap-services" / selected.name
                mqtt_menu(selected, config_path, directory)
            elif action == 3:
                from .manager import MonitorService
                from .server import run_server
                host = prompt("HTTP listen address", "127.0.0.1")
                port = int(prompt("HTTP port", "8765"))
                run_server(MonitorService([selected]), host, port)
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as error:
            print(f"{type(error).__name__}: {error}")
