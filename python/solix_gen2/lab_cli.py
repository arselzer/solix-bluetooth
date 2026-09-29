"""CLI orchestration for the isolated local Wi-Fi/MQTT endpoint."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import sys

from .client import SolixMonitor
from .config import DEFAULT_CONFIG, load_config
from .isolated_ap import IsolatedAP
from .lab_config import LabConfig, initialize_lab, load_lab, private_write
from .lab_service import lab_request
from .protocol import Model, timezone_confer


def add_commands(subcommands) -> None:
    init = subcommands.add_parser("lab-init", help="Generate private isolated-AP configuration and local MQTT certificates (C2000 Gen 2)")
    init.add_argument("--directory", type=Path, required=True, help="New private directory; existing directories are refused")
    init.add_argument("--name", required=True, help="Existing paired C2000 config name")
    init.add_argument("--serial-file", type=Path, required=True, help="Owner-only file with the 17-character device serial")
    init.add_argument("--account-id-file", type=Path, help="Otherwise use the paired BLE client ID")
    init.add_argument("--interface", required=True, help="Dedicated, unused Linux Wi-Fi interface")
    init.add_argument("--phy", required=True)
    init.add_argument("--country", required=True, help="Wi-Fi regulatory country, for example AT")
    init.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    run = subcommands.add_parser("lab-run", help="Run an isolated WPA2 AP and local API/NTP/native MQTT endpoint (root required)")
    run.add_argument("--directory", type=Path, required=True)
    run.add_argument("--provision", action="store_true", help="Send local Wi-Fi/API settings through the saved BLE pairing")
    run.add_argument("--allow-control", action="store_true", help="Enable explicit native charging-power commands via the private Unix socket")
    run.add_argument("--duration", type=int, help="Stop after this many seconds; default: run until Ctrl-C")
    run.add_argument("--hostapd", default="hostapd", help="Executable name or absolute path")
    run.add_argument("--dnsmasq", default="dnsmasq", help="Executable name or absolute path")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    for command, help_text in (("lab-status", "Query live native MQTT status"),
                               ("lab-readiness", "Read native controller readiness without writing settings"),
                               ("lab-set-charge-power", "Set and confirm C2000 native MQTT charging power")):
        parser = subcommands.add_parser(command, help=help_text)
        parser.add_argument("--directory", type=Path, required=True)
        if command == "lab-set-charge-power":
            parser.add_argument("--watts", type=int, required=True)
    serve = subcommands.add_parser("lab-serve", help="Expose native lab monitoring through the existing read-only HTTP/SSE/metrics API")
    serve.add_argument("--directory", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)


def _device(args, name: str):
    device = next((device for device in load_config(args.config) if device.name == name), None)
    if device is None or device.model != Model.C2000_GEN2 or device.protocol != "prime" or not device.client_id:
        raise ValueError("Configure and pair a C2000 Gen 2 before local MQTT setup")
    return device


def _secret(path: Path) -> str:
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Identifier file must have owner-only permissions")
    return path.read_text().strip()


async def run_lab(args) -> None:
    if args.duration is not None and args.duration <= 0:
        raise ValueError("Duration must be positive")
    directory = args.directory.resolve()
    config = load_lab(directory / "lab.json")
    ap = IsolatedAP(config, directory, hostapd=args.hostapd, dnsmasq=args.dnsmasq)
    monitor = None
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        if args.provision:
            device = _device(args, config.name)
            monitor = SolixMonitor(device.address, model=device.model, owner_user_id=device.client_id,
                                   protocol=device.protocol, timezone_name=config.timezone_name)
            await monitor.connect(timeout=35)
            await monitor.wait_for_update(timeout=15)
            await monitor.request_status()
            baseline = await monitor.wait_for_update(timeout=15)
            if baseline.get("serial_number") != config.device_serial:
                raise ValueError("Bluetooth device serial does not match lab configuration")
            private_write(directory / "provisioning-baseline.json", json.dumps(baseline))
        # Keep startup synchronous so cancellation cannot outlive cleanup in a thread.
        ap.start()
        worker = [sys.executable, "-m", "solix_gen2.lab_worker", "--directory", str(directory)]
        if args.allow_control:
            worker.append("--allow-control")
        (directory / "ready").unlink(missing_ok=True)
        ap.spawn(worker, "service.log")
        async with asyncio.timeout(15):
            while not (directory / "ready").exists():
                ap.check()
                await asyncio.sleep(0.25)
        if monitor:
            replies = await monitor.send_wifi_provisioning(
                ssid=config.ssid, passphrase=config.passphrase, account_id=config.account_id,
                api_url=config.api_url, posix_timezone=timezone_confer(config.timezone_name)[1].decode(),
                iana_timezone=config.timezone_name, allow_http=True,
            )
            private_write(directory / "provisioning-replies.json", json.dumps(replies))
            if replies["4824"] not in ("00", "timeout"):
                raise RuntimeError("Station rejected Wi-Fi credentials")
            await monitor.disconnect()
            monitor = None
        print(json.dumps({"event": "lab_started", "name": config.name, "control_enabled": args.allow_control}), flush=True)
        loop = asyncio.get_running_loop()
        end = loop.time() + args.duration if args.duration else None
        previous = None
        while end is None or loop.time() < end:
            ap.check()
            status = await lab_request(directory, "status")
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
    if args.command == "lab-init":
        device = _device(args, args.name)
        config = LabConfig(name=device.name, interface=args.interface, phy=args.phy, country=args.country,
                           device_serial=_secret(args.serial_file),
                           account_id=_secret(args.account_id_file) if args.account_id_file else device.client_id,
                           timezone_name=device.timezone_name or "Etc/UTC")
        initialize_lab(args.directory, config)
        print(f"Created private local AP and MQTT credentials in {args.directory}")
    elif args.command == "lab-run":
        asyncio.run(run_lab(args))
    elif args.command == "lab-serve":
        from .lab_monitor import LabMonitorService
        from .server import run_server
        run_server(LabMonitorService(load_lab(args.directory / "lab.json"), args.directory), args.host, args.port)
    else:
        command = {"lab-status": "status", "lab-readiness": "readiness", "lab-set-charge-power": "set-charge-power"}[args.command]
        fields = {"watts": args.watts} if args.command == "lab-set-charge-power" else {}
        print(json.dumps(asyncio.run(lab_request(args.directory, command, **fields))))
