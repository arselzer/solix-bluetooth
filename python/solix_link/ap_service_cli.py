"""CLI orchestration for the isolated local Wi-Fi/MQTT endpoint."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import sys
from dataclasses import replace

from .client import SolixMonitor
from .config import DEFAULT_CONFIG, load_config
from .isolated_ap import IsolatedAP
from .ap_service_config import APServiceConfig, add_ap_service_device, initialize_ap_service, load_ap_service, load_ap_service_profiles, private_write
from .ap_service import ap_service_request
from .protocol import Model, timezone_confer
from .tou import TouPeriod


def add_commands(subcommands) -> None:
    init = subcommands.add_parser("ap-service-init", help="Generate private isolated-AP configuration and local MQTT certificates (C1000/C2000 Gen 2)")
    init.add_argument("--directory", type=Path, required=True, help="New private directory; existing directories are refused")
    init.add_argument("--name", required=True, help="Existing paired C1000 Gen 2 or C2000 Gen 2 config name")
    init.add_argument("--serial-file", type=Path, required=True, help="Owner-only file with the 17-character device serial")
    init.add_argument("--account-id-file", type=Path, help="Otherwise use the paired BLE client ID")
    init.add_argument("--interface", required=True, help="Dedicated, unused Linux Wi-Fi interface")
    init.add_argument("--phy", required=True)
    init.add_argument("--country", required=True, help="Wi-Fi regulatory country, for example AT")
    init.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    add = subcommands.add_parser("ap-service-add", help="Register another paired Gen 2 station on the same stopped AP")
    add.add_argument("--directory", type=Path, required=True)
    add.add_argument("--name", required=True, help="Existing paired Gen 2 config name")
    add.add_argument("--serial-file", type=Path, required=True)
    add.add_argument("--account-id-file", type=Path)
    add.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    run = subcommands.add_parser("ap-service-run", help="Run an isolated WPA2 AP and local API/NTP/native MQTT endpoint (root required)")
    run.add_argument("--directory", type=Path, required=True)
    run.add_argument("--provision", action="store_true", help="Send local Wi-Fi/API settings through the saved BLE pairing")
    run.add_argument("--name", help="Select which registered station to provision; required for multiple stations")
    run.add_argument("--allow-control", action="store_true", help="Enable native charging, tariff and supported C1000 settings via the private Unix socket")
    run.add_argument("--energy-reports", action="store_true", help="Enable local energy reporting; counter units remain unverified")
    run.add_argument("--duration", type=int, help="Stop after this many seconds; default: run until Ctrl-C")
    run.add_argument("--hostapd", default="hostapd", help="Executable name or absolute path")
    run.add_argument("--dnsmasq", default="dnsmasq", help="Executable name or absolute path")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    for command, help_text in (("ap-service-status", "Query live native MQTT status"),
                               ("ap-service-readiness", "Read native controller readiness without writing settings"),
                               ("ap-service-set-charge-power", "Set and confirm Gen 2 native MQTT charging power"),
                               ("ap-service-set-charge-cap", "Set and confirm the Gen 2 native MQTT upper charge limit"),
                               ("ap-service-set-discharge-floor", "Set C1000 Gen 2 lower discharge limit without adjusting reserve"),
                               ("ap-service-set-temperature-unit", "Set and confirm C1000 Gen 2 temperature units"),
                               ("ap-service-set-off-grid-alert", "Set and confirm C1000 Gen 2 off-grid notification"),
                               ("ap-service-set-reserve", "Set and confirm backup reserve without changing outputs"),
                               ("ap-service-set-tou", "Replace the native hourly schedule; explicit activation persists until changed"),
                               ("ap-service-grid", "Clear the plan and confirm return to grid power without toggling AC output")):
        parser = subcommands.add_parser(command, help=help_text)
        parser.add_argument("--directory", type=Path, required=True)
        parser.add_argument("--name", help="Target station; required for writes when multiple stations share the AP")
        if command == "ap-service-set-charge-power":
            parser.add_argument("--watts", type=int, required=True,
                                help="100 W steps, from 300 W to 1200 W (C1000 Gen 2) or 1800 W (C2000 Gen 2)")
        elif command == "ap-service-set-charge-cap":
            parser.add_argument("--upper", type=int, required=True)
        elif command == "ap-service-set-discharge-floor":
            parser.add_argument("--lower", type=int, choices=[1, 5, 10, 15, 20], required=True)
        elif command == "ap-service-set-reserve":
            parser.add_argument("--reserve", type=int, required=True)
        elif command == "ap-service-set-temperature-unit":
            parser.add_argument("--unit", choices=["celsius", "fahrenheit"], required=True)
        elif command == "ap-service-set-off-grid-alert":
            parser.add_argument("--state", choices=["on", "off"], required=True)
        elif command == "ap-service-set-tou":
            parser.add_argument("--mode", choices=["standard", "time_of_use"], required=True)
            parser.add_argument("--period", action="append", default=[], metavar="TARIFF:START:END",
                                help="Repeat up to six times; peak, mid_peak or off_peak with whole local hours, e.g. peak:0:24")
        elif command == "ap-service-grid":
            parser.add_argument("--timeout", type=int, default=30, help="5–120 seconds per power-flow confirmation phase")
    serve = subcommands.add_parser("ap-service-serve", help="Expose AP-service HTTP/SSE/metrics with optional authenticated commands")
    serve.add_argument("--directory", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--allow-control", action="store_true", help="Enable HTTP commands; requires SOLIX_HTTP_TOKEN and a control-enabled worker")
    serve.add_argument("--web-ui", action="store_true", help="Serve the optional local dashboard at /")


def _device(args, name: str):
    device = next((device for device in load_config(args.config) if device.name == name), None)
    if device is None or device.model not in (Model.C1000_GEN2, Model.C2000_GEN2) or device.protocol != "prime" or not device.client_id:
        raise ValueError("Configure and pair a C1000 Gen 2 or C2000 Gen 2 before local MQTT setup")
    return device


def _secret(path: Path) -> str:
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Identifier file must have owner-only permissions")
    return path.read_text().strip()


async def run_ap_service(args) -> None:
    if args.duration is not None and args.duration <= 0:
        raise ValueError("Duration must be positive")
    directory = args.directory.resolve()
    config = load_ap_service(directory / "ap_service.json")
    profiles = load_ap_service_profiles(directory, config)
    provision_config = config
    if args.provision:
        name = getattr(args, "name", None)
        if name is None and len(profiles) > 1:
            raise ValueError("Select the station to provision with --name")
        if name is not None:
            if name not in profiles:
                raise ValueError("Unknown station name")
            provision_config = profiles[name][0]
    ap = IsolatedAP(config, directory, hostapd=args.hostapd, dnsmasq=args.dnsmasq)
    monitor = None
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        if args.provision:
            device = _device(args, provision_config.name)
            if device.model != provision_config.model:
                raise ValueError("Paired device model does not match AP-service configuration")
            monitor = SolixMonitor(device.address, model=device.model, owner_user_id=device.client_id,
                                   protocol=device.protocol, timezone_name=provision_config.timezone_name)
            await monitor.connect(timeout=35)
            await monitor.wait_for_update(timeout=15)
            await monitor.request_status()
            baseline = await monitor.wait_for_update(timeout=15)
            if baseline.get("serial_number") != provision_config.device_serial:
                raise ValueError("Bluetooth device serial does not match AP-service configuration")
            private_write(directory / "provisioning-baseline.json", json.dumps(baseline))
        # Keep startup synchronous so cancellation cannot outlive cleanup in a thread.
        ap.start()
        worker = [sys.executable, "-m", "solix_link.ap_service_worker", "--directory", str(directory)]
        if args.allow_control:
            worker.append("--allow-control")
        if args.energy_reports:
            worker.append("--energy-reports")
        (directory / "ready").unlink(missing_ok=True)
        ap.spawn(worker, "service.log")
        async with asyncio.timeout(15):
            while not (directory / "ready").exists():
                ap.check()
                await asyncio.sleep(0.25)
        if monitor:
            replies = await monitor.send_wifi_provisioning(
                ssid=config.ssid, passphrase=config.passphrase, account_id=provision_config.account_id,
                api_url=config.api_url, posix_timezone=timezone_confer(provision_config.timezone_name)[1].decode(),
                iana_timezone=provision_config.timezone_name, allow_http=True,
            )
            private_write(directory / "provisioning-replies.json", json.dumps(replies))
            if replies["4824"] not in ("00", "timeout"):
                raise RuntimeError("Station rejected Wi-Fi credentials")
            await monitor.disconnect()
            monitor = None
        print(json.dumps({"event": "ap_service_started", "names": list(profiles), "control_enabled": args.allow_control}), flush=True)
        loop = asyncio.get_running_loop()
        end = loop.time() + args.duration if args.duration else None
        previous = None
        while end is None or loop.time() < end:
            ap.check()
            status = await ap_service_request(directory, "status")
            if status != previous:
                print(json.dumps(status), flush=True)
                previous = status
            await asyncio.sleep(1)
    finally:
        if monitor:
            await monitor.disconnect()
        # Service shutdown precedes returning the adapter; no power commands are sent.
        ap.stop()
        loop.remove_signal_handler(signal.SIGTERM)


def dispatch(args) -> None:
    if args.command == "ap-service-init":
        device = _device(args, args.name)
        config = APServiceConfig(name=device.name, interface=args.interface, phy=args.phy, country=args.country,
                           device_serial=_secret(args.serial_file),
                           account_id=_secret(args.account_id_file) if args.account_id_file else device.client_id,
                           timezone_name=device.timezone_name or "Etc/UTC", model=device.model)
        initialize_ap_service(args.directory, config)
        print(f"Created private local AP and MQTT credentials in {args.directory}")
    elif args.command == "ap-service-run":
        asyncio.run(run_ap_service(args))
    elif args.command == "ap-service-add":
        device = _device(args, args.name)
        parent = load_ap_service(args.directory / "ap_service.json")
        config = replace(parent, name=device.name, model=device.model, device_serial=_secret(args.serial_file),
                         account_id=_secret(args.account_id_file) if args.account_id_file else device.client_id,
                         timezone_name=device.timezone_name or "Etc/UTC")
        add_ap_service_device(args.directory, config)
        print(f"Added {device.name} to the shared AP profile")
    elif args.command == "ap-service-serve":
        from .ap_service_monitor import APServiceMonitor
        from .server import run_server
        run_server(APServiceMonitor(load_ap_service(args.directory / "ap_service.json"), args.directory), args.host, args.port,
                   allow_control=args.allow_control, web_ui=args.web_ui)
    else:
        command = {"ap-service-status": "status", "ap-service-readiness": "readiness", "ap-service-set-charge-power": "set-charge-power",
                   "ap-service-set-charge-cap": "set-charge-cap", "ap-service-set-reserve": "set-backup-reserve",
                   "ap-service-set-discharge-floor": "set-discharge-floor",
                   "ap-service-set-temperature-unit": "set-temperature-unit",
                   "ap-service-set-off-grid-alert": "set-off-grid-alert",
                   "ap-service-set-tou": "set-tou-plan", "ap-service-grid": "return-grid"}[args.command]
        fields = ({"watts": args.watts} if args.command == "ap-service-set-charge-power" else
                  {"upper": args.upper} if args.command == "ap-service-set-charge-cap" else {})
        if args.command == "ap-service-set-reserve":
            fields = {"reserve": args.reserve}
        elif args.command == "ap-service-set-discharge-floor":
            fields = {"lower": args.lower}
        elif args.command == "ap-service-set-temperature-unit":
            fields = {"fahrenheit": args.unit == "fahrenheit"}
        elif args.command == "ap-service-set-off-grid-alert":
            fields = {"enabled": args.state == "on"}
        elif args.command == "ap-service-grid":
            fields = {"timeout": args.timeout}
        elif args.command == "ap-service-set-tou":
            periods = []
            for text in args.period:
                parts = text.split(":")
                if len(parts) != 3:
                    raise ValueError("Period format must be TARIFF:START:END")
                periods.append(TouPeriod(parts[0], int(parts[1]), int(parts[2])).to_dict())
            fields = {"periods": periods, "enabled": args.mode == "time_of_use"}
        if args.name is not None:
            fields["name"] = args.name
        print(json.dumps(asyncio.run(ap_service_request(args.directory, command, **fields))))
