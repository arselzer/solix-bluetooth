"""Versioned Fast flag routes use synthetic transport, never station commands."""

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
        self.metrics = {"ac_output_enabled": 1, "dc_output_enabled": 0, "ac_fast_charge_enabled": 0}
        self.calls = []

    async def connect(self, **_kwargs):
        self.connected = True
        self.callback(self.metrics)

    async def disconnect(self):
        self.connected = False

    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()

    async def set_fast_charge_enabled(self, enabled):
        self.calls.append(enabled)
        self.metrics["ac_fast_charge_enabled"] = int(enabled)
        self.callback(self.metrics)
        return self.metrics.copy()


def test_independently_verified_prime_and_native_eighth_capability():
    assert len(original_prime_commands()) == 9
    assert "set-fast-charge" in MonitorService([original()]).supported_commands("original")
    assert _supports_operation(original(), "fast_charge")
    assert decode_setting("fast_charge", b'{"enabled":true}') == {"enabled": True}
    assert len(native_commands_for_model(Model.C1000)) == 8
    assert "set-fast-charge" in native_commands_for_model(Model.C1000)


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_and_gateway_use_existing_fast_boolean_route(monkeypatch, tmp_path, capsys, enabled):
    path = tmp_path / "devices.json"
    save_config([original()], path)
    monitor = Monitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    asyncio.run(cli._set(argparse.Namespace(command="set-fast-charge", name="original", config=path,
                                            enabled="on" if enabled else "off")))
    assert monitor.calls == [enabled] and not monitor.connected
    warning = capsys.readouterr().err
    assert "adequate AC supply" in warning and "reboot persistence" in warning
    service = MonitorService([original()])
    monitor.connected = True
    service._monitors["original"] = monitor
    service._status["original"]["connected"] = True
    service._on_update("original", monitor.metrics)
    asyncio.run(service.command("original", "set-fast-charge", enabled=enabled))
    assert monitor.calls == [enabled, enabled]


@pytest.mark.parametrize("bad", [None, True, 2, 0.0, "0"])
def test_tui_rejects_unknown_fast_readback_before_dispatch(bad):
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        backend.monitor.metrics["ac_fast_charge_enabled"] = bad
        with pytest.raises(ValueError, match="fast-charge telemetry"):
            await backend.control("fast-charge", "on")
        assert backend.monitor.calls == []
    asyncio.run(run())


def test_tui_requires_fresh_flag_and_does_not_invent_original_mains():
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        await backend.connect("ble:original")
        assert "fast-charge" in {control.key for control in controls_for(backend.target)}
        assert "ac_input_connected" not in backend.monitor.metrics
        await backend.control("fast-charge", "on")
        await backend.control("fast-charge", "off")
        assert backend.monitor.calls == [True, False]
        backend.last_seen = time.time() - 91
        with pytest.raises(ValueError, match="Fresh"):
            await backend.control("fast-charge", "on")
        assert backend.monitor.calls == [True, False]
    asyncio.run(run())


@pytest.mark.parametrize("confirmation", ["0", "1"])
def test_guided_fast_requires_confirmation_with_supply_warning(monkeypatch, tmp_path, capsys, confirmation):
    calls = []
    async def apply(args):
        calls.append(vars(args))
    answers = iter(("6", "2", confirmation))
    monkeypatch.setattr("builtins.input", lambda _label: next(answers))
    monkeypatch.setattr(cli, "_set", apply)
    interactive.preference_menu(original(), tmp_path / "devices.json")
    assert calls == ([] if confirmation == "0" else [{"command": "set-fast-charge", "name": "original",
                                                       "config": tmp_path / "devices.json", "enabled": "on"}])
    warning = capsys.readouterr().out
    assert "adequate AC supply" in warning and "reboot persistence" in warning


def test_textual_fast_cancel_and_confirm_show_caution():
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        backend = TuiBackend([original()], monitor_factory=Monitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(120, 42)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            await pilot.press("f2")
            app.query_one("#setting", Select).value = "fast-charge"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "on"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert "adequate AC supply" in app.screen.detail
            await pilot.click("#saving-cancel")
            assert backend.monitor.calls == []
            await pilot.click("#apply-setting")
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert backend.monitor.calls == [True]
    asyncio.run(run())
