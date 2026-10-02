"""Dedicated native RSSI requests preserve namespace and failure semantics."""
import asyncio
import base64
from dataclasses import replace
import json

import pytest

from solix_link.commands import native_commands_for_model
from solix_link.native_mqtt import NativeMqttCommands, RADIO_NATIVE_PATTERN
from solix_link.protocol import DATA_RESPONSE, Model, parse_packet
from test_mqtt_startup import Session, make_server
from test_native_boolean_settings import service, unpack
from test_native_radio_wireless_state import WirelessStation, feed


RSSI_REPLY = b"\x00\xa1\x04" + (-70).to_bytes(4, "little", signed=True)


class RssiStation(WirelessStation):
    rssi_reply = RSSI_REPLY

    async def request(self, request):
        command, fields = unpack(request)
        if command == "0022":
            self.requests.append((command, fields))
            self.mutate()
            return self.rssi_reply
        return await super().request(request)


def setup(tmp_path, **options):
    server, _ = service(tmp_path, **options)
    server.connection = station = RssiStation(server)
    return server, station


def test_exact_native_rssi_query_has_empty_body_and_radio_namespace():
    request = NativeMqttCommands("SYNTHETIC", "a" * 40, Model.C1000_GEN2).wifi_rssi()
    outer = json.loads(request.payload)
    frame = parse_packet(base64.b64decode(json.loads(outer["payload"])["data"]))
    assert frame.pattern == RADIO_NATIVE_PATTERN and frame.command == b"\0\x22"
    assert not frame.payload and outer["head"]["cmd"] == 17
    assert request.response_command == "0822" and request.response_pattern == RADIO_NATIVE_PATTERN


@pytest.mark.parametrize("reply,expected", [(RSSI_REPLY, -70), (b"\1", None)])
def test_readonly_query_preserves_failure_and_settings(tmp_path, reply, expected):
    server, station = setup(tmp_path, allow_control=False)
    station.rssi_reply = reply
    before = bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)
    result = asyncio.run(server.wifi_rssi())
    assert result["wifi_rssi_dbm"] == expected and result["rssi_available"] == (expected is not None)
    assert result["settings_unchanged"] and "wireless_state" not in result
    assert before == (bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2))
    assert [c for c, _ in station.requests] == ["0100", "0022", "0100"]
    assert "wifi-rssi" not in native_commands_for_model(Model.C1000_GEN2)


@pytest.mark.parametrize("problem", ["main", "radio", "missing_versions", "other_model", "disconnected"])
def test_unsupported_firmware_and_models_send_no_rssi_request(tmp_path, problem):
    server, station = setup(tmp_path)
    if problem == "main": station.version = b"\4" + bytes((10, 4, 1, 1)) + station.version[5:]
    elif problem == "radio": station.version = station.version[:-4] + bytes((1, 3, 3, 0))
    elif problem == "missing_versions": station.version = b""
    elif problem == "other_model":
        server.config = replace(server.config, model=Model.C2000_GEN2)
        server.commands.model = Model.C2000_GEN2
    else: server.connection = None
    with pytest.raises((ValueError, ConnectionError)):
        asyncio.run(server.wifi_rssi())
    assert all(c == "0100" for c, _ in station.requests)


@pytest.mark.parametrize("reply", [b"", b"\0", b"\1\0", RSSI_REPLY + b"\0",
                                  b"\0\xa1\4" + bytes(4)])
def test_malformed_rssi_never_becomes_quality_or_retries(tmp_path, reply):
    server, station = setup(tmp_path)
    station.rssi_reply = reply
    with pytest.raises(ValueError):
        asyncio.run(server.wifi_rssi())
    assert [c for c, _ in station.requests] == ["0100", "0022"]


def test_protected_change_fails_with_no_setting_write(tmp_path):
    server, station = setup(tmp_path)
    station.mutate = lambda: station.a4.__setitem__(5, station.a4[5] ^ 1)
    with pytest.raises(RuntimeError):
        asyncio.run(server.wifi_rssi())
    assert [c for c, _ in station.requests] == ["0100", "0022", "0100"]


def test_other_models_fail_in_builder():
    for model in (Model.C1000, Model.C2000_GEN2, Model.C300):
        with pytest.raises(ValueError):
            NativeMqttCommands("SYNTHETIC", "a" * 40, model).wifi_rssi()


def test_exact_namespace_opcode_identity_and_retained_matching(tmp_path, monkeypatch):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            session.connection._command_ready_at = clock.now
            task = session.request(server.commands.wifi_rssi())
            assert (await session.packet())[0] == 0x30
            for pattern, command, values in (
                (DATA_RESPONSE, "0822", {}), (RADIO_NATIVE_PATTERN, "0803", {}),
                (RADIO_NATIVE_PATTERN, "0822", {"serial": "other"}),
                (RADIO_NATIVE_PATTERN, "0822", {"retained": True}),
            ):
                feed(session, pattern, command, reply=RSSI_REPLY, **values)
                await asyncio.sleep(0)
                assert not task.done()
            feed(session, RADIO_NATIVE_PATTERN, "0822", reply=RSSI_REPLY)
            assert await asyncio.wait_for(task, timeout=1) == RSSI_REPLY
            assert server.last_seen is None and not server.metrics
            assert session.connection._response_pattern == DATA_RESPONSE
            # RSSI replies must not satisfy a following wireless-state query.
            task = session.request(server.commands.wireless_state())
            await session.packet()
            feed(session, RADIO_NATIVE_PATTERN, "0822", reply=RSSI_REPLY)
            await asyncio.sleep(0)
            assert not task.done()
            feed(session, RADIO_NATIVE_PATTERN)
            assert await asyncio.wait_for(task, timeout=1) == b"\0\xa1\1\0\xa2\1\1"
        finally:
            await session.close()
    asyncio.run(run())
