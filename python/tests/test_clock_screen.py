"""Synthetic DA layout and model-boundary checks; no device identifiers."""

import pytest

from solix_link.clock_screen import decode_clock_screen
from solix_link.protocol import Model, decode_telemetry, tlv


VALUE = bytes.fromhex("048200616263647856341201e001fc035501010028056801")


def test_replayed_da_layout():
    decoded = decode_clock_screen(VALUE, model=Model.C1000_GEN2)
    assert decoded == {
        "clock_screen_enabled": 1,
        "clock_screen_flags_raw": 0x82,
        "clock_screen_theme_raw": 2,
        "clock_screen_transfer_status_raw": 0,
        "clock_screen_format_flag_raw": 1,
        "clock_screen_weekday_mask": 0x55,
        "clock_screen_second_window_enabled": 1,
        "clock_screen_first_brightness_flag_raw": 1,
        "clock_screen_second_brightness_flag_raw": 0,
        "clock_screen_first_start_minute": 480,
        "clock_screen_first_end_minute": 1020,
        "clock_screen_second_start_minute": 1320,
        "clock_screen_second_end_minute": 360,
        "clock_screen_first_brightness": 20,
        "clock_screen_second_brightness": 5,
    }


@pytest.mark.parametrize("model", [Model.C2000_GEN2, Model.C1000, Model.C300, "unknown"])
def test_unverified_models_rejected(model):
    with pytest.raises(ValueError):
        decode_clock_screen(VALUE, model=model)


@pytest.mark.parametrize("value", [b"", VALUE[:1], VALUE[:-1], VALUE + b"\0", b"\x03" + VALUE[1:]])
def test_incomplete_or_wrong_type_ignored(value):
    assert decode_clock_screen(value, model="c1000_gen2") == {}


def test_unknown_values_preserved_without_asset_metadata_or_invented_brightness():
    raw = bytearray(VALUE)
    raw[1] = 0xff
    raw[2] = 7
    raw[3:11] = bytes(8)
    raw[18] = 5
    decoded = decode_clock_screen(bytes(raw), model=Model.C1000_GEN2)
    assert decoded["clock_screen_flags_raw"] == 255
    assert decoded["clock_screen_theme_raw"] == 15
    assert decoded["clock_screen_transfer_status_raw"] == 7
    assert decoded["clock_screen_first_brightness_flag_raw"] == 5
    assert "clock_screen_first_brightness" not in decoded
    assert not any("asset" in name for name in decoded)


def test_clock_screen_metrics_flow_through_status_decoder():
    metrics, values = decode_telemetry(tlv(0xDA, VALUE), Model.C1000_GEN2)
    assert metrics == decode_clock_screen(VALUE, model=Model.C1000_GEN2)
    assert values[0xDA] == VALUE


def test_other_gen2_model_keeps_clock_screen_record_raw():
    metrics, values = decode_telemetry(tlv(0xDA, VALUE), Model.C2000_GEN2)
    assert not any(key.startswith("clock_screen_") for key in metrics)
    assert values[0xDA] == VALUE
