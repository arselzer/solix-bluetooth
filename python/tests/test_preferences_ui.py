"""Preference UI commands use fake transports; no actual output is changed."""

import argparse
import asyncio
from dataclasses import asdict
import json
import time
from types import SimpleNamespace

import pytest

from solix_link import cli, interactive
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.config import DeviceConfig, save_config
from solix_link.protocol import Model
from solix_link.tui import Target, TuiBackend, controls_for, create_app


class FakeMonitor:
    def __init__(self, *_args, **kwargs):
        self.callback = kwargs.get("on_update")
        self.connected = False
        self.metrics = {"battery_percentage": 80, "ac_output_enabled": 1, "device_timeout_minutes": 0,
                        "temperature_unit_fahrenheit": 0, "ac_fast_charge_enabled": 0,
                        "ac_power_saving_mode_enabled": 0, "dc_power_saving_mode_enabled": 0,
                        "usage_mode": "standard", "active_tariff": "none"}
        self.calls = []

    async def connect(self, **_kwargs):
        self.connected = True
        if self.callback:
            self.callback(self.metrics)

    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()

    async def _set(self, command, metric, enabled):
        self.calls.append((command, enabled))
        self.metrics[metric] = int(enabled)
        if self.callback:
            self.callback(self.metrics)
        return self.metrics.copy()

    async def set_temperature_unit(self, fahrenheit):
        return await self._set("temperature", "temperature_unit_fahrenheit", fahrenheit)

    async def set_fast_charge_enabled(self, enabled):
        return await self._set("fast", "ac_fast_charge_enabled", enabled)

    async def set_ac_power_saving_enabled(self, enabled):
        return await self._set("ac-saving", "ac_power_saving_mode_enabled", enabled)

    async def set_dc_power_saving_enabled(self, enabled):
        return await self._set("dc-saving", "dc_power_saving_mode_enabled", enabled)

    async def disconnect(self):
        self.connected = False


@pytest.mark.parametrize("command,flags,call,field", [
    ("set-temperature-unit", ["--unit", "fahrenheit"], "temperature", "temperature_unit_fahrenheit"),
    ("set-fast-charge", ["--enabled", "on"], "fast", "ac_fast_charge_enabled"),
    ("set-ac-power-saving", ["--enabled", "on"], "ac-saving", "ac_power_saving_mode_enabled"),
    ("set-dc-power-saving", ["--enabled", "on"], "dc-saving", "dc_power_saving_mode_enabled"),
])
def test_original_cli_preferences_use_semantic_booleans(tmp_path, monkeypatch, capsys, command, flags, call, field):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)], path)
    monitor = FakeMonitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    args = cli.parser().parse_args([command, "--name", "ups", "--config", str(path), *flags])
    asyncio.run(cli._set(args))
    output = capsys.readouterr()
    assert monitor.calls == [(call, True)] and not monitor.connected
    assert json.loads(output.out)["confirmed"] == {field: 1}
    if "power-saving" in command:
        assert "automatically turn the output off" in output.err


@pytest.mark.parametrize("model", [Model.C300, Model.C2000_GEN2, Model.C1000_GEN2])
@pytest.mark.parametrize("command", ["set-temperature-unit", "set-ac-power-saving", "set-dc-power-saving"])
def test_cli_original_preferences_reject_other_models_before_connect(tmp_path, monkeypatch, model, command):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model)], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: pytest.fail("No connection for unsupported control"))
    args = argparse.Namespace(command=command, name="ups", config=path, enabled="on", unit="fahrenheit")
    with pytest.raises(ValueError, match="not verified"):
        asyncio.run(cli._set(args))


def test_line_preferences_require_confirmation_and_warn_about_low_load(monkeypatch, tmp_path, capsys):
    called = []
    async def set_command(args):
        called.append((args.command, args.enabled))
    monkeypatch.setattr(cli, "_set", set_command)
    replies = iter(["3", "2", "0", "4", "2", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    station = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)
    interactive.preference_menu(station, tmp_path)
    assert called == []
    interactive.preference_menu(station, tmp_path)
    assert called == [("set-dc-power-saving", "on")]
    assert "automatically turn the output off at low load" in capsys.readouterr().out


def test_line_native_fast_charge_routes_boolean(monkeypatch, tmp_path):
    calls = []
    async def request(directory, command, **fields):
        calls.append((command, fields))
        return {"metrics": {"ac_fast_charge_enabled": 1}}
    monkeypatch.setattr(interactive, "ap_service_request", request)
    replies = iter(["1", "2", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    interactive.preference_menu(SimpleNamespace(name="ups", model=Model.C1000_GEN2), tmp_path, tmp_path / "ap")
    assert calls == [("set-fast-charge", {"name": "ups", "enabled": True})]


def test_tui_original_preferences_and_gen2_fast_guards():
    async def run():
        backend = TuiBackend([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)], monitor_factory=FakeMonitor)
        await backend.connect("ble:ups")
        await backend.control("temperature-unit", "fahrenheit")
        await backend.control("fast-charge", "on")
        await backend.control("ac-power-saving", "on")
        await backend.control("dc-power-saving", "off")
        assert backend.monitor.calls == [("temperature", True), ("fast", True), ("ac-saving", True), ("dc-saving", False)]
        await backend.disconnect()
        gen2 = TuiBackend([DeviceConfig("g2", "AA:BB:CC:DD:EE:02", Model.C1000_GEN2, "a" * 40)], monitor_factory=FakeMonitor)
        await gen2.connect("ble:g2")
        gen2.monitor.metrics.update(usage_mode="time_of_use", active_tariff="peak")
        with pytest.raises(ValueError, match="Standard"):
            await gen2.control("fast-charge", "on")
        await gen2.control("fast-charge", "off")
        assert gen2.monitor.calls == [("fast", False)]
        await gen2.disconnect()
    asyncio.run(run())


def test_tui_native_fast_guard_prevents_unknown_or_stale_baseline(tmp_path):
    config = APServiceConfig("ups", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40, model=Model.C1000_GEN2)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    async def run():
        calls = []
        state = {"connected": True, "available": True, "last_seen_timestamp": time.time(), "control_enabled": True,
                 "metrics": {"ac_fast_charge_enabled": 0, "ac_input_connected": 1, "usage_mode": "standard", "active_tariff": "none"}}
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return state.copy()
        backend = TuiBackend([], tmp_path, requester=request)
        await backend.connect("native")
        await backend.control("fast-charge", "on")
        assert calls[-1] == ("set-fast-charge", {"enabled": True})
        state["metrics"]["ac_input_connected"] = 0
        with pytest.raises(ValueError, match="mains"):
            await backend.control("fast-charge", "off")
        state["metrics"]["ac_input_connected"] = 1
        state["last_seen_timestamp"] = time.time() - 31
        await backend.refresh()
        before = len(calls)
        with pytest.raises(ValueError, match="Fresh"):
            await backend.control("fast-charge", "off")
        assert len(calls) == before
        await backend.disconnect()
    asyncio.run(run())


@pytest.mark.parametrize("model,native", [(Model.C2000_GEN2, False), (Model.C2000_GEN2, True), (Model.C300, False)])
def test_tui_never_exposes_new_preferences_on_unsupported_models(model, native):
    controls = {control.key for control in controls_for(Target("test", "Station", model, native))}
    assert not controls & {"fast-charge", "temperature-unit", "ac-power-saving", "dc-power-saving"}


def test_tui_smart_mode_confirmation_cancel_and_apply():
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        backend = TuiBackend([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)], monitor_factory=FakeMonitor)
        app = create_app(backend=backend)
        async with app.run_test(size=(110, 38)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            await pilot.press("f2")
            await pilot.pause()
            app.query_one("#setting", Select).value = "ac-power-saving"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "on"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "PowerSavingConfirmScreen"
            assert backend.monitor.calls == []
            await pilot.click("#saving-cancel")
            await pilot.pause()
            assert backend.monitor.calls == []
            await pilot.click("#apply-setting")
            await pilot.pause()
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert backend.monitor.calls == [("ac-saving", True)]
    asyncio.run(run())
