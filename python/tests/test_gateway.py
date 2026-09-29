"""Authenticated gateway commands, schemas, capability and failure reporting."""

import asyncio
import time

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer

from solix_link import PowerFlowTimeout
from solix_link.server import create_app


class Gateway:
    devices = {"ups": object()}

    def __init__(self):
        self.calls = []
        self.failure = None

    async def start(self):
        pass

    async def stop(self):
        pass

    def snapshot(self, name):
        return {"name": name, "model": "c2000_gen2", "protocol": "native_mqtt",
                "connected": True, "available": True, "last_seen_timestamp": time.time(),
                "metrics": {"battery_percentage": 90}, "power_flow": "grid"}

    def snapshots(self):
        return [self.snapshot("ups")]

    def supported_commands(self, name):
        return ["set-charge-power", "set-tou-plan", "return-grid"]

    async def command(self, name, command, **values):
        self.calls.append((name, command, values))
        if self.failure:
            raise self.failure
        return self.snapshot(name)


def test_http_controls_require_token_and_explicit_enablement():
    gateway = Gateway()
    with pytest.raises(ValueError, match="HTTP controls require"):
        create_app(gateway, allow_control=True)
    async def run():
        async with TestClient(TestServer(create_app(gateway))) as client:
            assert (await client.get("/devices")).status == 200
            assert (await (await client.get("/devices/ups")).json())["controls"] == []
            assert (await client.post("/devices/ups/commands", json={"command": "set-charge-power", "watts": 300})).status == 403
        assert not gateway.calls
    asyncio.run(run())


def test_gateway_rejects_unauthenticated_extra_fields_and_output_commands():
    gateway = Gateway()
    async def run():
        async with TestClient(TestServer(create_app(gateway, token="test-token", allow_control=True))) as client:
            assert (await client.post("/devices/ups/commands", json={"command": "return-grid", "timeout": 10})).status == 401
            headers = {"Authorization": "Bearer test-token"}
            for body in ({"command": "set-ac-output", "enabled": False},
                         {"command": "set-charge-power", "watts": True},
                         {"command": "set-charge-power", "watts": 300, "raw": "forbidden"},
                         {"command": "set-tou-plan", "enabled": True, "periods": []},
                         {"command": "return-grid", "timeout": 0}):
                assert (await client.post("/devices/ups/commands", json=body, headers=headers)).status == 400
            assert not gateway.calls
            result = await client.post("/devices/ups/commands", json={"command": "set-charge-power", "watts": 300}, headers=headers)
            assert result.status == 200 and (await result.json())["controls"] == gateway.supported_commands("ups")
            assert gateway.calls == [("ups", "set-charge-power", {"watts": 300})]
    asyncio.run(run())


def test_gateway_grid_timeout_does_not_report_success_or_leak_exception_text():
    gateway = Gateway()
    async def run():
        async with TestClient(TestServer(create_app(gateway, token="test-token", allow_control=True))) as client:
            headers = {"Authorization": "Bearer test-token"}
            gateway.failure = PowerFlowTimeout(gateway.snapshot("ups"))
            response = await client.post("/devices/ups/commands", json={"command": "return-grid", "timeout": 10}, headers=headers)
            body = await response.json()
            assert response.status == 504 and body["settings_may_have_changed"]
            assert body["error"] == "PowerFlowTimeout" and "grid_power_confirmed" not in body["device"]
            gateway.failure = RuntimeError("PRIVATE-DEVICE-IDENTIFIER")
            response = await client.post("/devices/ups/commands", json={"command": "return-grid", "timeout": 10}, headers=headers)
            body = await response.json()
            assert response.status == 409 and "PRIVATE" not in str(body)
    asyncio.run(run())
