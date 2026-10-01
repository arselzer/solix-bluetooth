"""C1000 preference API contracts; no Home Assistant runtime is simulated."""

import importlib.util
import json
from pathlib import Path
import time

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_preferences_api", BASE / "api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def station(model="c1000", protocol="legacy", **changes):
    value = {"name": "Station", "model": model, "protocol": protocol, "connected": True, "available": True,
             "last_seen_timestamp": time.time(), "controls": list(api.COMMANDS),
             "metrics": {"temperature_unit_fahrenheit": 0, "ac_fast_charge_enabled": 0,
                         "ac_power_saving_mode_enabled": 0, "dc_power_saving_mode_enabled": 1,
                         "ac_input_connected": 1, "usage_mode": "standard", "active_tariff": "none"}}
    value.update(changes)
    return value


@pytest.mark.parametrize("model,protocol", [("c1000", "legacy"), ("c1000_gen2", "native_mqtt")])
@pytest.mark.parametrize("fahrenheit", [False, True])
def test_temperature_display_supported_profiles_and_semantic_bool(model, protocol, fahrenheit):
    snapshot = api.parse_snapshot(station(model, protocol))
    assert api.temperature_unit_supported(snapshot)
    api.validate_command(snapshot, {"command": "set-temperature-unit", "fahrenheit": fahrenheit})


@pytest.mark.parametrize("model,protocol", [("c1000", "legacy"), ("c1000", "prime"), ("c1000_gen2", "prime"), ("c1000_gen2", "native_mqtt")])
@pytest.mark.parametrize("enabled", [False, True])
def test_fast_charge_allowed_supported_profiles(model, protocol, enabled):
    snapshot = api.parse_snapshot(station(model, protocol))
    assert api.boolean_setting_supported(snapshot, "set-fast-charge")
    api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": enabled})


@pytest.mark.parametrize("enabled", [False, True])
def test_original_prime_fast_without_fabricated_mains_or_tariffs(enabled):
    snapshot = station("c1000", "prime")
    for key in ("ac_input_connected", "usage_mode", "active_tariff"):
        snapshot["metrics"].pop(key)
    api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": enabled})


@pytest.mark.parametrize("enabled", [False, True])
def test_original_native_fast_rejects_even_advertised_capability(enabled):
    snapshot = station("c1000", "native_mqtt")
    assert not api.boolean_setting_supported(snapshot, "set-fast-charge")
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": enabled})


@pytest.mark.parametrize("changes", [{"controls": []}, {"connected": False}, {"available": False},
                                     {"last_seen_timestamp": time.time() - 91}])
def test_original_prime_fast_requires_capability_and_fresh_telemetry(changes):
    with pytest.raises(ValueError):
        api.validate_command(station("c1000", "prime", **changes), {"command": "set-fast-charge", "enabled": True})


@pytest.mark.parametrize("bad", [None, True, 2, 0.0, "0"])
def test_original_prime_fast_requires_exact_binary_readback(bad):
    snapshot = station("c1000", "prime")
    snapshot["metrics"]["ac_fast_charge_enabled"] = bad
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": False})


@pytest.mark.parametrize("command", ["set-ac-power-saving", "set-dc-power-saving"])
@pytest.mark.parametrize("enabled", [False, True])
def test_original_smart_modes_use_semantic_boolean_values(command, enabled):
    snapshot = api.parse_snapshot(station())
    assert api.boolean_setting_supported(snapshot, command)
    api.validate_command(snapshot, {"command": command, "enabled": enabled})


@pytest.mark.parametrize("command", ["set-temperature-unit", "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving"])
@pytest.mark.parametrize("model,protocol", [("c2000_gen2", "prime"), ("c2000_gen2", "native_mqtt"), ("c300", "legacy")])
def test_preferences_never_enabled_on_c2000_or_c300(command, model, protocol):
    parameter = "fahrenheit" if command == "set-temperature-unit" else "enabled"
    with pytest.raises(ValueError):
        api.validate_command(station(model, protocol), {"command": command, parameter: True})


@pytest.mark.parametrize("command", ["set-ac-power-saving", "set-dc-power-saving"])
@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
def test_smart_modes_reject_gen2_even_if_capability_is_advertised(command, protocol):
    with pytest.raises(ValueError):
        api.validate_command(station("c1000_gen2", protocol), {"command": command, "enabled": True})


@pytest.mark.parametrize("command,parameter,metric", [
    ("set-temperature-unit", "fahrenheit", "temperature_unit_fahrenheit"),
    ("set-fast-charge", "enabled", "ac_fast_charge_enabled"),
    ("set-ac-power-saving", "enabled", "ac_power_saving_mode_enabled"),
    ("set-dc-power-saving", "enabled", "dc_power_saving_mode_enabled"),
])
@pytest.mark.parametrize("bad", [None, 2, True, 0.0, "0"])
def test_preferences_need_explicit_binary_readback(command, parameter, metric, bad):
    snapshot = station()
    snapshot["metrics"][metric] = bad
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": command, parameter: True})


@pytest.mark.parametrize("command", ["set-temperature-unit", "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving"])
@pytest.mark.parametrize("bad", [1, "on", None])
def test_preference_requests_require_actual_booleans(command, bad):
    parameter = "fahrenheit" if command == "set-temperature-unit" else "enabled"
    with pytest.raises(ValueError):
        api.validate_command(station(), {"command": command, parameter: bad})


@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
@pytest.mark.parametrize("mode,tariff", [("time_of_use", "peak"), ("standard", "peak"), (None, "none"), ("standard", None)])
def test_gen2_fast_enable_rejects_active_or_unknown_mode_but_disable_stays_possible(protocol, mode, tariff):
    snapshot = station("c1000_gen2", protocol)
    snapshot["metrics"].update(usage_mode=mode, active_tariff=tariff)
    with pytest.raises(ValueError, match="Standard"):
        api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": True})
    api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": False})


@pytest.mark.parametrize("mains", [0, None, "1", 2, True, 1.0])
def test_native_fast_charge_needs_explicit_connected_mains(mains):
    snapshot = station("c1000_gen2", "native_mqtt")
    snapshot["metrics"]["ac_input_connected"] = mains
    with pytest.raises(ValueError, match="mains"):
        api.validate_command(snapshot, {"command": "set-fast-charge", "enabled": False})


@pytest.mark.parametrize("command", ["set-temperature-unit", "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving"])
@pytest.mark.parametrize("changes", [{"controls": []}, {"available": False}, {"last_seen_timestamp": 0}])
def test_preference_commands_require_fresh_connection_and_capability(command, changes):
    parameter = "fahrenheit" if command == "set-temperature-unit" else "enabled"
    with pytest.raises(ValueError):
        api.validate_command(station(**changes), {"command": command, parameter: False})


def test_new_switch_translation_and_icon_keys_match():
    strings = json.loads((BASE / "strings.json").read_text())
    icons = json.loads((BASE / "icons.json").read_text())
    for key in ("fast_charge", "ac_power_saving", "dc_power_saving"):
        assert key in strings["entity"]["switch"] and key in icons["entity"]["switch"]
