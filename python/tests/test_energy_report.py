import base64
import json

import pytest

from solix_link.energy_report import REPORT_NAME, decode_energy_events, decode_energy_report
from solix_link.ap_service_config import APServiceConfig
from solix_link.ap_service import api_response


def varint(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes(out + bytes([value]))


def field(tag, value):
    if isinstance(value, bytes):
        return varint(tag * 8 + 2) + varint(len(value)) + value
    return varint(tag * 8) + varint(value)


def request(payload):
    return {"account": "private-account", "device_sn": "A1783SYNTHETIC001", "protobuf_name": REPORT_NAME,
            "events": [{"params": {"payload": base64.b64encode(payload).decode(), "secret": "private-value"}}]}


def test_groups_large_counters_and_private_fields_are_not_exported():
    payload = (field(1, b"private-serial") + field(18, field(3, 2**40 + 17) + field(4, 6))
               + field(19, field(1, 12) + field(7, 90)) + field(20, field(8, 88)) + field(21, field(2, 4))
               + varint(31 * 8 + 5) + bytes(4) + varint(32 * 8 + 1) + bytes(8))
    decoded = decode_energy_events(request(payload))[0]
    assert decoded["groups"]["time_of_use"]["ac_input_energy_raw"] == 2**40 + 17
    assert decoded["groups"]["standard"] == {"ac_output_duration_raw": 12, "dc_input_energy_raw": 90}
    assert decoded["groups"]["backup_variant_2"] == {"ac_charge_duration_raw": 4}
    assert not decoded["units_verified"]
    assert "private" not in json.dumps(decoded)


@pytest.mark.parametrize("payload", [b"", b"x" * 8193, b"\x80", b"\x00", b"\x0b", b"\x12\x05x",
                                     b"\x09x", b"\x08" + b"\xff" * 9 + b"\x02",
                                     field(18, 3), field(18, field(3, b"bad"))])
def test_malformed_protobuf_is_rejected(payload):
    with pytest.raises(ValueError):
        decode_energy_report(payload)


@pytest.mark.parametrize("changes", [{"protobuf_name": "unknown"}, {"events": []},
                                     {"events": [{"params": {"payload": "%%%"}}]},
                                     {"events": [{"params": {"payload": 10}}]}])
def test_invalid_event_envelope_is_rejected(changes):
    with pytest.raises(ValueError):
        decode_energy_events({**request(field(18, field(3, 1))), **changes})


def test_local_logging_ack_does_not_forward_and_rejects_wrong_device():
    config = APServiceConfig("ups", "wlan_ap", "phy9", "AT", "A1783SYNTHETIC001", "a" * 40)
    event = request(field(19, field(3, 150)))
    body, chunked = api_response("//equipment/logging/upload_pb_events", event, config, b"unused")
    assert json.loads(body)["code"] == 0 and not chunked
    with pytest.raises(ValueError):
        api_response("/equipment/logging/upload_pb_events", {**event, "device_sn": "other"}, config, b"unused")
