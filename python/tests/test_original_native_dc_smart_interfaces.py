"""Native DC Smart interface checks use synthetic status and Unix RPC only."""

import asyncio
from dataclasses import asdict
import json
import time

import pytest

from solix_link import ap_service_cli, cli
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.protocol import Model
from solix_link.tui import TuiBackend


@pytest.fixture
def native_config(tmp_path):
    config = APServiceConfig("original", "wlan_unused", "phy9", "AT", "A1761SYNTHETIC01", "a" * 40,
                             model=Model.C1000)
    private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
    return tmp_path


def status():
    return {"connected": True, "available": True, "control_enabled": True,
            "last_seen_timestamp": time.time(), "metrics": {"dc_output_enabled": 0,
                                                              "dc_power_saving_mode_enabled": 1}}


@pytest.mark.parametrize("enabled", [False, True])
def test_native_cli_routes_semantic_bool_and_prints_warning(monkeypatch, tmp_path, capsys, enabled):
    calls = []
    async def request(directory, command, **fields):
        calls.append((directory, command, fields))
        return status()
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    args = cli.parser().parse_args(["ap-service-set-dc-power-saving", "--directory", str(tmp_path),
                                    "--name", "original", "--enabled", "on" if enabled else "off"])
    ap_service_cli.dispatch(args)
    assert calls == [(tmp_path, "set-dc-power-saving", {"name": "original", "enabled": enabled})]
    assert "inactivity counter" in capsys.readouterr().err


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("changes", [{"connected": False}, {"available": False},
                                     {"last_seen_timestamp": 0}, {"last_seen_timestamp": None},
                                     {"control_enabled": False}, {"dc_output_enabled": 1},
                                     {"dc_output_enabled": None}, {"dc_output_enabled": True},
                                     {"dc_output_enabled": 0.0}, {"dc_power_saving_mode_enabled": None}])
def test_native_tui_refuses_stale_dc_on_unknown_or_readonly_before_rpc(native_config, enabled, changes):
    async def run():
        current = status()
        for key, value in changes.items():
            if key.startswith("dc_"):
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
            await backend.control("dc-power-saving", "on" if enabled else "off")
        assert calls == [("status", {})]
    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_native_tui_routes_both_directions_with_dc_off(native_config, enabled):
    async def run():
        calls = []
        async def request(_path, command, **fields):
            calls.append((command, fields))
            return status()
        backend = TuiBackend([], native_config, requester=request)
        await backend.connect("native")
        await backend.control("dc-power-saving", "on" if enabled else "off")
        assert calls == [("status", {}), ("set-dc-power-saving", {"enabled": enabled})]
    asyncio.run(run())
