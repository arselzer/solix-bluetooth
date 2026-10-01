"""Native AC Smart interface contracts use only synthetic profiles and RPC."""

import asyncio
from dataclasses import asdict
import json
import time

import pytest

from solix_link import ap_service_cli, cli
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.protocol import Model
from solix_link.tui import TuiBackend, create_app


@pytest.fixture
def native_config(tmp_path):
    config = APServiceConfig("original", "wlan_unused", "phy9", "AT", "A1761SYNTHETIC01", "a" * 40,
                             model=Model.C1000)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    return tmp_path


def status():
    return {"connected": True, "available": True, "control_enabled": True,
            "last_seen_timestamp": time.time(), "metrics": {"ac_output_enabled": 0,
                "ac_output_timer_remaining_seconds": 0, "ac_power_saving_mode_enabled": 1}}


@pytest.mark.parametrize("enabled", [False, True])
def test_native_cli_routes_actual_bool_and_prints_ac_requirement(monkeypatch, native_config, capsys, enabled):
    calls = []
    async def request(directory, command, **fields):
        calls.append((directory, command, fields))
        return status()
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    args = cli.parser().parse_args(["ap-service-set-ac-power-saving", "--directory", str(native_config),
                                    "--name", "original", "--enabled", "on" if enabled else "off"])
    ap_service_cli.dispatch(args)
    assert calls == [(native_config, "set-ac-power-saving", {"name": "original", "enabled": enabled})]
    warning = capsys.readouterr().err
    assert "AC output OFF" in warning and "inactive AC timer" in warning and "inactivity counter" in warning


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("changes", [{"connected": False}, {"available": False},
                                     {"last_seen_timestamp": 0}, {"last_seen_timestamp": time.time() - 31},
                                     {"control_enabled": False}, {"ac_output_enabled": 1},
                                     {"ac_output_enabled": None}, {"ac_output_enabled": True},
                                     {"ac_output_enabled": 0.0}, {"ac_output_timer_remaining_seconds": None},
                                     {"ac_output_timer_remaining_seconds": True}, {"ac_output_timer_remaining_seconds": 0.0},
                                     {"ac_output_timer_remaining_seconds": "0"}, {"ac_output_timer_remaining_seconds": 30},
                                     {"ac_power_saving_mode_enabled": None}, {"ac_power_saving_mode_enabled": True}])
def test_native_ac_smart_rejects_stale_active_unknown_or_readonly_before_rpc(native_config, enabled, changes):
    async def run():
        current = status()
        for key, value in changes.items():
            if key.startswith("ac_"):
                current["metrics"][key] = value
            else:
                current[key] = value
        calls = []
        async def request(_path, command, **fields):
            calls.append((command, fields))
            return current
        backend = TuiBackend([], native_config, requester=request)
        await backend.connect("native")
        with pytest.raises((ValueError, RuntimeError)):
            await backend.control("ac-power-saving", "on" if enabled else "off")
        assert calls == [("status", {})]
    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_native_ac_smart_routes_both_directions_with_zero_timer(native_config, enabled):
    async def run():
        calls = []
        async def request(_path, command, **fields):
            calls.append((command, fields))
            return status()
        backend = TuiBackend([], native_config, requester=request)
        await backend.connect("native")
        await backend.control("ac-power-saving", "on" if enabled else "off")
        assert calls == [("status", {}), ("set-ac-power-saving", {"enabled": enabled})]
        with pytest.raises(ValueError, match="unavailable"):
            await backend.control("ac-output", "on")
        assert len(calls) == 2
    asyncio.run(run())


def test_native_ac_smart_textual_confirmation_and_timer_disable(native_config):
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        current = status()
        calls = []
        async def request(_path, command, **fields):
            calls.append((command, fields))
            return current
        backend = TuiBackend([], native_config, requester=request)
        app = create_app(backend=backend)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#connect")
            await pilot.pause()
            await pilot.press("f2")
            app.query_one("#setting", Select).value = "ac-power-saving"
            await pilot.pause()
            app.query_one("#setting-value", Input).value = "off"
            await pilot.click("#apply-setting")
            await pilot.pause()
            assert "AC output OFF" in app.screen.detail and "inactive AC timer" in app.screen.detail
            await pilot.click("#saving-cancel")
            assert all(command == "status" for command, _fields in calls)
            await pilot.click("#apply-setting")
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert [(command, fields) for command, fields in calls if command != "status"] == [
                ("set-ac-power-saving", {"enabled": False})]
            current["metrics"]["ac_output_timer_remaining_seconds"] = 30
            app.render_snapshot(current)
            assert app.query_one("#apply-setting").disabled
    asyncio.run(run())
