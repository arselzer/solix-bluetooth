"""Strict saved-setting telemetry over BLE decoding and native MQTT."""

import base64
import json

import pytest

from solix_link.native_mqtt import decode_mqtt_telemetry
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, decode_telemetry, tlv
from solix_link.tui import public_snapshot

FREQUENCY = "ac_output_frequency_setting_hz"
SMART = ("ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled")


def settings(frequency=50, ac_saving=0, dc_saving=1):
    block = bytearray(34)
    block[0], block[7], block[8], block[13] = 4, frequency, ac_saving, dc_saving
    return bytes(block)


@pytest.mark.parametrize("frequency", [50, 60])
@pytest.mark.parametrize("ac_saving,dc_saving", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_gen2_saved_settings_do_not_infer_output_or_mains_states(frequency, ac_saving, dc_saving):
    metrics, raw = decode_telemetry(tlv(0xA4, settings(frequency, ac_saving, dc_saving)), Model.C1000_GEN2)
    assert metrics[FREQUENCY] == frequency
    assert [metrics[key] for key in SMART] == [ac_saving, dc_saving]
    assert raw[0xA4] == settings(frequency, ac_saving, dc_saving)
    assert not {"ac_input_frequency_hz", "ac_input_connected", "ac_output_enabled"} & metrics.keys()
    assert all(key in public_snapshot({"metrics": metrics})["metrics"] for key in (FREQUENCY, *SMART))


@pytest.mark.parametrize("offset,key", [(7, FREQUENCY), (8, SMART[0]), (13, SMART[1])])
@pytest.mark.parametrize("invalid", [2, 49, 61, 255])
def test_invalid_complete_values_replace_earlier_valid_metrics_with_unknown(offset, key, invalid):
    block = bytearray(settings())
    block[offset] = invalid
    merged = decode_telemetry(tlv(0xA4, settings()), Model.C1000_GEN2)[0]
    merged.update(decode_telemetry(tlv(0xA4, bytes(block)), Model.C1000_GEN2)[0])
    assert merged[key] == "unknown"


@pytest.mark.parametrize("block", [b"", settings()[:14], settings()[:-1], settings() + b"\0", b"\x03" + settings()[1:]])
def test_new_fields_require_full_type04_a4(block):
    metrics, raw = decode_telemetry(tlv(0xA4, block), Model.C1000_GEN2)
    assert not {FREQUENCY, *SMART} & metrics.keys()
    assert raw[0xA4] == block


@pytest.mark.parametrize("model", [None, Model.C300, Model.C1000, Model.C2000_GEN2])
def test_saved_output_frequency_mapping_stays_model_specific(model):
    metrics, _ = decode_telemetry(tlv(0xA4, settings()), model)
    assert FREQUENCY not in metrics
    assert "ac_input_frequency_hz" not in metrics
    if model == Model.C2000_GEN2:
        assert metrics["ac_frequency_raw"] == 50


@pytest.mark.parametrize("model,product,key", [(Model.C1000_GEN2, "A1763", FREQUENCY),
                                             (Model.C2000_GEN2, "A1783", "ac_frequency_raw")])
@pytest.mark.parametrize("command", ["0900", "0421"])
def test_native_status_and_incremental_report_share_strict_readback(model, product, key, command):
    body = (b"\0" if command == "0900" else b"") + tlv(0xA4, settings(60))
    frame = build_packet(DATA_RESPONSE, bytes.fromhex(command), body)
    message = json.dumps({"head": {"cmd": 16}, "payload": json.dumps({
        "pn": product, "sn": "SYNTHETIC", "data": base64.b64encode(frame).decode(),
    })})
    result = decode_mqtt_telemetry(message, model=model, expected_serial="SYNTHETIC")
    assert result.metrics[key] == 60
    assert "ac_input_frequency_hz" not in result.metrics
