import asyncio
import json
import stat

import pytest

from solix_gen2.config import DeviceConfig, load_config, save_config
from solix_gen2.manager import MonitorService
from solix_gen2.protocol import Model


def test_config_preserves_pairing_id_privately(tmp_path):
    path = tmp_path / "config.json"
    client_id = "a" * 40
    device = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C2000_GEN2,
                          client_id, timezone_name="Europe/Vienna")
    save_config([device], path)
    assert load_config(path) == [device]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["devices"][0]["client_id"] == client_id
    assert json.loads(path.read_text())["devices"][0]["timezone_name"] == "Europe/Vienna"


def test_http_auth_and_status(tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer
    from solix_gen2.server import create_app

    async def scenario():
        service = MonitorService([DeviceConfig("ups", "AA:BB:CC:DD:EE:02", Model.C1000_GEN2, "b" * 40)])

        async def no_ble():
            pass

        service.start = no_ble
        service.stop = no_ble
        service._status["ups"]["connected"] = True
        service._on_update("ups", {"battery_percentage": 87, "ac_output_enabled": 1})
        async with TestServer(create_app(service, token="test-token")) as server:
            async with TestClient(server) as client:
                assert (await client.get("/devices/ups")).status == 401
                headers = {"Authorization": "Bearer test-token"}
                response = await client.get("/devices/ups", headers=headers)
                assert response.status == 200
                assert (await response.json())["metrics"]["battery_percentage"] == 87
                assert (await client.get("/devices/missing", headers=headers)).status == 404
                metrics = await (await client.get("/metrics", headers=headers)).text()
                assert 'solix_gen2_battery_percentage{device="ups"} 87' in metrics

    asyncio.run(scenario())
