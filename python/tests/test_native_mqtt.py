import base64
import json

import pytest

from solix_gen2 import Model, decode_mqtt_telemetry
from solix_gen2.protocol import DATA_RESPONSE, build_packet, tlv


def envelope(*, serial="SYNTHETIC", product="A1783", data=None, command="0421", status=0):
    # Synthetic battery, mains/output, and C2000 Standard-mode blocks.
    body = (tlv(0xa5, bytes([4, 25, 0, 90, 100]))
            + tlv(0xa7, bytes.fromhex("04015600015600"))
            + tlv(0xd9, bytes([4, 0, 0, 10, 90, 1, 4, 0])))
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
