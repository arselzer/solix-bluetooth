"""Radio-local queries cannot acknowledge controller writes or expose identities."""
import asyncio
import base64
from dataclasses import replace
import json

import pytest

from solix_link import mqtt_intercept
from solix_link.commands import native_commands_for_model
from solix_link.native_mqtt import (RADIO_NATIVE_PATTERN, NativeMqttCommands,
                                    decode_native_wireless_state)
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, parse_packet
from test_gen2_native_dc_smart import DcStation
from test_mqtt_startup import Session, make_server, mqtt_string
from test_native_boolean_settings import service, unpack


GOOD_REPLY = b"\x00\xa1\x01\x00\xa2\x01\x01"


class WirelessStation(DcStation):
    version = b"\x04" + bytes((9, 4, 1, 1)) + bytes(20) + bytes((0, 3, 3, 0))
    radio_reply = GOOD_REPLY

    async def request(self, request):
        command, fields = unpack(request)
        if command == "0003":
            self.requests.append((command, fields))
            self.mutate()
            return self.radio_reply
        return await super().request(request)


def setup(tmp_path, **options):
    server, _ = service(tmp_path, **options)
    server.connection = station = WirelessStation(server)
    return server, station


def test_exact_radio_query_has_no_controller_source_or_timestamp():
    request = NativeMqttCommands("SYNTHETIC", "a" * 40, Model.C1000_GEN2).wireless_state()
    outer = json.loads(request.payload)
    frame = parse_packet(base64.b64decode(json.loads(outer["payload"])["data"]))
    assert frame.pattern == RADIO_NATIVE_PATTERN
    assert frame.command == b"\x00\x03" and frame.payload == b""
    assert outer["head"]["cmd"] == 17 and outer["head"]["cmd_status"] == 2
    assert request.response_command == "0803" and request.response_pattern == RADIO_NATIVE_PATTERN
    controller = NativeMqttCommands("SYNTHETIC", "a" * 40, Model.C1000_GEN2).status()
    assert controller.response_pattern == DATA_RESPONSE


@pytest.mark.parametrize("model", [Model.C1000, Model.C2000_GEN2, Model.C300])
def test_radio_builder_does_not_assume_other_models(model):
    with pytest.raises(ValueError):
        NativeMqttCommands("SYNTHETIC", "a" * 40, model).wireless_state()


def test_selected_state_bytes_omit_mac_ssid_and_unknown_fields():
    reply = GOOD_REPLY + b"\xa3\x0cAABBCCDDEEFF\xa4\x0cprivate-ssid"
    assert decode_native_wireless_state(reply) == {
        "bluetooth_application_state": 0, "wifi_application_state": 1}
    assert decode_native_wireless_state(b"\0\xa1\1\xff\xa2\1\2") == {
        "bluetooth_application_state": 255, "wifi_application_state": 2}


@pytest.mark.parametrize("reply", [b"", b"\1", b"\0", b"\0\xa1\1\0",
    b"\0\xa1\0\xa2\1\1", b"\0\xa1\2\0\0\xa2\1\1",
    GOOD_REPLY + b"\xa1\1\1", GOOD_REPLY + b"\xa3", GOOD_REPLY + b"\xa3\2\0",
    GOOD_REPLY + b"\xa3\0\xa3\0"])
def test_invalid_and_ambiguous_fields_fail_without_disclosing_reply(reply):
    with pytest.raises(ValueError):
        decode_native_wireless_state(reply)


def test_query_works_with_controls_disabled_and_preserves_complete_settings(tmp_path):
    server, station = setup(tmp_path, allow_control=False)
    before = bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)
    result = asyncio.run(server.wireless_state())
    assert result["settings_unchanged"] and result["wireless_state"]["wifi_application_state"] == 1
    assert (bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)) == before
    assert [c for c, _ in station.requests] == ["0100", "0003", "0100"]
    assert "wireless-state" not in native_commands_for_model(Model.C1000_GEN2)


@pytest.mark.parametrize("problem", ["main", "radio", "missing_versions", "other_model", "disconnected"])
def test_unsupported_firmware_and_models_send_no_radio_query(tmp_path, problem):
    server, station = setup(tmp_path)
    if problem == "main": station.version = b"\4" + bytes((10, 4, 1, 1)) + station.version[5:]
    elif problem == "radio": station.version = station.version[:-4] + bytes((1, 3, 3, 0))
    elif problem == "missing_versions": station.version = b""
    elif problem == "other_model": server.config = replace(server.config, model=Model.C2000_GEN2); server.commands.model = Model.C2000_GEN2
    else: server.connection = None
    with pytest.raises((ValueError, ConnectionError)):
        asyncio.run(server.wireless_state())
    assert all(c == "0100" for c, _ in station.requests)


@pytest.mark.parametrize("block,offset", [("a4", 5), ("a4", 9), ("d9", 3), ("a7", 1), ("b2", 1)])
def test_unexpected_setting_or_output_change_fails_without_writes_or_retries(tmp_path, block, offset):
    server, station = setup(tmp_path)
    station.mutate = lambda: getattr(station, block).__setitem__(offset, getattr(station, block)[offset] ^ 1)
    with pytest.raises(RuntimeError):
        asyncio.run(server.wireless_state())
    assert [c for c, _ in station.requests] == ["0100", "0003", "0100"]


def feed(session, pattern, command="0803", *, serial=None, retained=False, reply=GOOD_REPLY):
    config = session.connection.server.config
    frame = build_packet(pattern, bytes.fromhex(command), reply)
    envelope = json.dumps({"payload": json.dumps({"sn": serial or config.device_serial,
        "pn": config.product, "data": base64.b64encode(frame).decode()})}).encode()
    topic = f"dt/anker_power/{config.product}/{config.device_serial}/param_info"
    session.reader.feed_data(mqtt_intercept.mqtt_packet(0x31 if retained else 0x30,
                                                      mqtt_string(topic) + envelope))


def test_namespace_identity_retained_and_opcode_matching_do_not_cross_ack(tmp_path, monkeypatch):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            session.connection._command_ready_at = clock.now
            request = server.commands.wireless_state()
            task = session.request(request)
            assert (await session.packet())[0] == 0x30
            for pattern, command, values in (
                (DATA_RESPONSE, "0803", {}), (RADIO_NATIVE_PATTERN, "0824", {}),
                (RADIO_NATIVE_PATTERN, "0803", {"serial": "other"}),
                (RADIO_NATIVE_PATTERN, "0803", {"retained": True}),
                (b"\3\1\x10", "0803", {}),
            ):
                feed(session, pattern, command, **values)
                await asyncio.sleep(0)
                assert not task.done()
            assert server.last_seen is None
            feed(session, RADIO_NATIVE_PATTERN)
            assert await asyncio.wait_for(task, timeout=1) == GOOD_REPLY
            assert server.last_seen is None and not server.metrics
            assert session.connection._response_pattern == DATA_RESPONSE
            # A radio reply must also fail to acknowledge an ordinary controller query.
            controller = server.commands.readiness()
            task = session.request(controller)
            await session.packet()
            feed(session, RADIO_NATIVE_PATTERN)
            await asyncio.sleep(0)
            assert not task.done()
            feed(session, DATA_RESPONSE, "0889")
            assert await asyncio.wait_for(task, timeout=1) == GOOD_REPLY
        finally:
            await session.close()
    asyncio.run(run())


def test_radio_reply_not_admitted_without_explicit_pending_namespace(tmp_path):
    server, _ = setup(tmp_path)
    frame = build_packet(RADIO_NATIVE_PATTERN, b"\x08\x03", GOOD_REPLY)
    message = json.dumps({"payload": json.dumps({"sn": server.config.device_serial,
        "pn": server.config.product, "data": base64.b64encode(frame).decode()})}).encode()
    assert mqtt_intercept.native_response(message, server.config) is None
    assert mqtt_intercept.native_response(message, server.config, radio_query=True).payload == GOOD_REPLY
    config = replace(server.config, model=Model.C2000_GEN2)
    assert mqtt_intercept.native_response(message, config, radio_query=True) is None


def test_unknown_radio_opcode_rejected_before_publish(tmp_path, monkeypatch):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            session.connection._command_ready_at = clock.now
            request = replace(server.commands.wireless_state(), response_command="0824")
            with pytest.raises(ValueError):
                await session.connection.request(request)
            assert len(session.writer.frames) == 2
        finally:
            await session.close()
    asyncio.run(run())
