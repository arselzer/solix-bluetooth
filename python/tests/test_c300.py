"""Synthetic C300 AC fixtures; no captured device identifiers."""

from solix_gen2.c300 import decode_c300_telemetry
from solix_gen2.protocol import tlv


def scalar(tag: int, value: int, width: int = 2, *, signed: bool = False) -> bytes:
    return tlv(tag, bytes(({1: 1, 2: 2, 4: 3}[width],)) + value.to_bytes(width, "little", signed=signed))


def test_c300_ac_telemetry_uses_distinct_fields_from_gen2() -> None:
    payload = b"\x00" + b"".join([
        scalar(0xA4, 25), scalar(0xA5, 0), scalar(0xA6, 45),
        scalar(0xA7, 3), scalar(0xA8, 4), scalar(0xA9, 5),
        scalar(0xAA, 6), scalar(0xAB, 7), scalar(0xAC, 0),
        scalar(0xAD, 0), scalar(0xAE, 70), scalar(0xB1, 1049), scalar(0xB7, 1, 1),
        scalar(0xB9, -3, 1, signed=True), scalar(0xBA, 1, 1),
        scalar(0xBB, 73, 1), scalar(0xBC, 99, 1), scalar(0xC1, 1, 1),
        scalar(0xC6, 330), scalar(0xC8, 30), tlv(0xC5, b"\x00TESTSERIAL000001"),
        tlv(0xF8, b"\x04\x10\x20\x30"),
    ])
    metrics, raw = decode_c300_telemetry(payload)
    assert metrics["battery_percentage"] == 73
    assert "battery_health" not in metrics
    assert raw[0xBC] == b"\x01\x63"
    assert metrics["software_version"] == "1.0.4.9"
    assert metrics["software_version_code"] == 1049
    assert metrics["battery_status"] == "discharging"
    assert metrics["temperature_c"] == -3
    assert metrics["time_remaining_minutes"] == 150
    assert metrics["output_power_w"] == 70
    assert metrics["ac_output_power_w"] == 45
    assert metrics["usb_c1_power_w"] == 3
    assert metrics["usb_c2_power_w"] == 4
    assert metrics["usb_c3_power_w"] == 5
    assert metrics["usb_a1_power_w"] == 6
    assert metrics["dc_output_power_w"] == 7
    assert metrics["ac_output_enabled"] == metrics["dc_output_enabled"] == 1
    assert metrics["ac_charging_power_limit_w"] == 330
    assert metrics["display_timeout_seconds"] == 30
    assert metrics["serial_number"] == "TESTSERIAL000001"
    assert "ac_input_connected" not in metrics
    assert raw[0xF8] == b"\x04\x10\x20\x30"


def test_c300_rejects_incomplete_and_unknown_scalar_types() -> None:
    payload = b"".join([
        tlv(0xA5, b"\x02\x2a"), tlv(0xA6, b"\x03\x01\x02"),
        tlv(0xBB, b"\x04\x64"), tlv(0xBC, b"\x01\x64\x00"),
        tlv(0xC5, b"\x00\xff"),
    ])
    metrics, raw = decode_c300_telemetry(payload)
    assert metrics == {}
    assert raw[0xBB] == b"\x04\x64"


def test_c300_idle_and_unknown_status_do_not_report_runtime() -> None:
    for status, expected in [(0, "idle"), (9, "unknown")]:
        metrics, _ = decode_c300_telemetry(scalar(0xBA, status, 1) + scalar(0xA4, 65535))
        assert metrics["battery_status"] == expected
        assert metrics["time_remaining_minutes"] == 0
        assert metrics["battery_discharging"] == 0


def test_c300_missing_fields_remain_absent() -> None:
    metrics, _ = decode_c300_telemetry(scalar(0xBB, 82, 1))
    assert metrics == {"battery_percentage": 82}


def test_c300_usb_input_is_included_in_total_input_power() -> None:
    # Live C300X USB-C2 charging distinguished AD's total from A5's AC input.
    payload = b"".join([
        scalar(0xA5, 0), scalar(0xA8, 50), scalar(0xAD, 50),
        scalar(0xAE, 0), scalar(0xBE, 2, 1), scalar(0xBA, 2, 1),
    ])
    metrics, _ = decode_c300_telemetry(payload)
    assert metrics["ac_input_power_w"] == 0
    assert metrics["input_power_w"] == metrics["usb_c2_power_w"] == 50
    assert metrics["usb_c2_status"] == 2
    assert metrics["battery_status"] == "charging"
    assert metrics["output_power_w"] == 0


def test_c300_display_timeout_uses_c8_not_device_timer_c9() -> None:
    for seconds in (30, 60):
        metrics, raw = decode_c300_telemetry(scalar(0xC8, seconds) + scalar(0xC9, 120))
        assert metrics["display_timeout_seconds"] == seconds
        assert raw[0xC9] == b"\x02\x78\x00"


def test_c300_light_mode_is_distinct_from_unresolved_brightness() -> None:
    for mode in (0, 1, 2, 3):
        metrics, raw = decode_c300_telemetry(scalar(0xCD, 2, 1) + scalar(0xCF, mode, 1))
        assert metrics == {"light_mode": mode}
        assert raw[0xCD] == b"\x01\x02"
        assert raw[0xCF] == bytes((1, mode))


def test_c300_verified_control_readbacks_have_separate_fields() -> None:
    for ac, light, power in [(0, 0, 330), (1, 0, 330), (0, 1, 330), (0, 0, 300)]:
        payload = scalar(0xB7, ac, 1) + scalar(0xCF, light, 1) + scalar(0xC6, power)
        metrics, _ = decode_c300_telemetry(payload)
        assert metrics == {
            "ac_output_enabled": ac,
            "light_mode": light,
            "ac_charging_power_limit_w": power,
        }
