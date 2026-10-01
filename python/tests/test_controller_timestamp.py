"""C1000 Gen 2 reported UTC: strict FE shape and no cross-model inference."""

import base64
import json

import pytest

from solix_link.native_mqtt import decode_mqtt_telemetry
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, decode_telemetry, tlv


METRIC = "controller_utc_timestamp_seconds"


@pytest.mark.parametrize("encoded,seconds", [
    (bytes.fromhex("0300000000"), 0),
    (bytes.fromhex("0301000000"), 1),
    (bytes.fromhex("0378563412"), 0x12345678),
    (bytes.fromhex("0300000080"), 2147483648),
    (bytes.fromhex("03ffffffff"), 4294967295),
])
def test_gen2_controller_timestamp_is_unsigned_little_endian(encoded, seconds):
    metrics, raw = decode_telemetry(tlv(0xFE, encoded), model=Model.C1000_GEN2)
    assert metrics[METRIC] == seconds
    assert raw[0xFE] == encoded


@pytest.mark.parametrize("encoded", [
    b"", b"\x03", bytes.fromhex("03010203"), bytes.fromhex("030102030405"),
    bytes.fromhex("0001020304"), bytes.fromhex("0101020304"), bytes.fromhex("0401020304"),
])
def test_timestamp_rejects_wrong_type_or_length_without_discarding_other_telemetry(encoded):
    payload = tlv(0xA5, bytes.fromhex("0419005a64")) + tlv(0xFE, encoded)
    metrics, raw = decode_telemetry(payload, model=Model.C1000_GEN2)
    assert METRIC not in metrics
    assert metrics["battery_percentage"] == 90
    assert raw[0xFE] == encoded


@pytest.mark.parametrize("model", [None, Model.C1000, Model.C300, Model.C2000_GEN2])
def test_controller_timestamp_is_not_inferred_for_other_models(model):
    encoded = bytes.fromhex("0378563412")
    metrics, raw = decode_telemetry(tlv(0xFE, encoded), model=model)
    assert METRIC not in metrics
    assert raw[0xFE] == encoded


def test_absent_or_truncated_timestamp_does_not_invent_a_value():
    for payload in (b"", bytes.fromhex("fe0503785634")):
        metrics, _raw = decode_telemetry(payload, model=Model.C1000_GEN2)
        assert METRIC not in metrics


@pytest.mark.parametrize("command", ["0900", "0421"])
def test_native_telemetry_exposes_reported_time_independently_of_envelope_time(command):
    body = tlv(0xFE, bytes.fromhex("0378563412"))
    if command == "0900":
        body = b"\x00" + body
    frame = build_packet(DATA_RESPONSE, bytes.fromhex(command), body)
    message = json.dumps({"head": {"timestamp": 999999999}, "payload": json.dumps({
        "pn": "A1763", "sn": "SYNTHETIC", "data": base64.b64encode(frame).decode(),
    })})
    result = decode_mqtt_telemetry(message, model=Model.C1000_GEN2, expected_serial="SYNTHETIC")
    assert result.metrics[METRIC] == 0x12345678
