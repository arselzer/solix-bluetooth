"""Versioned original Prime preferences; standalone API/entity contracts only."""

import importlib.util
import json
from pathlib import Path
import time

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_original_prime_preferences", BASE / "api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def station(**changes):
    value = {"name": "Original", "model": "c1000", "protocol": "prime", "connected": True,
             "available": True, "last_seen_timestamp": time.time(),
             "controls": ["set-display-timeout", "set-light", "set-temperature-unit"],
             "metrics": {"display_timeout_seconds": 30, "light_mode": 0, "temperature_unit_fahrenheit": 0}}
    value.update(changes)
    return value


def test_original_prime_options_exact_payloads_and_translations():
    snapshot = api.parse_snapshot(station())
    assert api.display_timeout_options(snapshot) == ["20_seconds", "30_seconds", "1_minute", "5_minutes", "30_minutes"]
    assert api.light_mode_options(snapshot) == ["off", "low", "medium", "high", "sos"]
    assert api.temperature_unit_supported(snapshot)
    for command in ({"command": "set-display-timeout", "seconds": 60}, {"command": "set-light", "mode": 1},
                    {"command": "set-temperature-unit", "fahrenheit": True}):
        api.validate_command(snapshot, command)
    strings = json.loads((BASE / "strings.json").read_text())
    translations = json.loads((BASE / "translations/en.json").read_text())
    assert strings == translations
    assert set(strings["entity"]["select"]["light_mode"]["state"]) == set(api.LIGHT_MODE_OPTIONS)
    icons = json.loads((BASE / "icons.json").read_text())
    assert icons["entity"]["select"]["light_mode"]["default"] == "mdi:flashlight"


@pytest.mark.parametrize("seconds", [0, 10, 31, -1, True, 60.0, "60"])
def test_original_screen_timeout_rejects_native_only_and_invalid_choices(seconds):
    with pytest.raises(ValueError):
        api.validate_command(station(), {"command": "set-display-timeout", "seconds": seconds})


@pytest.mark.parametrize("mode", [True, -1, 5, 1.0, "1"])
def test_original_light_rejects_invalid_modes(mode):
    with pytest.raises(ValueError):
        api.validate_command(station(), {"command": "set-light", "mode": mode})


@pytest.mark.parametrize("metric", ["display_timeout_seconds", "light_mode"])
@pytest.mark.parametrize("bad", [None, True, "1", 1.0, 255])
def test_original_preferences_need_exact_valid_fresh_readback(metric, bad):
    value = station()
    value["metrics"][metric] = bad
    command = {"command": "set-light", "mode": 1} if metric == "light_mode" else {"command": "set-display-timeout", "seconds": 60}
    with pytest.raises(ValueError):
        api.validate_command(value, command)


@pytest.mark.parametrize("changes", [{"controls": []}, {"connected": False}, {"available": False},
                                     {"last_seen_timestamp": 0}, {"last_seen_timestamp": None}])
@pytest.mark.parametrize("payload", [{"command": "set-display-timeout", "seconds": 60},
                                     {"command": "set-light", "mode": 1},
                                     {"command": "set-temperature-unit", "fahrenheit": True}])
def test_original_preferences_need_capability_and_fresh_connection(changes, payload):
    with pytest.raises(ValueError):
        api.validate_command(station(**changes), payload)


@pytest.mark.parametrize("model,protocol", [("c1000_gen2", "prime"), ("c1000_gen2", "native_mqtt"),
                                           ("c2000_gen2", "prime"), ("c2000_gen2", "native_mqtt"),
                                           ("c300", "legacy"), ("c1000", "legacy")])
def test_light_select_does_not_expand_to_other_profiles(model, protocol):
    snapshot = station(model=model, protocol=protocol)
    assert not api.light_mode_options(snapshot)
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-light", "mode": 1})


def test_original_native_readonly_advertisement_has_no_configuration_controls():
    snapshot = station(protocol="native_mqtt", controls=[])
    assert not api.display_timeout_options(snapshot)
    assert not api.display_brightness_options(snapshot)
    assert not api.light_mode_options(snapshot)
    assert not api.temperature_unit_supported(snapshot)
    assert not api.device_timeout_options(snapshot)
    for payload in ({"command": "set-charge-power", "watts": 900},
                    {"command": "set-display-timeout", "seconds": 60},
                    {"command": "set-light", "mode": 1},
                    {"command": "set-temperature-unit", "fahrenheit": True}):
        with pytest.raises(ValueError):
            api.validate_command(snapshot, payload)


def test_original_native_interface_routes_only_advertised_preferences():
    # Synthetic interface contract; live native confirmation is recorded
    # separately and does not establish Home Assistant runtime compatibility.
    snapshot = station(protocol="native_mqtt")
    assert api.display_timeout_options(snapshot) == list(api.ORIGINAL_DISPLAY_TIMEOUT_OPTIONS)
    assert api.light_mode_options(snapshot) == list(api.LIGHT_MODE_OPTIONS)
    assert api.temperature_unit_supported(snapshot)
    for payload in ({"command": "set-display-timeout", "seconds": 60},
                    {"command": "set-light", "mode": 1},
                    {"command": "set-temperature-unit", "fahrenheit": True}):
        api.validate_command(snapshot, payload)
    snapshot["controls"] = list(api.COMMANDS)
    for payload in ({"command": "set-fast-charge", "enabled": True},
                    {"command": "set-ac-power-saving", "enabled": True},
                    {"command": "set-dc-power-saving", "enabled": True},
                    {"command": "set-port-memory", "enabled": True},
                    {"command": "set-charge-cap", "upper": 90},
                    {"command": "set-backup-reserve", "reserve": 10},
                    {"command": "return-grid", "timeout": 30}):
        with pytest.raises(ValueError):
            api.validate_command(snapshot, payload)
