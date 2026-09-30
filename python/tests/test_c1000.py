"""Original C1000 synthetic fixtures and selected nonprivate live field values."""

import pytest

from solix_gen2.c1000 import c1000_setting
from solix_gen2.cli import main
from solix_gen2.config import DeviceConfig, save_config
from solix_gen2.mqtt_bridge import _supports_operation
from solix_gen2.protocol import Model, Session, decode_telemetry, parse_packet, parse_tlvs, tlv


def scalar(tag, value, width=2, *, signed=False):
    return tlv(tag, bytes(({1: 1, 2: 2, 4: 3}[width],)) + value.to_bytes(width, "little", signed=signed))


def test_original_c1000_identity_is_distinct_from_gen2():
    for name in ("Anker SOLIX C1000", "SOLIX C1000X", "Anker_A1761"):
        assert Model.from_name(name) is Model.C1000
    assert Model.from_name("Anker SOLIX C1000 Gen 2") is Model.C1000_GEN2
    assert Model.from_name("Anker_A1763") is Model.C1000_GEN2
    with pytest.raises(ValueError):
        Model.from_name("SOLIX C1000 Gen 3")
    session = Session(Model.C1000)
    assert session.protocol == "legacy" and session.owner_user_id is None
    assert parse_packet(session.start()).command.hex() == "0001"
    prime = Session(Model.C1000, protocol="prime")
    assert prime.protocol == "prime" and len(prime.owner_user_id) == 40
    assert parse_packet(prime.start()).command.hex() == "4001"


def test_original_c1000_decode_uses_reference_layout_not_browser_or_gen2_aliases():
    payload = b"".join([
        scalar(0xA4, 20), scalar(0xA5, 500), scalar(0xA6, 200),
        scalar(0xA7, 10), scalar(0xA8, 11), scalar(0xA9, 12), scalar(0xAA, 13),
        scalar(0xAB, 900), scalar(0xAF, 777), scalar(0xB0, 260),
        tlv(0xB2, b"\x04\x01\x0e\x00"), scalar(0xB3, 1234),
        scalar(0xBD, -2, signed=True), scalar(0xC1, 74, 1), scalar(0xC3, 99, 1),
        scalar(0xD1, 600), scalar(0xD3, 30), scalar(0xD7, 1, 1),
        scalar(0xD8, 1, 1), tlv(0xD0, b"\x00TESTC1000SERIAL"),
    ])
    metrics, raw = decode_telemetry(payload, Model.C1000)
    assert metrics["battery_percentage"] == 74
    assert metrics["temperature_c"] == -2
    assert metrics["software_version"] == "1.2.3.4"
    assert metrics["ac_input_power_w"] == 500
    assert metrics["output_power_w"] == 260
    assert metrics["dc_output_power_w"] == 14
    assert metrics["usb_a2_power_w"] == 13
    assert metrics["time_remaining_minutes"] == 120
    assert metrics["ac_charging_power_limit_w"] == 600
    assert metrics["display_timeout_seconds"] == 30
    assert metrics["serial_number"] == "TESTC1000SERIAL"
    assert "ac_input_connected" not in metrics
    assert "battery_status" not in metrics
    assert "input_power_w" not in metrics  # AF's reference meanings conflict.
    assert raw[0xAB] == b"\x02\x84\x03"
    malformed = tlv(0xC1, b"\x02\x32") + tlv(0xD0, b"\x00\xff")
    assert decode_telemetry(malformed, Model.C1000)[0] == {}


def test_original_c1000_three_digit_version_seen_on_hardware():
    # Sanitized A1761 B3 field from the 2026-09-30 live baseline.
    metrics, _ = decode_telemetry(bytes.fromhex("b303029700"), Model.C1000)
    assert metrics["software_version_code"] == 151
    assert metrics["software_version"] == "1.5.1"


@pytest.mark.parametrize("wire,raw,minutes", [
    ("a40302ffff", 65535, "unknown"),
    ("a40302900d", 3472, "unknown"),
    ("a40302b200", 178, 1068),
    ("a403029900", 153, 918),
])
def test_original_c1000_runtime_sentinel_and_live_battery_estimates(wire, raw, minutes):
    metrics, _ = decode_telemetry(bytes.fromhex(wire), Model.C1000)
    assert metrics["time_remaining_raw"] == raw
    assert metrics["time_remaining_minutes"] == minutes


@pytest.mark.parametrize("setting,value,command,typed,expected", [
    ("display_timeout", 60, "4046", "023c00", {"display_timeout_seconds": 60}),
    ("display_brightness", 2, "404c", "0102", {"display_brightness": 2}),
    ("display_enabled", True, "4052", "0101", {"display_enabled": 1}),
    ("light_mode", 3, "404f", "0103", {"light_mode": 3}),
    ("ac_charging_power", 500, "4044", "02f401", {"ac_charging_power_limit_w": 500}),
    ("ac_output_enabled", False, "404a", "0100", {"ac_output_enabled": 0}),
    ("dc_output_enabled", True, "404b", "0101", {"dc_output_enabled": 1}),
])
def test_original_c1000_controls_match_reference_shapes(setting, value, command, typed, expected):
    session = Session(Model.C1000)
    session.ready = True
    session._secret = bytes(range(32))
    packet = parse_packet(session.c1000_control_packet(setting, value))
    assert packet.command.hex() == command
    fields = parse_tlvs(session._crypt(packet.payload, False))
    assert set(fields) == {0xA1, 0xA2, 0xFE}
    assert fields[0xA1] == b"\x21"
    assert fields[0xA2].hex() == typed
    assert len(fields[0xFE]) == 5 and fields[0xFE][0] == 3
    assert c1000_setting(setting, value)[2] == expected
    with pytest.raises(ValueError, match="dedicated"):
        session.send_command(command, b"\xa1\x01\x21")


@pytest.mark.parametrize("setting,value", [
    ("display_timeout", True), ("display_timeout", 31), ("display_enabled", 1),
    ("ac_charging_power", 0), ("ac_charging_power", 1500), ("ac_charging_power", 550),
    ("display_brightness", 4), ("light_mode", -1), ("factory_reset", 1),
])
def test_original_c1000_control_validation(setting, value):
    with pytest.raises(ValueError):
        c1000_setting(setting, value)


@pytest.mark.parametrize("model", [Model.C300, Model.C1000_GEN2, Model.C2000_GEN2])
def test_experimental_original_controls_cannot_target_other_stations(model):
    session = Session(model)
    session.ready = True
    session._secret = bytes(range(32))
    with pytest.raises(RuntimeError, match="original C1000"):
        session.c1000_control_packet("ac_output_enabled", False)


def test_c1000_cli_rejects_bad_values_before_connecting_and_bridge_supports_settings(tmp_path):
    device = DeviceConfig("original", "AA:BB:CC:DD:EE:04", Model.C1000)
    path = tmp_path / "config.json"
    save_config([device], path)
    args = ["c1000-setting", "--name", "original", "--setting", "display_timeout",
            "--value", "31", "--config", str(path)]
    assert main(args) == 1  # Invalid setting fails before BLE import/connection.
    assert _supports_operation(device, "display_timeout")
    assert _supports_operation(device, "ac_charging_power")
    assert _supports_operation(device, "ac_output")
