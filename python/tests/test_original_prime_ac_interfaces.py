"""Prime AC interfaces use fake transport; output switching stays local."""

import argparse
import asyncio
import time

import pytest

from solix_link import cli, interactive
from solix_link.c1000_capabilities import original_prime_commands
from solix_link.commands import native_commands_for_model
from solix_link.config import DeviceConfig, save_config
from solix_link.manager import MonitorService
from solix_link.mqtt_bridge import MqttBridge, _supports_operation, decode_setting
from solix_link.protocol import Model
from solix_link.tui import TuiBackend, controls_for, create_app


def original():
    return DeviceConfig("original", "AA:BB:CC:DD:EE:04", Model.C1000, "a" * 40, "prime")


class Monitor:
    def __init__(self, *_args, **kwargs):
        self.connected = False
        self.callback = kwargs.get("on_update", lambda _metrics: None)
        self.metrics = {"ac_output_enabled": 0, "dc_output_enabled": 0,
                        "ac_output_timer_remaining_seconds": 0, "ac_power_saving_mode_enabled": 1}
        self.calls = []

    async def connect(self, **_kwargs):
        self.connected = True
        self.callback(self.metrics)

    async def disconnect(self):
        self.connected = False

    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()

    async def set_ac_power_saving_enabled(self, enabled):
        self.calls.append(("smart", enabled))
        self.metrics["ac_power_saving_mode_enabled"] = int(enabled)
        self.callback(self.metrics)
        return self.metrics.copy()

    async def set_ac_output_enabled(self, enabled):
        self.calls.append(("output", enabled))
        self.metrics["ac_output_enabled"] = int(enabled)
        self.callback(self.metrics)
        return self.metrics.copy()


def test_nine_gateway_preferences_and_ten_direct_terminal_controls():
    assert len(original_prime_commands()) == 9
    assert "set-ac-output" not in MonitorService([original()]).supported_commands("original")
    assert _supports_operation(original(), "ac_power_saving")
    assert not _supports_operation(original(), "ac_output")
    assert decode_setting("ac_power_saving", b'{"enabled":true}') == {"enabled": True}
    assert len(native_commands_for_model(Model.C1000)) == 9
    assert "set-ac-power-saving" in native_commands_for_model(Model.C1000)
    assert "set-ac-output" not in native_commands_for_model(Model.C1000)
    backend = TuiBackend([original()])
    assert len(controls_for(backend.targets[0])) == 10


@pytest.mark.parametrize("command,method", [("set-ac-power-saving", "smart"), ("set-ac-output", "output")])
@pytest.mark.parametrize("enabled", [False, True])
def test_direct_cli_routes_explicit_on_off_without_extra_flag(monkeypatch, tmp_path, capsys, command, method, enabled):
    path = tmp_path / "devices.json"
    save_config([original()], path)
    monitor = Monitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    args = cli.parser().parse_args([command, "--config", str(path), "--name", "original",
                                    "--enabled", "on" if enabled else "off"])
    asyncio.run(cli._set(args))
    assert monitor.calls == [(method, enabled)] and not monitor.connected
    if method == "smart":
        warning = capsys.readouterr().err
        assert "AC output OFF" in warning and "inactive AC timer" in warning


def test_gateway_routes_ac_smart_but_rejects_ac_output_command():
    service = MonitorService([original()])
    monitor = Monitor()
    monitor.connected = True
    service._monitors["original"] = monitor
    service._status["original"]["connected"] = True
    service._on_update("original", monitor.metrics)
    asyncio.run(service.command("original", "set-ac-power-saving", enabled=False))
    assert monitor.calls == [("smart", False)]
    with pytest.raises(ValueError):
        asyncio.run(service.command("original", "set-ac-output", enabled=False))
    asyncio.run(MqttBridge(service)._handle_command("solix_gen2/original/set/ac_output", b'{"enabled":false}', False))
    assert monitor.calls == [("smart", False)]


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_gen2_cli_never_opens_transport_for_ac_output(monkeypatch, tmp_path, model):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("station", "AA:BB:CC:DD:EE:05", model, "a" * 40, "prime")], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: pytest.fail("Blocked model must not connect"))
    with pytest.raises(ValueError):
        asyncio.run(cli._set(argparse.Namespace(command="set-ac-output", name="station", enabled="off", config=path)))


@pytest.mark.parametrize("action", ["ac-output", "ac-power-saving"])
@pytest.mark.parametrize("countdown", [None, True, 0.0, "0", 1, 3600])
def test_terminal_rejects_active_or_unknown_countdown_before_dispatch(action, countdown):
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        backend.monitor.metrics["ac_output_timer_remaining_seconds"] = countdown
        with pytest.raises(ValueError, match="inactive AC countdown"):
            await backend.control(action, "off")
        assert backend.monitor.calls == []
    asyncio.run(run())


@pytest.mark.parametrize("state", [1, None, True, 0.0, "0"])
@pytest.mark.parametrize("enabled", ["on", "off"])
def test_terminal_ac_smart_requires_ac_off_both_directions(state, enabled):
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        backend.monitor.metrics["ac_output_enabled"] = state
        with pytest.raises(ValueError):
            await backend.control("ac-power-saving", enabled)
        assert backend.monitor.calls == []
    asyncio.run(run())


@pytest.mark.parametrize("action", ["ac-output", "ac-power-saving"])
def test_terminal_requires_fresh_status_and_routes_restored_ac_sequence(action):
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        await backend.control(action, "on")
        await backend.control(action, "off")
        assert backend.monitor.calls == [("output" if action == "ac-output" else "smart", True),
                                         ("output" if action == "ac-output" else "smart", False)]
        backend.last_seen = time.time() - 91
        with pytest.raises(ValueError, match="Fresh"):
            await backend.control(action, "off")
        assert len(backend.monitor.calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize("selection,command", [("7", "set-ac-power-saving"), ("8", "set-ac-output")])
@pytest.mark.parametrize("confirmation", ["0", "1"])
def test_line_ac_preference_or_output_requires_confirmation(monkeypatch, tmp_path, capsys, selection, command, confirmation):
    calls = []
    async def apply(args):
        calls.append(vars(args))
    answers = iter((selection, "1", confirmation))
    monkeypatch.setattr("builtins.input", lambda _label: next(answers))
    monkeypatch.setattr(cli, "_set", apply)
    interactive.preference_menu(original(), tmp_path / "devices.json")
    assert calls == ([] if confirmation == "0" else [{"command": command, "name": "original",
                                                      "config": tmp_path / "devices.json", "enabled": "off"}])
    warning = capsys.readouterr().out
    assert "inactive AC" in warning


def test_textual_output_cancel_confirm_and_smart_guard():
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            await pilot.press("f2")
            app.query_one("#setting", Select).value = "ac-output"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "on"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert "power at the AC sockets" in app.screen.detail
            await pilot.click("#saving-cancel")
            assert backend.monitor.calls == []
            await pilot.click("#apply-setting")
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert backend.monitor.calls == [("output", True)]
            app.query_one("#setting", Select).value = "ac-power-saving"
            await pilot.pause()
            assert app.query_one("#apply-setting").disabled
            backend.monitor.metrics["ac_output_enabled"] = 0
            app.render_snapshot(backend._ble_snapshot())
            assert not app.query_one("#apply-setting").disabled
            app.query_one("#setting-value", Input).value = "off"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert "inactive AC timer" in app.screen.detail and "inactivity counter" in app.screen.detail
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert backend.monitor.calls[-1] == ("smart", False)
    asyncio.run(run())
