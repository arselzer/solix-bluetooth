"""Firmware-derived C1000 Gen 2 telemetry without invented units or alarms."""

import pytest

from solix_link.protocol import Model, decode_telemetry, tlv


def packet(*, active=1, power=123, cached_power=600, error=27):
    a6 = b"\x04" + bytes(4) + power.to_bytes(2, "little") + bytes(3)
    a8 = bytes((4, active)) + cached_power.to_bytes(2, "little")
    a3 = bytes((4, 0, error)) + bytes(11)
    return tlv(0xA6, a6) + tlv(0xA8, a8) + tlv(0xA3, a3)


def test_c1000_gen2_dc_power_uses_full_a6_not_stale_incremental_a8():
    metrics, raw = decode_telemetry(packet(), Model.C1000_GEN2)
    assert metrics["dc_input_power_raw"] == 123
    assert metrics["dc_input_active"] == 1
    assert metrics["controller_error_code"] == 27
    assert "dc_input_power_w" not in metrics  # Physical scaling is still unverified.
    assert raw[0xA8][2:] == b"\x58\x02"  # 600 retained in synthetic incremental A8.
    metrics, _ = decode_telemetry(tlv(0xA8, bytes.fromhex("04017b00")), Model.C1000_GEN2)
    assert metrics["dc_input_active"] == 1 and "dc_input_power_raw" not in metrics


@pytest.mark.parametrize("active,power", [(0, 0), (0, 123), (1, 0), (1, 123)])
def test_dc_state_and_power_are_independent(active, power):
    metrics, _ = decode_telemetry(packet(active=active, power=power), Model.C1000_GEN2)
    assert metrics["dc_input_active"] == active
    assert metrics["dc_input_power_raw"] == power


@pytest.mark.parametrize("tag,length,key", [(0xA3, 14, "controller_error_code"),
                                          (0xA6, 10, "dc_input_power_raw"),
                                          (0xA8, 4, "dc_input_active")])
@pytest.mark.parametrize("kind", ["short", "extra", "wrong_type"])
def test_new_fields_require_complete_known_types(tag, length, key, kind):
    body = b"\x04" + bytes(length - 1)
    body = body[:-1] if kind == "short" else body + b"\x00" if kind == "extra" else b"\x03" + body[1:]
    assert key not in decode_telemetry(tlv(tag, body), Model.C1000_GEN2)[0]


def test_invalid_dc_state_is_not_coerced_to_connected():
    metrics, _ = decode_telemetry(packet(active=2), Model.C1000_GEN2)
    assert "dc_input_active" not in metrics
    assert metrics["dc_input_power_raw"] == 123


@pytest.mark.parametrize("model", [Model.C1000, Model.C300, Model.C2000_GEN2, None])
def test_a1763_new_mappings_are_not_inferred_for_other_profiles(model):
    metrics, _ = decode_telemetry(packet(), model)
    assert not {"dc_input_active", "dc_input_power_raw", "controller_error_code"} & metrics.keys()


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2, None])
def test_packed_health_value_is_retained_only_as_raw_data(model):
    metrics, _ = decode_telemetry(tlv(0xA5, bytes.fromhex("041900326400")), model)
    assert metrics["battery_health_raw"] == 100
    assert "battery_health" not in metrics


def test_original_c1000_health_mapping_remains_separate():
    metrics, _ = decode_telemetry(tlv(0xC3, b"\x01\x63"), Model.C1000)
    assert metrics["battery_health"] == 99
    assert "battery_health_raw" not in metrics
