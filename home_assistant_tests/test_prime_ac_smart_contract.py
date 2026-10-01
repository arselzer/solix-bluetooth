"""Original Prime AC Smart contracts; no HA runtime or station actions."""

import importlib.util
from pathlib import Path
import time

import pytest

SPEC = importlib.util.spec_from_file_location("solix_prime_ac_smart", Path(__file__).resolve().parents[1] / "custom_components/solix_link/api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def station(**changes):
    snapshot = {"name": "Original", "model": "c1000", "protocol": "prime", "connected": True,
                "available": True, "last_seen_timestamp": time.time(), "controls": ["set-ac-power-saving"],
                "metrics": {"ac_output_enabled": 0, "ac_output_timer_remaining_seconds": 0,
                            "ac_power_saving_mode_enabled": 1}}
    snapshot.update(changes)
    return snapshot


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
def test_ac_smart_accepts_semantic_boolean_and_preserves_timer_readback(enabled, protocol):
    snapshot = api.parse_snapshot(station(protocol=protocol))
    assert snapshot["metrics"]["ac_output_timer_remaining_seconds"] == 0
    assert api.boolean_setting_supported(snapshot, "set-ac-power-saving")
    api.validate_command(snapshot, {"command": "set-ac-power-saving", "enabled": enabled})


@pytest.mark.parametrize("metric,bad", [
    ("ac_output_enabled", 1), ("ac_output_enabled", None), ("ac_output_enabled", True),
    ("ac_output_enabled", 0.0), ("ac_output_timer_remaining_seconds", None),
    ("ac_output_timer_remaining_seconds", True), ("ac_output_timer_remaining_seconds", 0.0),
    ("ac_output_timer_remaining_seconds", "0"), ("ac_output_timer_remaining_seconds", 30),
    ("ac_power_saving_mode_enabled", None), ("ac_power_saving_mode_enabled", True),
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
def test_ac_smart_refuses_active_or_unknown_ac_timer_or_mode(metric, bad, enabled, protocol):
    snapshot = station(protocol=protocol)
    snapshot["metrics"][metric] = bad
    assert not api.boolean_setting_supported(snapshot, "set-ac-power-saving")
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-ac-power-saving", "enabled": enabled})


@pytest.mark.parametrize("changes", [{"controls": []}, {"connected": False}, {"available": False},
                                     {"last_seen_timestamp": time.time() - 91},
                                     {"model": "c1000_gen2"}, {"model": "c2000_gen2"}, {"model": "c300"}])
@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
def test_ac_smart_requires_fresh_original_capability(changes, protocol):
    with pytest.raises(ValueError):
        api.validate_command(station(protocol=protocol, **changes), {"command": "set-ac-power-saving", "enabled": False})


def test_gateway_contract_excludes_output_switch_even_when_advertised():
    snapshot = station(controls=["set-ac-output"])
    assert "set-ac-output" not in api.parse_snapshot(snapshot)["controls"]
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-ac-output", "enabled": False})
