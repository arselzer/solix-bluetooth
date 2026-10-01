"""Native original Fast interfaces use private synthetic profiles and RPC."""

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
            "last_seen_timestamp": time.time(), "metrics": {"ac_fast_charge_enabled": 0}}


@pytest.mark.parametrize("enabled", [False, True])
def test_native_cli_fast_uses_actual_bool_and_original_supply_warning(monkeypatch, native_config, capsys, enabled):
    calls = []
    async def request(directory, command, **fields):
        calls.append((directory, command, fields))
        return status()
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    args = cli.parser().parse_args(["ap-service-set-fast-charge", "--directory", str(native_config),
                                    "--name", "original", "--enabled", "on" if enabled else "off"])
    ap_service_cli.dispatch(args)
    assert calls == [(native_config, "set-fast-charge", {"name": "original", "enabled": enabled})]
    warning = capsys.readouterr().err
    assert "adequate AC supply" in warning and "reboot persistence" in warning


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("changes", [{"connected": False}, {"available": False},
                                     {"last_seen_timestamp": 0}, {"last_seen_timestamp": time.time() - 31},
                                     {"control_enabled": False}, {"ac_fast_charge_enabled": None},
                                     {"ac_fast_charge_enabled": True}, {"ac_fast_charge_enabled": 0.0},
                                     {"ac_fast_charge_enabled": "0"}, {"ac_fast_charge_enabled": 2}])
def test_native_fast_rejects_stale_readonly_or_invalid_flag_before_rpc(native_config, enabled, changes):
    async def run():
        current = status()
        for key, value in changes.items():
            if key == "ac_fast_charge_enabled":
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
            await backend.control("fast-charge", "on" if enabled else "off")
        assert calls == [("status", {})]
    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_native_fast_does_not_require_invented_original_mains_or_tariff_fields(native_config, enabled):
    async def run():
        calls = []
        async def request(_path, command, **fields):
            calls.append((command, fields))
            return status()
        backend = TuiBackend([], native_config, requester=request)
        await backend.connect("native")
        await backend.control("fast-charge", "on" if enabled else "off")
        assert calls == [("status", {}), ("set-fast-charge", {"enabled": enabled})]
    asyncio.run(run())


def test_native_fast_textual_cancel_then_explicit_confirm(native_config):
    pytest.importorskip("textual")
    from textual.widgets import Input, Select
    async def run():
        calls = []
        async def request(_path, command, **fields):
            calls.append((command, fields))
            return status()
        backend = TuiBackend([], native_config, requester=request)
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
            assert all(command == "status" for command, _fields in calls)
            await pilot.click("#apply-setting")
            await pilot.click("#saving-confirm")
            await pilot.pause()
            assert [(command, fields) for command, fields in calls if command != "status"] == [
                ("set-fast-charge", {"enabled": True})]
    asyncio.run(run())
