"""D9 tail offsets come from actual firmware serialization, not a write builder."""

import struct

import pytest

from solix_link.disaster_plan import decode_disaster_plan
from solix_link.protocol import Model, decode_telemetry, tlv


def block(count=0, kind=1, manual=1, automatic=0):
    prefix = bytes((4, 0, 0, 10, 100, 1, count)) + bytes((0, 24, 1)) * count
    return prefix + bytes((kind, manual, automatic)) + struct.pack("<4I", 1700000000, 1700003600, 1700000001, 1700003500)


@pytest.mark.parametrize("count", range(7))
def test_disaster_tail_follows_variable_tou_periods(count):
    metrics, _ = decode_telemetry(tlv(0xD9, block(count)), Model.C1000_GEN2)
    assert metrics["disaster_preparation_mode"] == "manual"
    assert metrics["disaster_preparation_active"] == 1
    assert metrics["manual_backup_enabled"] == 1 and metrics["storm_guard_enabled"] == 0
    assert metrics["manual_backup_start_timestamp_s"] == 1700000000
    assert metrics["manual_backup_end_timestamp_s"] == 1700003600
    assert metrics["active_backup_start_timestamp_s"] == 1700000001
    assert metrics["active_backup_end_timestamp_s"] == 1700003500


@pytest.mark.parametrize("value", [b"", block()[:-1], block() + b"\0", b"\x03" + block()[1:], block(7)])
def test_incomplete_or_unverified_layout_is_ignored(value):
    assert decode_disaster_plan(value) == {}


def test_unknown_mode_and_switches_do_not_invent_active_state():
    result = decode_disaster_plan(block(kind=3, manual=2, automatic=255))
    assert result["disaster_preparation_mode"] == "unknown"
    assert "disaster_preparation_active" not in result
    assert "manual_backup_enabled" not in result and "storm_guard_enabled" not in result


def test_other_gen2_model_keeps_backup_tail_raw():
    value = block()
    metrics, raw = decode_telemetry(tlv(0xD9, value), Model.C2000_GEN2)
    assert "disaster_preparation_mode" not in metrics and "storm_guard_enabled" not in metrics
    assert raw[0xD9] == value
