"""Timeout API contracts; Home Assistant runtime is not exercised here."""

import importlib.util
import json
from pathlib import Path
import time

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_timeout_api", BASE / "api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def station(model="c1000_gen2", protocol="native_mqtt", baseline=0, **changes):
    result = {"name": "UPS", "model": model, "protocol": protocol, "connected": True, "available": True,
              "last_seen_timestamp": time.time(), "controls": ["set-device-timeout"],
              "metrics": {"device_timeout_minutes": baseline}}
    result.update(changes)
    return result


@pytest.mark.parametrize("model,protocol", [("c1000", "legacy"), ("c1000", "prime"), ("c1000_gen2", "prime"), ("c1000_gen2", "native_mqtt")])
@pytest.mark.parametrize("minutes", [0, 30, 60, 120, 240, 360, 720, 1440])
def test_timeout_supported_profiles_and_exact_choices(model, protocol, minutes):
    snapshot = api.parse_snapshot(station(model, protocol, baseline=720))
    assert snapshot["metrics"]["device_timeout_minutes"] == 720
    assert api.device_timeout_options(snapshot)[0] == "never"
    api.validate_command(snapshot, {"command": "set-device-timeout", "minutes": minutes})


@pytest.mark.parametrize("minutes", [True, False, 30.0, "30", -1, 1, 90, 1441])
def test_timeout_rejects_unknown_or_non_integer_write_values(minutes):
    with pytest.raises(ValueError):
        api.validate_command(station(), {"command": "set-device-timeout", "minutes": minutes})


@pytest.mark.parametrize("baseline", [True, False, 0.0, "0", None, -1, 90])
def test_timeout_requires_exact_valid_baseline_readback(baseline):
    snapshot = api.parse_snapshot(station(baseline=baseline))
    assert api.device_timeout_options(snapshot) == []
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-device-timeout", "minutes": 0})


@pytest.mark.parametrize("model,protocol", [("c300", "legacy"), ("c2000_gen2", "native_mqtt"),
                                           ("c2000_gen2", "prime"),
                                           ("c1000_gen2", "legacy"), ("c1000", "native_mqtt")])
def test_timeout_rejects_unsupported_station_profiles(model, protocol):
    snapshot = station(model, protocol)
    assert api.device_timeout_options(snapshot) == []
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-device-timeout", "minutes": 0})


@pytest.mark.parametrize("changes", [{"controls": []}, {"available": False}, {"connected": False},
                                     {"last_seen_timestamp": time.time() - 31}, {"last_seen_timestamp": None}])
def test_timeout_requires_advertised_capability_and_fresh_connection(changes):
    with pytest.raises(ValueError):
        api.validate_command(station(**changes), {"command": "set-device-timeout", "minutes": 0})


def test_timeout_rejects_extra_fields_and_translation_options_match_contract():
    with pytest.raises(ValueError):
        api.validate_command(station(), {"command": "set-device-timeout", "minutes": 0, "enabled": True})
    strings = json.loads((BASE / "strings.json").read_text())
    assert set(strings["entity"]["select"]["device_timeout"]["state"]) == set(api.DEVICE_TIMEOUT_OPTIONS)
    assert strings["entity"]["select"]["device_timeout"]["state"]["never"] == "Never"
