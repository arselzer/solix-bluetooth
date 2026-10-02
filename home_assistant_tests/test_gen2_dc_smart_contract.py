"""Native Gen 2 Smart controls require a fresh safe model-specific baseline."""
import time

import pytest

from test_prime_dc_smart_contract import api


def station(**changes):
    snapshot = {"name": "Synthetic", "model": "c1000_gen2", "protocol": "native_mqtt",
                "connected": True, "available": True, "last_seen_timestamp": time.time(),
                "controls": ["set-dc-power-saving"], "metrics": {
                    "software_version": "1.1.4.9", "dc_output_enabled": 0,
                    "dc_power_saving_mode_enabled": 0, "ac_output_timeout_seconds": 0,
                    "dc_output_timeout_seconds": 0}}
    snapshot.update(changes)
    return snapshot


@pytest.mark.parametrize("enabled", [False, True])
def test_safe_native_dc_smart_and_no_ac_extension(enabled):
    snapshot = api.parse_snapshot(station())
    assert api.boolean_setting_supported(snapshot, "set-dc-power-saving")
    api.validate_command(snapshot, {"command": "set-dc-power-saving", "enabled": enabled})
    assert not api.boolean_setting_supported(snapshot, "set-ac-power-saving")


@pytest.mark.parametrize("key,value", [("software_version", "1.1.4.10"), ("software_version", None),
                                     ("dc_output_enabled", 1), ("dc_output_enabled", True),
                                     ("ac_output_timeout_seconds", 1), ("dc_output_timeout_seconds", 1),
                                     ("ac_output_timeout_seconds", None), ("dc_output_timeout_seconds", 0.0),
                                     ("dc_power_saving_mode_enabled", "unknown")])
def test_unsafe_or_unknown_field_refuses_both_directions(key, value):
    snapshot = station()
    snapshot["metrics"][key] = value
    for enabled in (False, True):
        with pytest.raises(ValueError):
            api.validate_command(snapshot, {"command": "set-dc-power-saving", "enabled": enabled})


@pytest.mark.parametrize("changes", [{"connected": False}, {"available": False}, {"controls": []},
                                     {"last_seen_timestamp": 0}, {"protocol": "prime"},
                                     {"model": "c2000_gen2"}])
def test_no_stale_unsupported_transport_or_c2000(changes):
    with pytest.raises(ValueError):
        api.validate_command(station(**changes), {"command": "set-dc-power-saving", "enabled": True})
