"""Preview/history routes keep authentication and never call station commands."""
import asyncio
from copy import deepcopy
import json
import time

import pytest

from http_helpers import api_client
from test_charging_policy import request as policy_request, station
from test_gateway import Gateway
from solix_link.cli import parser
from solix_link.charging_preview_cli import offline_preview
from solix_link.history import HistoryStore
from solix_link.server import create_app


class ReadOnlyGateway(Gateway):
    def __init__(self):
        super().__init__()
        self.current = station()
        self.current["name"] = "ups"

    def snapshot(self, name):
        result = deepcopy(self.current)
        result["last_seen_timestamp"] = time.time()
        return result


def body():
    request = policy_request("export")
    now = time.time()
    request["signals"]["export"]["timestamp"] = now
    request["config"]["latch_changed_at"] = now - 300
    return request


def test_preview_works_readonly_but_requires_auth_and_has_no_command_path():
    gateway = ReadOnlyGateway()
    async def run():
        async with api_client(create_app(gateway, token="token")) as client:
            target = "/devices/ups/charging-preview"
            assert (await client.post(target, json=body())).status_code == 401
            headers = {"Authorization": "Bearer token"}
            response = await client.post(target, json=body(), headers=headers)
            assert response.status_code == 200
            report = response.json()
            assert report["dry_run"] and report["commands_sent"] == 0
            assert report["decision"] == "opportunity"
            assert response.headers["cache-control"] == "no-store"
            assert "PRIVATE-STATION" not in response.text and "ups" not in response.text
            assert (await client.post("/devices/ups/commands", json={"command":"set-charge-power","watts":1000}, headers=headers)).status_code == 403
            gateway.current["metrics"]["ac_fast_charge_enabled"] = 1
            report = (await client.post(target, json=body(), headers=headers)).json()
            assert report["decision"] == "blocked" and "fast_not_confirmed_off" in report["reasons"]
        assert not gateway.calls
    asyncio.run(run())


@pytest.mark.parametrize("payload", [b"{", b"\xff", b'{"config":{},"config":{},"signals":{}}',
    b'{"config":{},"signals":{},"execute":true}', b"["*100+b"]"*100])
def test_bad_preview_bodies_are_fixed_redacted_errors(payload):
    gateway = ReadOnlyGateway()
    async def run():
        async with api_client(create_app(gateway)) as client:
            response = await client.post("/devices/ups/charging-preview", content=payload)
            assert response.status_code == 400
            assert response.json() == {"error":"InvalidChargingPreview", "commands_sent":0,"settings_may_have_changed":False}
            assert (await client.post("/devices/missing/charging-preview", json=body())).status_code == 404
            assert (await client.post("/devices/ups/charging-preview", content=b"x"*4097)).status_code == 413
        assert not gateway.calls
    asyncio.run(run())


def test_history_is_opt_in_private_authenticated_and_survives_restart_without_bridge(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path/"readings.sqlite"
    gateway = ReadOnlyGateway()
    now = time.time()
    with_store = HistoryStore(path)
    baseline = gateway.snapshot("ups")
    baseline["metrics"].update(ac_input_power_w=600,ac_output_power_w=300)
    for offset in (10,5,0):
        baseline["last_seen_timestamp"] = now-offset
        with_store.record([baseline],now=now-offset)
    with_store.close()
    async def run():
        async with api_client(create_app(gateway, token="token")) as client:
            headers = {"Authorization":"Bearer token"}
            assert (await client.get("/history")).status_code == 401
            assert (await client.get("/history",headers=headers)).json()["enabled"] is False
            assert (await client.get("/devices/ups/history",headers=headers)).status_code == 404
        async with api_client(create_app(gateway,token="token",history_file=path)) as client:
            assert (await client.get("/devices/ups/history")).status_code == 401
            info = await client.get("/history",headers=headers)
            assert info.json()["enabled"] and info.json()["estimated"]
            response=await client.get("/devices/ups/history",headers=headers)
            assert response.status_code==200 and response.headers["cache-control"]=="no-store"
            result=response.json()
            assert result["estimated"] and len(result["points"])>=3
            assert result["totals"]["ac_input_energy_kwh_estimate"]==pytest.approx(600*10/3600000)
            assert (await client.head("/history",headers=headers)).status_code==200
            assert (await client.head("/devices/ups/history",headers=headers)).status_code==200
            for query in ("limit=2001","since=NaN","since=inf","limit=1&limit=2","private=PRIVATE-TOKEN"):
                failure=await client.get("/devices/ups/history?"+query,headers=headers)
                assert failure.status_code==400 and failure.json()=={"error":"InvalidHistoryQuery"}
        assert not gateway.calls
    asyncio.run(run())
    assert path.stat().st_mode & 0o077==0


def test_failed_history_sampler_is_visible_without_stopping_gateway(tmp_path):
    tmp_path.chmod(0o700)
    gateway=ReadOnlyGateway()
    good=gateway.snapshots
    gateway.snapshots=lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE-ERROR"))
    async def run():
        async with api_client(create_app(gateway,history_file=tmp_path/"h.sqlite")) as client:
            await asyncio.sleep(0.02)
            failure=await client.get("/history")
            assert failure.status_code==503 and failure.json()=={"error":"HistoryUnavailable"}
            gateway.snapshots=good
            assert (await client.get("/devices")).status_code==200
            assert not gateway.calls
    asyncio.run(run())


def test_cli_preview_and_history_options_work_without_station_clients(tmp_path,monkeypatch):
    from solix_link import client
    monkeypatch.setattr(client,"SolixMonitor",lambda *args,**kwargs:pytest.fail("No BLE client allowed"))
    snapshot=station()
    snapshot["last_seen_timestamp"]=time.time()
    snapfile,requestfile=tmp_path/"snapshot.json",tmp_path/"request.json"
    snapfile.write_text(json.dumps(snapshot));requestfile.write_text(json.dumps(body()))
    result=offline_preview(snapfile,requestfile)
    assert result["commands_sent"]==0 and result["decision"]=="opportunity"
    args=parser().parse_args(["charging-preview","--snapshot-file",str(snapfile),"--request-file",str(requestfile)])
    assert args.command=="charging-preview"
    for arguments in (["serve"],["ap-service-serve","--directory",str(tmp_path)]):
        args=parser().parse_args([*arguments,"--history-file",str(tmp_path/"history.sqlite")])
        assert args.history_retention_days==7
