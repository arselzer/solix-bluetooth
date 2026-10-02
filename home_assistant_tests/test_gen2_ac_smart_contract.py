"""AC Smart uses its own off-output gate and both inactive timers."""
import pytest

from test_gen2_dc_smart_contract import api, station


def ac_station(**changes):
    snapshot = station(controls=["set-ac-power-saving"])
    snapshot["metrics"].update(ac_output_enabled=0, ac_power_saving_mode_enabled=0,
                               dc_output_enabled=1)
    snapshot["metrics"].update(changes)
    return snapshot


@pytest.mark.parametrize("enabled", [False, True])
def test_ac_smart_accepts_safe_baseline_without_requiring_dc_off(enabled):
    snapshot = api.parse_snapshot(ac_station())
    assert api.boolean_setting_supported(snapshot, "set-ac-power-saving")
    api.validate_command(snapshot, {"command": "set-ac-power-saving", "enabled": enabled})


@pytest.mark.parametrize("key,value", [
    ("software_version", "1.1.4.10"), ("software_version", None),
    ("ac_output_enabled", 1), ("ac_output_enabled", True),
    ("ac_output_timeout_seconds", 1), ("dc_output_timeout_seconds", 1),
    ("ac_output_timeout_seconds", None), ("dc_output_timeout_seconds", 0.0),
    ("ac_power_saving_mode_enabled", "unknown"),
])
def test_ac_smart_refuses_unknown_or_unsafe_state_in_both_directions(key, value):
    for enabled in (False, True):
        with pytest.raises(ValueError):
            api.validate_command(ac_station(**{key: value}),
                                 {"command": "set-ac-power-saving", "enabled": enabled})


@pytest.mark.parametrize("changes", [
    {"connected": False}, {"available": False}, {"controls": []},
    {"last_seen_timestamp": 0}, {"protocol": "prime"}, {"model": "c2000_gen2"},
])
def test_ac_smart_refuses_stale_transport_and_c2000(changes):
    snapshot = ac_station()
    snapshot.update(changes)
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-ac-power-saving", "enabled": True})
