"""Command-line discovery, pairing, monitoring, and HTTP serving."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from pathlib import Path
import sys

from .client import SolixMonitor, discover
from .config import DEFAULT_CONFIG, DeviceConfig, load_config, save_config
from .manager import MonitorService
from .protocol import Model


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="solix-gen2", description="Local SOLIX Gen 2 BLE monitoring")
    subcommands = command.add_subparsers(dest="command", required=True)

    scan = subcommands.add_parser("scan", help="Find nearby C1000/C2000 Gen 2 devices")
    scan.add_argument("--timeout", type=float, default=8)

    add = subcommands.add_parser("add", help="Save a known device in the local config")
    add.add_argument("--name", required=True)
    add.add_argument("--address", required=True)
    add.add_argument("--model", choices=[model.value for model in Model], required=True)
    add.add_argument("--client-id", help="Previously paired 40-character Prime client ID")
    add.add_argument("--protocol", choices=["prime", "legacy"], default="prime")
    add.add_argument("--timezone", help="Station timezone, for example Europe/Vienna")
    add.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    pair = subcommands.add_parser("pair", help="Pair a Prime Gen 2 station with one main button press")
    pair.add_argument("--name", required=True)
    pair.add_argument("--address", required=True)
    pair.add_argument("--model", choices=[model.value for model in Model], default=Model.C2000_GEN2.value)
    pair.add_argument("--client-id", help="Use an existing 40-character ID")
    pair.add_argument("--timezone", help="Station timezone, for example Europe/Vienna")
    pair.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    monitor = subcommands.add_parser("monitor", help="Print newline-delimited JSON status updates")
    monitor.add_argument("--name", help="Monitor only this configured device")
    monitor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    serve = subcommands.add_parser("serve", help="Run the read-only HTTP monitoring server")
    serve.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    mqtt = subcommands.add_parser("mqtt-bridge", help="Publish BLE status and verified Gen 2 settings through a local MQTT broker")
    mqtt.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mqtt.add_argument("--broker", default="127.0.0.1")
    mqtt.add_argument("--port", type=int, default=1883)
    mqtt.add_argument("--topic-prefix", default="solix_gen2")
    mqtt.add_argument("--username")
    mqtt.add_argument("--password-file", type=Path)
    mqtt.add_argument("--ca-file", type=Path, help="Enable TLS with this trusted CA file")

    limits = subcommands.add_parser("set-limits", help="Set C1000 Prime charge/discharge limits")
    limits.add_argument("--name", required=True)
    limits.add_argument("--upper", type=int, required=True, help="Charging upper limit, 80–100 percent in 5 percent steps")
    limits.add_argument("--lower", type=int, required=True, help="Discharging lower limit: 1, 5, 10, 15, or 20 percent")
    limits.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    cap = subcommands.add_parser("set-charge-cap", help="Set the C2000 Gen 2 upper charge limit")
    cap.add_argument("--name", required=True)
    cap.add_argument("--upper", type=int, required=True, help="80–100 percent in 5 percent steps")
    cap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    power = subcommands.add_parser("set-charge-power", help="Set C1000/C2000 Gen 2 Prime AC charging power")
    power.add_argument("--name", required=True)
    power.add_argument("--watts", type=int, required=True, help="300–1200 W (C1000) or 300–1800 W (C2000), in 100 W steps")
    power.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    display = subcommands.add_parser("set-display-timeout", help="Set C1000/C2000 Gen 2 display timeout")
    display.add_argument("--name", required=True)
    display.add_argument("--seconds", type=int, required=True)
    display.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    fast = subcommands.add_parser("set-fast-charge", help="Set C1000 fast charge switch")
    fast.add_argument("--name", required=True)
    fast.add_argument("--enabled", choices=["on", "off"], required=True)
    fast.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    wifi = subcommands.add_parser("wifi-setup", help="Experimentally provision C1000 Wi-Fi over Bluetooth")
    wifi.add_argument("--name", required=True)
    wifi.add_argument("--ssid", required=True)
    wifi.add_argument("--password-file", type=Path, help="Read Wi-Fi passphrase from a local file; otherwise prompt")
    wifi.add_argument("--api-url", required=True, help="API base URL supplied to the station")
    wifi.add_argument("--allow-http", action="store_true", help="Allow a local HTTP API URL for isolated lab use")
    wifi.add_argument("--account-id", help="40-character account ID; defaults to the paired local client ID")
    wifi.add_argument("--posix-timezone", default="UTC0")
    wifi.add_argument("--iana-timezone", default="Etc/UTC")
    wifi.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    join = subcommands.add_parser("wifi-join", help="Join a WPA2 AP from C1000/C2000 Bluetooth without cloud setup")
    join.add_argument("--name", required=True)
    join.add_argument("--ssid", required=True)
    join.add_argument("--password-file", type=Path, help="Read Wi-Fi passphrase from a local file; otherwise prompt")
    join.add_argument("--account-id", help="40-character account ID; defaults to the paired local client ID")
    join.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return command


def _upsert(device: DeviceConfig, path: Path) -> None:
    current = load_config(path)
    current = [saved for saved in current if saved.name != device.name]
    current.append(device)
    save_config(current, path)


async def _scan(timeout: float) -> None:
    for device in await discover(timeout=timeout):
        print(json.dumps({"name": device.name, "address": device.address}))


async def _pair(args: argparse.Namespace) -> None:
    existing = next((device for device in load_config(args.config) if device.name == args.name), None)
    client_id = args.client_id or (existing.client_id if existing else None)
    model = Model(args.model)
    timezone_name = args.timezone or (existing.timezone_name if existing else None)
    monitor = SolixMonitor(args.address, model=model, owner_user_id=client_id,
                           protocol="prime", timezone_name=timezone_name)
    connecting = asyncio.create_task(monitor.connect(timeout=120))
    pairing = asyncio.create_task(monitor.pairing_required.wait())
    try:
        done, _pending = await asyncio.wait({connecting, pairing}, return_when=asyncio.FIRST_COMPLETED)
        if pairing in done:
            print("Station requests physical pairing confirmation.", flush=True)
            await asyncio.to_thread(input, "Press its MAIN power button once, then press Enter here: ")
            await monitor.confirm_pairing()
        await connecting
        try:
            update = await monitor.wait_for_update(timeout=15)
            print(f"Connected; battery {update.get('battery_percentage', '?')}%, AC output {update.get('ac_output_enabled', '?')}")
        except TimeoutError:
            print("Registration succeeded; telemetry has not arrived yet")
        _upsert(DeviceConfig(args.name, args.address, model, monitor.owner_user_id,
                             "prime", timezone_name), args.config)
        print(f"Saved client ID to {args.config} (owner-only file permissions)")
    finally:
        pairing.cancel()
        if not connecting.done():
            connecting.cancel()
        await asyncio.gather(connecting, pairing, return_exceptions=True)
        await monitor.disconnect()


async def _monitor(args: argparse.Namespace) -> None:
    devices = load_config(args.config)
    if args.name:
        devices = [device for device in devices if device.name == args.name]
    service = MonitorService(devices)
    queue = service.subscribe()
    await service.start()
    try:
        for status in service.snapshots():
            print(json.dumps(status), flush=True)
        while True:
            print(json.dumps(await queue.get()), flush=True)
    finally:
        service.unsubscribe(queue)
        await service.stop()


async def _set(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None:
        raise ValueError(f"Unknown configured device: {args.name}")
    if device.protocol != "prime" or (
        device.model != Model.C1000_GEN2
        and not (device.model == Model.C2000_GEN2 and args.command in ("set-display-timeout", "set-charge-power", "set-charge-cap"))
    ):
        raise ValueError("This setting is not verified for the selected device")
    if args.command == "set-charge-cap" and device.model != Model.C2000_GEN2:
        raise ValueError("set-charge-cap is verified only on C2000 Gen 2 Prime")
    monitor = SolixMonitor(
        device.address, model=device.model, owner_user_id=device.client_id, protocol=device.protocol,
        timezone_name=device.timezone_name,
    )
    try:
        await monitor.connect(timeout=30)
        if args.command == "set-limits":
            metrics = await monitor.set_charge_limits(args.upper, args.lower)
            result = {"max_charge_percentage": metrics["max_charge_percentage"],
                      "min_charge_percentage": metrics["min_charge_percentage"]}
        elif args.command == "set-charge-cap":
            metrics = await monitor.set_charge_cap(args.upper)
            result = {"max_charge_percentage": metrics["max_charge_percentage"],
                      "min_charge_percentage": metrics["min_charge_percentage"]}
        elif args.command == "set-charge-power":
            metrics = await monitor.set_ac_charging_power(args.watts)
            result = {"ac_charging_power_limit_w": metrics["ac_charging_power_limit_w"]}
        elif args.command == "set-display-timeout":
            metrics = await monitor.set_display_timeout(args.seconds)
            result = {"display_timeout_seconds": metrics["display_timeout_seconds"]}
        else:
            metrics = await monitor.set_fast_charge_enabled(args.enabled == 'on')
            result = {"ac_fast_charge_enabled": metrics["ac_fast_charge_enabled"]}
        print(json.dumps({"name": device.name, "confirmed": result}))
    finally:
        await monitor.disconnect()


async def _wifi_setup(args: argparse.Namespace) -> None:
    device = next((saved for saved in load_config(args.config) if saved.name == args.name), None)
    if device is None:
        raise ValueError(f"Unknown configured device: {args.name}")
    if device.protocol != "prime" or (args.command == 'wifi-setup' and device.model != Model.C1000_GEN2):
        raise ValueError("Wi-Fi join requires Gen 2 Prime; cloud setup is tested only on C1000 Gen 2")
    account_id = args.account_id or device.client_id
    if account_id is None:
        raise ValueError("A paired client ID or --account-id is required")
    passphrase = (args.password_file.read_text().rstrip('\r\n') if args.password_file
                  else getpass.getpass('Wi-Fi passphrase: '))
    monitor = SolixMonitor(
        device.address, model=device.model, owner_user_id=device.client_id, protocol=device.protocol,
        timezone_name=device.timezone_name,
    )
    try:
        await monitor.connect(timeout=35)
        if args.command == 'wifi-join':
            replies = {'4824': await monitor.join_wifi(
                ssid=args.ssid, passphrase=passphrase, account_id=account_id,
            )}
        else:
            replies = await monitor.send_wifi_provisioning(
                ssid=args.ssid, passphrase=passphrase, account_id=account_id,
                api_url=args.api_url, posix_timezone=args.posix_timezone,
                iana_timezone=args.iana_timezone, allow_http=args.allow_http,
            )
        print(json.dumps({"name": device.name, "ble_replies": replies}))
    finally:
        await monitor.disconnect()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "scan":
            asyncio.run(_scan(args.timeout))
        elif args.command == "add":
            device = DeviceConfig(args.name, args.address, Model(args.model), args.client_id,
                                  args.protocol, args.timezone)
            _upsert(device, args.config)
            print(f"Saved {device.name} to {args.config}")
        elif args.command == "pair":
            asyncio.run(_pair(args))
        elif args.command == "monitor":
            asyncio.run(_monitor(args))
        elif args.command == "serve":
            from .server import run_server
            run_server(MonitorService(load_config(args.config)), host=args.host, port=args.port)
        elif args.command == "mqtt-bridge":
            from .mqtt_bridge import MqttBridge
            asyncio.run(MqttBridge(
                MonitorService(load_config(args.config)), host=args.broker, port=args.port,
                topic_prefix=args.topic_prefix, username=args.username,
                password_file=args.password_file, ca_file=args.ca_file,
            ).run())
        elif args.command in ("set-limits", "set-charge-cap", "set-charge-power", "set-display-timeout", "set-fast-charge"):
            asyncio.run(_set(args))
        elif args.command in ("wifi-setup", "wifi-join"):
            asyncio.run(_wifi_setup(args))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
