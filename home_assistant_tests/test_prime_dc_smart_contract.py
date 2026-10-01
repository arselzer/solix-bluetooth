"""DC Smart standalone contracts; Home Assistant runtime is not installed."""

import importlib.util
from pathlib import Path
import time

import pytest

SPEC = importlib.util.spec_from_file_location("solix_prime_dc_smart", Path(__file__).resolve().parents[1] / "custom_components/solix_link/api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def station(**changes):
    value = {"name": "Original", "model": "c1000", "protocol": "prime", "connected": True,
             "available": True, "last_seen_timestamp": time.time(), "controls": ["set-dc-power-saving"],
             "metrics": {"dc_power_saving_mode_enabled": 1, "dc_output_enabled": 0}}
    value.update(changes)
    return value


@pytest.mark.parametrize("enabled", [False, True])
def test_prime_dc_smart_requires_advertisement_and_semantic_bool(enabled):
    snapshot = api.parse_snapshot(station())
    assert api.boolean_setting_supported(snapshot, "set-dc-power-saving")
    api.validate_command(snapshot, {"command": "set-dc-power-saving", "enabled": enabled})
    assert not api.boolean_setting_supported(snapshot, "set-ac-power-saving")


@pytest.mark.parametrize("dc_output", [1, None, True, 0.0, "0", 255])
@pytest.mark.parametrize("enabled", [False, True])
def test_prime_dc_smart_refuses_active_or_unknown_dc(dc_output, enabled):
    snapshot = station(metrics={"dc_power_saving_mode_enabled": 1, "dc_output_enabled": dc_output})
    assert not api.boolean_setting_supported(snapshot, "set-dc-power-saving")
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-dc-power-saving", "enabled": enabled})


@pytest.mark.parametrize("changes", [{"controls": []}, {"connected": False}, {"available": False},
                                     {"last_seen_timestamp": 0}, {"protocol": "native_mqtt"},
                                     {"model": "c1000_gen2"}, {"model": "c2000_gen2"}, {"model": "c300"}])
def test_prime_dc_smart_does_not_widen_native_or_other_models(changes):
    with pytest.raises(ValueError):
        api.validate_command(station(**changes), {"command": "set-dc-power-saving", "enabled": True})
