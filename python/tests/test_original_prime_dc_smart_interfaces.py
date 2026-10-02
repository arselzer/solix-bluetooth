"""DC Smart interfaces use fake BLE and keep native MQTT restricted."""

import argparse
import asyncio
import time

import pytest

from solix_link import cli, interactive
from solix_link.c1000_capabilities import original_prime_commands
from solix_link.commands import native_commands_for_model
from solix_link.config import DeviceConfig, save_config
from solix_link.manager import MonitorService
from solix_link.mqtt_bridge import _supports_operation, decode_setting
from solix_link.protocol import Model
from solix_link.tui import TuiBackend, controls_for, create_app


def original():
    return DeviceConfig("original", "AA:BB:CC:DD:EE:04", Model.C1000, "a" * 40, "prime")


class Monitor:
    def __init__(self, *_args, **kwargs):
        self.connected = False
        self.callback = kwargs.get("on_update", lambda _metrics: None)
        self.metrics = {"ac_output_enabled": 1, "dc_output_enabled": 0, "dc_power_saving_mode_enabled": 1}
        self.calls = []
    async def connect(self, **_kwargs):
        self.connected = True
        self.callback(self.metrics)
    async def disconnect(self):
        self.connected = False
    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()
    async def set_dc_power_saving_enabled(self, enabled):
        self.calls.append(enabled)
        self.metrics["dc_power_saving_mode_enabled"] = int(enabled)
        self.callback(self.metrics)
        return self.metrics.copy()


def test_dc_smart_capabilities_include_gen2_native_and_exclude_c2000():
    assert len(original_prime_commands()) == 9
    assert "set-dc-power-saving" in MonitorService([original()]).supported_commands("original")
    assert _supports_operation(original(), "dc_power_saving")
    assert decode_setting("dc_power_saving", b'{"enabled":true}') == {"enabled": True}
    assert len(native_commands_for_model(Model.C1000)) == 9
    assert "set-dc-power-saving" in native_commands_for_model(Model.C1000)
    assert "set-dc-power-saving" in native_commands_for_model(Model.C1000_GEN2)
    assert "set-dc-power-saving" not in native_commands_for_model(Model.C2000_GEN2)
    assert "set-ac-power-saving" in original_prime_commands()


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_and_gateway_delegate_boolean_dc_setting(monkeypatch, tmp_path, capsys, enabled):
    path = tmp_path / "devices.json"
    save_config([original()], path)
    monitor = Monitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    asyncio.run(cli._set(argparse.Namespace(command="set-dc-power-saving", name="original", config=path,
                                             enabled="on" if enabled else "off")))
    assert monitor.calls == [enabled] and not monitor.connected
    error = capsys.readouterr().err
    assert "DC output OFF" in error and "inactivity counter" in error
    service = MonitorService([original()])
    monitor.connected = True
    service._monitors["original"] = monitor
    service._status["original"]["connected"] = True
    service._on_update("original", monitor.metrics)
    asyncio.run(service.command("original", "set-dc-power-saving", enabled=enabled))
    assert monitor.calls == [enabled, enabled]


@pytest.mark.parametrize("dc_output", [1, None, True, 0.0, "0"])
def test_tui_refuses_active_or_unknown_dc_before_delegation(dc_output):
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        backend.monitor.metrics["dc_output_enabled"] = dc_output
        with pytest.raises(ValueError, match="DC output OFF"):
            await backend.control("dc-power-saving", "on")
        assert backend.monitor.calls == []
    asyncio.run(run())


def test_tui_requires_fresh_dc_off_and_routes_both_directions():
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        assert "dc-power-saving" in {control.key for control in controls_for(backend.target)}
        await backend.control("dc-power-saving", "off")
        await backend.control("dc-power-saving", "on")
        assert backend.monitor.calls == [False, True]
        backend.last_seen = time.time() - 91
        with pytest.raises(ValueError, match="Fresh"):
            await backend.control("dc-power-saving", "off")
        assert backend.monitor.calls == [False, True]
    asyncio.run(run())


@pytest.mark.parametrize("confirmation", ["0", "1"])
def test_line_dc_smart_requires_confirmation_and_warns(monkeypatch, tmp_path, capsys, confirmation):
    calls = []
    async def apply(args):
        calls.append(vars(args))
    answers = iter(("5", "2", confirmation))
    monkeypatch.setattr("builtins.input", lambda _label: next(answers))
    monkeypatch.setattr(cli, "_set", apply)
    interactive.preference_menu(original(), tmp_path / "devices.json")
    assert calls == ([] if confirmation == "0" else [{"command": "set-dc-power-saving", "name": "original",
                                                      "config": tmp_path / "devices.json", "enabled": "on"}])
    output = capsys.readouterr().out
    assert "DC output OFF" in output and "inactivity counter" in output


def test_textual_dc_smart_cancel_confirm_and_dc_on_disable():
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(120, 42)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            await pilot.press("f2")
            app.query_one("#setting", Select).value = "dc-power-saving"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "off"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert "inactivity counter" in app.screen.detail
            await pilot.click("#saving-cancel")
            assert backend.monitor.calls == []
            await pilot.click("#apply-setting")
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert backend.monitor.calls == [False]
            backend.monitor.metrics["dc_output_enabled"] = 1
            app.render_snapshot(backend._ble_snapshot())
            assert app.query_one("#apply-setting").disabled
    asyncio.run(run())
