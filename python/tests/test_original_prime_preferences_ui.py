"""Original Prime preference routing uses synthetic stations and no BLE."""

import argparse
import asyncio

import pytest

from solix_link import cli, interactive, protocol
from solix_link.c1000_capabilities import original_prime_commands
from solix_link.config import DeviceConfig, save_config
from solix_link.manager import MonitorService
from solix_link.mqtt_bridge import _supports_operation, decode_setting
from solix_link.protocol import Model
from solix_link.tui import Target, TuiBackend, controls_for


def original():
    return DeviceConfig("original", "AA:BB:CC:DD:EE:04", Model.C1000, "a" * 40, "prime")


def test_shared_verified_set_controls_all_python_interfaces(monkeypatch, tmp_path):
    monkeypatch.setattr(protocol, "C1000_PRIME_SETTINGS", frozenset(("ac_charging_power", "device_timeout", "display_brightness")))
    expected = ["set-charge-power", "set-device-timeout", "set-display-brightness"]
    assert original_prime_commands() == expected
    assert MonitorService([original()]).supported_commands("original") == expected
    target = Target("ble:original", "Original", Model.C1000, device=original())
    assert {item.key for item in controls_for(target)} == {"charge-power", "device-timeout", "display-brightness"}
    for operation in ("display_timeout", "light_mode", "temperature_unit", "ac_output", "fast_charge"):
        assert not _supports_operation(original(), operation)
    path = tmp_path / "config.json"
    save_config([original()], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_a, **_k: pytest.fail("Unverified control cannot connect"))
    with pytest.raises(ValueError, match="not verified"):
        asyncio.run(cli._set(argparse.Namespace(command="set-display-timeout", name="original", seconds=60, config=path)))


@pytest.mark.parametrize("selection,value,command,fields", [
    ("2", "3", "set-display-timeout", {"seconds": 60}),
    ("3", "2", "set-light", {"mode": "low"}),
    ("4", "2", "set-temperature-unit", {"unit": "fahrenheit"}),
])
@pytest.mark.parametrize("confirm", ["1", "0"])
def test_line_preferences_require_confirmation_and_route_exact_fields(monkeypatch, tmp_path, selection, value, command, fields, confirm):
    calls = []
    async def apply(args):
        calls.append(vars(args))
    monkeypatch.setattr(cli, "_set", apply)
    answers = iter([selection, value, confirm])
    monkeypatch.setattr("builtins.input", lambda _label: next(answers))
    interactive.preference_menu(original(), tmp_path / "config.json")
    assert calls == ([] if confirm == "0" else [{"command": command, "name": "original",
                                                  "config": tmp_path / "config.json", **fields}])


class FakeMonitor:
    def __init__(self, _address=None, **kwargs):
        self.connected = False
        self.callback = kwargs.get("on_update", lambda _metrics: None)
        self.metrics = {"display_timeout_seconds": 30, "light_mode": 0, "temperature_unit_fahrenheit": 0,
                        "ac_output_enabled": 1, "dc_output_enabled": 0, "ac_fast_charge_enabled": 0}
        self.calls = []

    async def connect(self, **_kwargs):
        self.connected = True
        self.callback(self.metrics)

    async def disconnect(self):
        self.connected = False

    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()

    def changed(self, key, value):
        self.metrics[key] = value
        self.callback(self.metrics)
        return self.metrics.copy()

    async def set_display_timeout(self, seconds):
        self.calls.append(("display_timeout", seconds))
        return self.changed("display_timeout_seconds", seconds)

    async def set_light_mode(self, mode):
        self.calls.append(("light_mode", mode))
        return self.changed("light_mode", mode)

    async def set_temperature_unit(self, fahrenheit):
        self.calls.append(("temperature_unit", fahrenheit))
        return self.changed("temperature_unit_fahrenheit", int(fahrenheit))

    async def set_fast_charge_enabled(self, enabled):
        self.calls.append(("fast_charge", enabled))
        return self.changed("ac_fast_charge_enabled", int(enabled))


def test_terminal_preferences_route_sdk_and_reject_unknown_ac_countdown():
    async def run():
        backend = TuiBackend([original()], monitor_factory=FakeMonitor)
        await backend.connect("ble:original")
        await backend.control("display-timeout", "60")
        await backend.control("light", "1")
        await backend.control("temperature-unit", "fahrenheit")
        assert backend.monitor.calls == [("display_timeout", 60), ("light_mode", 1), ("temperature_unit", True)]
        await backend.control("fast-charge", "on")
        await backend.control("fast-charge", "off")
        assert backend.monitor.calls[-2:] == [("fast_charge", True), ("fast_charge", False)]
        for action in ("ac-output", "ac-power-saving"):
            with pytest.raises(ValueError, match="inactive AC countdown"):
                await backend.control(action, "on")
        assert len(backend.monitor.calls) == 5
        assert backend.monitor.metrics["ac_output_enabled"] == 1 and backend.monitor.metrics["dc_output_enabled"] == 0
        await backend.disconnect()
    asyncio.run(run())


def test_http_and_bridge_preferences_advertise_and_route_exact_payloads():
    from http_helpers import api_client
    from solix_link.server import create_app

    async def run():
        service = MonitorService([original()])
        monitor = FakeMonitor(on_update=lambda metrics: service._on_update("original", metrics))
        monitor.connected = True
        service._monitors["original"] = monitor
        service._status["original"]["connected"] = True
        service._on_update("original", monitor.metrics)
        async def no_ble():
            pass
        service.start = service.stop = no_ble
        async with api_client(create_app(service, token="synthetic-token", allow_control=True)) as client:
            headers = {"Authorization": "Bearer synthetic-token"}
            response = await client.get("/devices", headers=headers)
            assert response.status_code == 200
            assert response.json()["devices"][0]["controls"] == original_prime_commands()
            for command, fields in (("set-display-timeout", {"seconds": 60}), ("set-light", {"mode": 1}),
                                    ("set-temperature-unit", {"fahrenheit": True})):
                response = await client.post("/devices/original/commands", json={"command": command, **fields}, headers=headers)
                assert response.status_code == 200
            assert monitor.calls == [("display_timeout", 60), ("light_mode", 1), ("temperature_unit", True)]
            response = await client.post("/devices/original/commands", json={"command": "set-fast-charge", "enabled": True}, headers=headers)
            assert response.status_code == 200 and monitor.calls[-1] == ("fast_charge", True)
            response = await client.post("/devices/original/commands", json={"command": "set-fast-charge", "enabled": False}, headers=headers)
            assert response.status_code == 200 and monitor.calls[-1] == ("fast_charge", False)
            response = await client.post("/devices/original/commands", json={"command": "set-fast-charge", "enabled": 1}, headers=headers)
            assert 400 <= response.status_code < 500 and len(monitor.calls) == 5
        assert _supports_operation(original(), "temperature_unit")
        assert decode_setting("temperature_unit", b'{"fahrenheit":true}') == {"fahrenheit": True}
        with pytest.raises(ValueError):
            decode_setting("temperature_unit", b'{"fahrenheit":1}')
    asyncio.run(run())
