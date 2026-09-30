import base64
import json

import pytest

from solix_gen2 import Model, NativeMqttCommands, decode_mqtt_telemetry
from solix_gen2.protocol import DATA_REQUEST, DATA_RESPONSE, build_packet, parse_packet, parse_tlvs, tlv


def envelope(*, serial="SYNTHETIC", product="A1783", data=None, command="0421", status=0):
    # Synthetic battery, mains/output, and C2000 Standard-mode blocks.
    body = (tlv(0xa5, bytes([4, 25, 0, 90, 100]))
            + tlv(0xa7, bytes.fromhex("04015600015600"))
            + tlv(0xd9, bytes([4, 0, 0, 10, 90, 1, 0]) + bytes(19)))
    if command == "0900":
        body = bytes([status]) + body
    encoded = base64.b64encode(build_packet(DATA_RESPONSE, bytes.fromhex(command), body)).decode()
    return json.dumps({"head": {"cmd": 16}, "payload": json.dumps({
        "pn": product, "sn": serial, "data": encoded if data is None else data,
    })}).encode()


def test_decode_native_telemetry_and_filter_device():
    message = envelope()
    result = decode_mqtt_telemetry(message, model=Model.C2000_GEN2, expected_serial="SYNTHETIC")
    assert result.metrics["battery_percentage"] == 90
    assert result.metrics["ac_output_enabled"] == 1
    assert result.metrics["ac_input_connected"] == 1
    assert result.metrics["usage_mode"] == "standard"
    assert result.metrics["active_tariff"] == "none"
    assert result.metrics["tou_schedule_slot_count"] == 0
    assert result.raw_tlvs[0xa5] == bytes([4, 25, 0, 90, 100])
    assert decode_mqtt_telemetry(message, model=Model.C2000_GEN2, expected_serial="DIFFERENT") is None
    assert decode_mqtt_telemetry(message, model=Model.C1000_GEN2) is None


def test_ignore_network_state_and_command_acknowledgement():
    state = json.dumps({"payload": json.dumps({"battery": 100, "rssi": -50})})
    assert decode_mqtt_telemetry(state, model=Model.C2000_GEN2) is None
    assert decode_mqtt_telemetry(envelope(command="0857"), model=Model.C2000_GEN2) is None


def test_decode_status_reply_and_reject_failed_reply():
    result = decode_mqtt_telemetry(envelope(command="0900"), model=Model.C2000_GEN2)
    assert result.metrics["battery_percentage"] == 90
    assert result.metrics["ac_output_enabled"] == 1
    with pytest.raises(ValueError, match="status request failed"):
        decode_mqtt_telemetry(envelope(command="0900", status=1), model=Model.C2000_GEN2)


@pytest.mark.parametrize("identity", [{"sn": None}, {"pn": None}, {"sn": 123}])
def test_reject_invalid_device_identity(identity):
    outer = json.loads(envelope())
    payload = json.loads(outer["payload"])
    payload.update(identity)
    outer["payload"] = json.dumps(payload)
    with pytest.raises(ValueError, match="Missing native MQTT device identity"):
        decode_mqtt_telemetry(json.dumps(outer), model=Model.C2000_GEN2)


@pytest.mark.parametrize("message", [b"not json", b"[]", b'{}', b'{"payload":{}}',
                                    b'{"payload":"[]"}', envelope(data="not base64!"),
                                    envelope(data="/wk="), b" "*131073])
def test_reject_malformed_native_mqtt(message):
    with pytest.raises(ValueError):
        decode_mqtt_telemetry(message, model=Model.C2000_GEN2)


def test_reject_corrupt_packet_and_encrypted_payload():
    outer = json.loads(envelope())
    payload = json.loads(outer["payload"])
    packet = bytearray(base64.b64decode(payload["data"]))
    packet[-1] ^= 1
    payload["data"] = base64.b64encode(packet).decode()
    outer["payload"] = json.dumps(payload)
    with pytest.raises(ValueError, match="Invalid native MQTT packet"):
        decode_mqtt_telemetry(json.dumps(outer), model=Model.C2000_GEN2)
    payload["encoding_type"] = 1
    outer["payload"] = json.dumps(payload)
    with pytest.raises(ValueError, match="Encrypted"):
        decode_mqtt_telemetry(json.dumps(outer), model=Model.C2000_GEN2)


def unpack_request(request):
    outer = json.loads(request.payload)
    inner = json.loads(outer["payload"])
    return outer, inner, parse_packet(base64.b64decode(inner["data"], validate=True))


def test_native_read_requests_and_private_representations(monkeypatch):
    monkeypatch.setattr("solix_gen2.native_mqtt.time.time", lambda: 1800000000.125)
    commands = NativeMqttCommands("A1783SYNTHETIC0001", "a" * 40)
    status = commands.status()
    outer, inner, packet = unpack_request(status)
    assert status.topic == "cmd/anker_power/A1783/A1783SYNTHETIC0001/req"
    assert status.response_command == "0900"
    assert packet.pattern == DATA_REQUEST
    assert packet.command.hex() == "0100"
    assert packet.payload.hex() == "a10122fe050300d2496b"
    assert inner["account_id"] == "a" * 40
    assert outer["head"]["device_sn"] == inner["device_sn"] == commands.device_serial
    assert outer["head"]["device_pn"] == "A1783"
    assert outer["head"]["cmd"] == 17
    assert outer["head"]["timestamp"] == 1800000000
    stream = commands.stream(60)
    second, _, packet = unpack_request(stream)
    assert stream.response_command == "0857"
    assert packet.command.hex() == "0057"
    assert packet.payload.hex() == "a10122a2020101a305033c000000fe050300d2496b"
    assert second["head"]["msg_seq"] == outer["head"]["msg_seq"] + 1
    assert second["head"]["sess_id"] == outer["head"]["sess_id"]
    for value in (commands, status, stream):
        assert commands.device_serial not in repr(value)
        assert commands.account_id not in repr(value)


@pytest.mark.parametrize("watts", [300, 1700, 1800])
def test_native_charging_request_has_only_charging_field(monkeypatch, watts):
    monkeypatch.setattr("solix_gen2.native_mqtt.time.time", lambda: 1800000000.125)
    request = NativeMqttCommands("SYNTHETIC", "a" * 40).ac_charging_power(watts)
    _, _, packet = unpack_request(request)
    fields = parse_tlvs(packet.payload)
    assert packet.command.hex() == "0101"
    assert request.response_command == "0901"
    assert list(fields) == [0xA1, 0xA4, 0xFD]  # No AC output switch, timer, or mode fields.
    assert fields[0xA4] == b"\x02" + watts.to_bytes(2, "little")
    assert fields[0xFD] == b"\x001800000000125"


@pytest.mark.parametrize("watts", [True, 0, 200, 350, 1900, 1800.0, "1800"])
def test_reject_unsupported_native_charging_power(watts):
    with pytest.raises(ValueError, match="Charging power"):
        NativeMqttCommands("SYNTHETIC", "a" * 40).ac_charging_power(watts)


@pytest.mark.parametrize("percentage", [80, 85, 90, 95, 100])
def test_native_charge_cap_preserves_lower_limit(monkeypatch, percentage):
    monkeypatch.setattr("solix_gen2.native_mqtt.time.time", lambda: 1800000000.125)
    request = NativeMqttCommands("SYNTHETIC", "a" * 40).charge_cap(percentage)
    _, _, packet = unpack_request(request)
    fields = parse_tlvs(packet.payload)
    assert packet.command.hex() == "0103" and request.response_command == "0903"
    assert list(fields) == [0xA1, 0xAA, 0xFD]
    assert fields[0xAA] == bytes((1, percentage))
    assert fields[0xFD] == b"\x001800000000125"


@pytest.mark.parametrize("percentage", [True, 0, 79, 91, 101, 90.0, "90"])
def test_reject_unsupported_native_charge_cap(percentage):
    with pytest.raises(ValueError, match="Charge cap"):
        NativeMqttCommands("SYNTHETIC", "a" * 40).charge_cap(percentage)


@pytest.mark.parametrize("seconds", [True, 0, -1, 121, 1.5])
def test_reject_unbounded_native_stream(seconds):
    with pytest.raises(ValueError, match="Stream duration"):
        NativeMqttCommands("SYNTHETIC", "a" * 40).stream(seconds)


@pytest.mark.parametrize("serial,account", [("a/b", "a" * 40), ("#", "a" * 40),
                                         ("", "a" * 40), (None, "a" * 40),
                                         ("SYNTHETIC", "short"), ("SYNTHETIC", None)])
def test_reject_invalid_native_request_identity(serial, account):
    with pytest.raises(ValueError):
        NativeMqttCommands(serial, account)


@pytest.mark.parametrize("model", [Model.C1000, Model.C300])
def test_reject_unsupported_native_request_model(model):
    with pytest.raises(ValueError, match="Gen 2 only"):
        NativeMqttCommands("SYNTHETIC", "a" * 40, model=model)


@pytest.mark.parametrize("model,product,maximum", [
    (Model.C1000_GEN2, "A1763", 1200),
    (Model.C2000_GEN2, "A1783", 1800),
])
def test_native_model_profile_identity_and_charging_limit(model, product, maximum):
    commands = NativeMqttCommands("SYNTHETIC", "a" * 40, model=model.value)
    request = commands.ac_charging_power(maximum)
    outer, inner, packet = unpack_request(request)
    assert commands.model is model
    assert commands.product == product
    assert request.topic == f"cmd/anker_power/{product}/SYNTHETIC/req"
    assert outer["head"]["device_pn"] == product
    assert outer["head"]["device_sn"] == inner["device_sn"] == "SYNTHETIC"
    assert list(parse_tlvs(packet.payload)) == [0xA1, 0xA4, 0xFD]
    assert parse_tlvs(packet.payload)[0xA4] == b"\x02" + maximum.to_bytes(2, "little")
    minimum = 100 if model == Model.C1000_GEN2 else 300
    with pytest.raises(ValueError, match=f"{minimum}–{maximum}"):
        commands.ac_charging_power(maximum + 100)


def test_c1000_native_response_requires_matching_product_and_serial():
    result = decode_mqtt_telemetry(envelope(product="A1763"), model=Model.C1000_GEN2,
                                   expected_serial="SYNTHETIC")
    assert result is not None
    assert result.metrics["usage_mode"] == "standard"
    assert result.metrics["backup_reserve_percentage"] == 10
    assert decode_mqtt_telemetry(envelope(product="A1783"), model=Model.C1000_GEN2) is None
    assert decode_mqtt_telemetry(envelope(product="A1763"), model=Model.C1000_GEN2,
                                 expected_serial="DIFFERENT") is None
