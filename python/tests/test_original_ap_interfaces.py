"""Original C1000 AP setup and model gates use private synthetic profiles."""

import argparse
import asyncio
import json
import time

import pytest

from solix_link import ap_service_cli, cli
from solix_link import interactive
from solix_link.ap_service import APService, ap_service_request
from solix_link.ap_service_config import APServiceConfig, initialize_ap_service, private_write
from solix_link.ap_service_monitor import APServiceMonitor
from solix_link.commands import COMMAND_FIELDS, native_commands_for_model
from solix_link.config import DeviceConfig, save_config
from solix_link.protocol import Model
from solix_link.tui import TuiBackend, controls_for


@pytest.fixture
def original_ap(tmp_path):
    config = APServiceConfig("original", "wlan_unused", "phy9", "AT", "A1761SYNTHETIC01",
                             "a" * 40, model=Model.C1000)
    directory = tmp_path / "original-ap"
    initialize_ap_service(directory, config)
    return config, directory


def test_ap_setup_requires_saved_prime_original_pairing(tmp_path):
    path = tmp_path / "config.json"
    args = argparse.Namespace(config=path)
    for protocol, identity in (("legacy", "a" * 40), ("prime", None)):
        save_config([DeviceConfig("original", "AA:BB:CC:DD:EE:01", Model.C1000, identity, protocol)], path)
        with pytest.raises(ValueError, match="paired Prime"):
            ap_service_cli._device(args, "original")
    paired = DeviceConfig("original", "AA:BB:CC:DD:EE:01", Model.C1000, "a" * 40, "prime")
    save_config([paired], path)
    assert ap_service_cli._device(args, "original") == paired


def test_original_native_readonly_monitor_and_terminal(original_ap):
    config, directory = original_ap
    private_write(directory / "status.json", json.dumps({
        "name": config.name, "model": "c1000", "protocol": "native_mqtt", "connected": True,
        "available": True, "control_enabled": False, "last_seen_timestamp": time.time(),
        "power_flow": "grid", "metrics": {"ac_output_enabled": 1, "dc_output_enabled": 0},
    }))
    monitor = APServiceMonitor(config, directory)
    assert monitor.snapshot(config.name)["available"]
    assert monitor.snapshot(config.name)["power_flow"] == "unknown"
    assert monitor.supported_commands(config.name) == []
    expected = {"set-charge-power", "set-device-timeout", "set-display-brightness",
                "set-display-timeout", "set-light", "set-temperature-unit", "set-dc-power-saving", "set-fast-charge"}
    assert set(native_commands_for_model(Model.C1000)) == expected
    async def run():
        calls = []
        async def request(_directory, command, **_fields):
            calls.append(command)
            return monitor.snapshot(config.name)
        backend = TuiBackend([], directory, requester=request)
        assert backend.targets[0].model == Model.C1000
        assert {item.key for item in controls_for(backend.targets[0])} == {
            "charge-power", "device-timeout", "display-brightness", "display-timeout", "light", "temperature-unit", "dc-power-saving", "fast-charge"}
        await backend.connect("native")
        for action in ("charge-cap", "reserve", "plan", "return-grid", "ac-output", "port-memory"):
            with pytest.raises(ValueError, match="unavailable"):
                await backend.control(action, "1")
        with pytest.raises(RuntimeError, match="controls are disabled"):
            await backend.control("charge-power", "900")
        assert calls == ["status"]
        with pytest.raises(PermissionError):
            await monitor.command(config.name, "set-charge-power", watts=900)
    asyncio.run(run())


def test_private_socket_rejects_every_unverified_original_write(original_ap):
    config, directory = original_ap
    async def run():
        service = APService(config, directory, allow_control=True)
        listener = await asyncio.start_unix_server(service._control, path=directory / "control.sock")
        try:
            assert (await ap_service_request(directory, "status"))["model"] == "c1000"
            for command in set(COMMAND_FIELDS) - set(native_commands_for_model(Model.C1000)):
                with pytest.raises(ValueError):
                    await ap_service_request(directory, command)
            with pytest.raises(ValueError):
                await ap_service_request(directory, "readiness")
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


def test_native_original_socket_routes_only_eight_validated_shapes(original_ap):
    config, directory = original_ap
    cases = (("set-charge-power", "set_ac_charging_power", {"watts": 900}),
             ("set-device-timeout", "set_device_timeout", {"minutes": 0}),
             ("set-display-brightness", "set_display_brightness", {"level": 2}),
             ("set-display-timeout", "set_display_timeout", {"seconds": 60}),
             ("set-light", "set_light_mode", {"mode": 1}),
             ("set-temperature-unit", "set_temperature_unit", {"fahrenheit": True}),
             ("set-dc-power-saving", "set_dc_power_saving_enabled", {"enabled": False}),
             ("set-fast-charge", "set_fast_charge_enabled", {"enabled": True}))
    async def run():
        service = APService(config, directory, allow_control=True)
        calls = []
        for command, method, _fields in cases:
            async def apply(value, command=command):
                calls.append((command, value))
                return {"metrics": {"ac_output_enabled": 1}}
            setattr(service.mqtt, method, apply)
        listener = await asyncio.start_unix_server(service._control, path=directory / "control.sock")
        try:
            for command, _method, fields in cases:
                result = await ap_service_request(directory, command, **fields)
                assert result["metrics"]["ac_output_enabled"] == 1
                with pytest.raises(ValueError):
                    await ap_service_request(directory, command, **fields, arbitrary=True)
            assert calls == [(command, next(iter(fields.values()))) for command, _method, fields in cases]
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


def test_terminal_original_native_routes_eight_and_preserves_original_choices(original_ap):
    config, directory = original_ap
    async def run():
        calls = []
        status = {"connected": True, "available": True, "control_enabled": True,
                  "last_seen_timestamp": time.time(), "metrics": {
                      "display_brightness": 2, "display_timeout_seconds": 30, "device_timeout_minutes": 720,
                      "light_mode": 0, "temperature_unit_fahrenheit": 0, "ac_charging_power_limit_w": 1000,
                      "dc_output_enabled": 0, "dc_power_saving_mode_enabled": 1, "ac_fast_charge_enabled": 0}}
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return status
        backend = TuiBackend([], directory, requester=request)
        await backend.connect("native")
        for action, value, command, fields in (
            ("charge-power", "900", "set-charge-power", {"watts": 900}),
            ("display-brightness", "1", "set-display-brightness", {"level": 1}),
            ("device-timeout", "0", "set-device-timeout", {"minutes": 0}),
            ("display-timeout", "60", "set-display-timeout", {"seconds": 60}),
            ("light", "1", "set-light", {"mode": 1}),
            ("temperature-unit", "fahrenheit", "set-temperature-unit", {"fahrenheit": True}),
            ("dc-power-saving", "off", "set-dc-power-saving", {"enabled": False}),
            ("fast-charge", "on", "set-fast-charge", {"enabled": True}),
        ):
            await backend.control(action, value)
            assert calls[-1] == (command, fields)
        for value in ("0", "10"):
            with pytest.raises(ValueError, match="supported setting"):
                await backend.control("display-timeout", value)
        assert len(calls) == 9
    asyncio.run(run())


@pytest.mark.parametrize("selection,value,command,fields", [
    ("1", "1", "set-display-brightness", {"level": 1}),
    ("2", "3", "set-display-timeout", {"seconds": 60}),
    ("3", "2", "set-light", {"mode": 1}),
    ("4", "2", "set-temperature-unit", {"fahrenheit": True}),
    ("5", "2", "set-dc-power-saving", {"enabled": True}),
    ("6", "2", "set-fast-charge", {"enabled": True}),
])
@pytest.mark.parametrize("confirmation", ["0", "1"])
def test_line_original_native_preferences_require_confirmation(original_ap, monkeypatch, selection, value,
                                                                command, fields, confirmation):
    config, directory = original_ap
    calls = []
    async def request(path, command, **fields):
        calls.append((path, command, fields))
        return {"metrics": {}}
    answers = iter((selection, value, confirmation))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(interactive, "ap_service_request", request)
    interactive.preference_menu(config, directory / "devices.json", directory)
    assert calls == ([] if confirmation == "0" else [(directory, command, {"name": config.name, **fields})])


def test_native_light_cli_uses_exact_mode_field(monkeypatch, tmp_path, capsys):
    calls = []
    async def request(directory, command, **fields):
        calls.append((directory, command, fields))
        return {"metrics": {"light_mode": fields["mode"]}}
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    args = cli.parser().parse_args(["ap-service-set-light", "--directory", str(tmp_path),
                                    "--name", "original", "--mode", "low"])
    ap_service_cli.dispatch(args)
    assert calls == [(tmp_path, "set-light", {"name": "original", "mode": 1})]
    assert json.loads(capsys.readouterr().out)["metrics"]["light_mode"] == 1


def test_wifi_setup_accepts_paired_original_prime_and_passes_country(monkeypatch, tmp_path, capsys):
    path = tmp_path / "devices.json"
    identity = "a" * 40
    save_config([DeviceConfig("original", "AA:BB:CC:DD:EE:01", Model.C1000, identity, "prime")], path)
    password = tmp_path / "wifi-password"
    password.write_text("synthetic-wifi-password")
    calls = []
    class Monitor:
        def __init__(self, _address, **kwargs):
            calls.append(kwargs)
        async def connect(self, **_kwargs): pass
        async def disconnect(self): pass
        async def send_wifi_provisioning(self, **kwargs):
            calls.append(kwargs)
            return {"4824": "00", "4825": "00"}
    monkeypatch.setattr(cli, "SolixMonitor", Monitor)
    args = cli.parser().parse_args(["wifi-setup", "--name", "original", "--config", str(path),
                                    "--ssid", "synthetic-ap", "--password-file", str(password),
                                    "--api-url", "http://192.168.77.1/", "--allow-http", "--country-code", "AT"])
    asyncio.run(cli._wifi_setup(args))
    assert calls[0]["protocol"] == "prime"
    assert calls[1]["country_code"] == "AT"
    assert calls[1]["account_id"] == identity
    assert identity not in capsys.readouterr().out
