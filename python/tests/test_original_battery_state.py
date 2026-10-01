"""Original BMS codes remain unsigned/raw and require the observed byte shape."""

import base64
import json

import pytest

from solix_link.native_mqtt import decode_mqtt_telemetry
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, decode_telemetry, tlv


@pytest.mark.parametrize("main,expansion", [(0, 0), (1, 2), (2, 1), (255, 255)])
def test_original_bms_byte_codes_do_not_assign_unverified_semantics(main, expansion):
    payload = tlv(0xBF, bytes((1, main))) + tlv(0xC0, bytes((1, expansion)))
    metrics, raw = decode_telemetry(payload, Model.C1000)
    assert metrics == {"battery_state_code": main, "expansion_battery_state_code": expansion}
    assert raw == {0xBF: bytes((1, main)), 0xC0: bytes((1, expansion))}


@pytest.mark.parametrize("invalid", [b"", b"\x01", b"\x02\x02\x00", b"\x03\x02\x00\x00\x00",
                                     b"\x00\x02", b"\x04\x02", b"\x01\x02\x00"])
@pytest.mark.parametrize("tag,name", [(0xBF, "battery_state_code"), (0xC0, "expansion_battery_state_code")])
def test_malformed_code_does_not_remove_other_telemetry(invalid, tag, name):
    payload = tlv(tag, invalid) + tlv(0xC1, b"\x01\x64")
    metrics, raw = decode_telemetry(payload, Model.C1000)
    assert name not in metrics and metrics["battery_percentage"] == 100
    assert raw[tag] == invalid


@pytest.mark.parametrize("model", [None, Model.C300, Model.C1000_GEN2, Model.C2000_GEN2])
def test_original_state_fields_do_not_cross_model_maps(model):
    metrics, _raw = decode_telemetry(tlv(0xBF, b"\x01\x02") + tlv(0xC0, b"\x01\x01"), model)
    assert "battery_state_code" not in metrics and "expansion_battery_state_code" not in metrics


@pytest.mark.parametrize("command", ["0405", "0840"])
def test_original_native_state_codes_are_decoded_from_power_status_only(command):
    body = tlv(0xBF, b"\x01\x02") + tlv(0xC0, b"\x01\xff")
    frame = build_packet(DATA_RESPONSE, bytes.fromhex(command), (b"\x00" if command == "0840" else b"") + body)
    message = json.dumps({"payload": json.dumps({"sn": "SYNTHETIC", "pn": "A1761",
                                                "data": base64.b64encode(frame).decode()})})
    decoded = decode_mqtt_telemetry(message, model=Model.C1000, expected_serial="SYNTHETIC")
    assert decoded.metrics == {"battery_state_code": 2, "expansion_battery_state_code": 255}
