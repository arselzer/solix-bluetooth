"""C1000 Gen 2 lower limits preserve the separate C2000 model range."""

import importlib.util
from pathlib import Path
import time

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components/solix_link"
SPEC = importlib.util.spec_from_file_location("solix_power_range_api", BASE / "api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def station(model, protocol):
    return {"name": "Station", "model": model, "protocol": protocol, "connected": True, "available": True,
            "last_seen_timestamp": time.time(), "controls": ["set-charge-power"],
            "metrics": {"ac_charging_power_limit_w": 1000}}


@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
@pytest.mark.parametrize("watts", [100, 200, 1200])
def test_gen2_c1000_lower_power_range(protocol, watts):
    api.validate_command(station("c1000_gen2", protocol), {"command": "set-charge-power", "watts": watts})


@pytest.mark.parametrize("protocol", ["prime", "native_mqtt"])
@pytest.mark.parametrize("watts", [100, 200])
def test_c2000_retains_300w_minimum(protocol, watts):
    with pytest.raises(ValueError):
        api.validate_command(station("c2000_gen2", protocol), {"command": "set-charge-power", "watts": watts})


@pytest.mark.parametrize("watts", [0, 99, 150, 1201, True, 100.0])
def test_c1000_lower_range_rejects_non_step_and_non_integer_values(watts):
    with pytest.raises(ValueError):
        api.validate_command(station("c1000_gen2", "native_mqtt"), {"command": "set-charge-power", "watts": watts})
