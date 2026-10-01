"""Strict passive decoding of the A1763 firmware's MPPT weak-light flag."""

import pytest

from solix_link.protocol import Model, decode_telemetry, tlv


def work_status(*, lock: int, active: int = 0) -> bytes:
    value = bytearray(14)
    value[0] = 4
    value[1] = active
    value[2] = 2  # Independent current error code.
    value[13] = lock
    return bytes(value)


@pytest.mark.parametrize("lock", [0, 1])
@pytest.mark.parametrize("active", [0, 1, 2])
def test_weak_light_lock_is_independent_of_battery_work_state(lock, active):
    payload = tlv(0xA3, work_status(lock=lock, active=active))
    metrics, raw = decode_telemetry(payload, model=Model.C1000_GEN2)
    assert metrics["pv_weak_light_locked"] == lock
    assert metrics["controller_error_code"] == 2
    assert metrics["battery_status"] == ("idle", "discharging", "charging")[active]
    assert raw[0xA3] == work_status(lock=lock, active=active)


@pytest.mark.parametrize("value", [
    b"", work_status(lock=1)[:-1], work_status(lock=1) + b"\0",
    b"\x03" + work_status(lock=1)[1:], work_status(lock=2), work_status(lock=255),
])
def test_unknown_or_incomplete_weak_light_shape_does_not_invent_state(value):
    metrics, _ = decode_telemetry(tlv(0xA3, value), model=Model.C1000_GEN2)
    assert "pv_weak_light_locked" not in metrics


@pytest.mark.parametrize("model", [Model.C1000, Model.C2000_GEN2, Model.C300])
def test_weak_light_interpretation_is_not_extended_to_other_models(model):
    metrics, _ = decode_telemetry(tlv(0xA3, work_status(lock=1)), model=model)
    assert "pv_weak_light_locked" not in metrics


def test_missing_status_does_not_imply_unlocked():
    metrics, _ = decode_telemetry(b"", model=Model.C1000_GEN2)
    assert "pv_weak_light_locked" not in metrics
