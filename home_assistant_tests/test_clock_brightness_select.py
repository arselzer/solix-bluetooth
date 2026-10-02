"""Inactive clock-window select contracts, using actual entity/API code offline.

Scoped HA doubles exercise discovery and action dispatch; they do not certify
HA runtime behavior or physical screen/output effects.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from home_assistant_tests.test_freshness_entities import (
    Description, NOW, coordinator, platform, snapshot,
)

ROOT = Path(__file__).resolve().parents[1]/"custom_components/solix_link"
KEYS = {1: "clock_screen_first_brightness_flag_raw", 2: "clock_screen_second_brightness_flag_raw"}


@pytest.fixture
def selects(platform, monkeypatch):
    select_module = ModuleType("homeassistant.components.select")
    select_module.SelectEntity = type("SelectEntity", (), {})
    select_module.SelectEntityDescription = Description
    monkeypatch.setitem(sys.modules, select_module.__name__, select_module)
    monkeypatch.setattr(sys.modules["homeassistant.const"].EntityCategory, "CONFIG", "config", raising=False)
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    monkeypatch.setitem(sys.modules, exceptions.__name__, exceptions)
    name = f"{platform.api.__package__}.select"
    spec = importlib.util.spec_from_file_location(name, ROOT/"select.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return SimpleNamespace(module=module, api=platform.api, error=exceptions.HomeAssistantError)


def station(**changes):
    result = snapshot(model="c1000_gen2", controls=["set-clock-brightness"], metrics={
        "software_version": "1.1.4.9", "usage_mode": "standard", "active_tariff": "none",
        "clock_screen_enabled": 0, "clock_screen_transfer_status_raw": 0,
        "ac_output_timeout_seconds": 0, "dc_output_timeout_seconds": 0,
        KEYS[1]: 0, KEYS[2]: 1,
    })
    result.update(changes)
    return result


def entity(selects, window, data=None, *, token="synthetic-token"):
    source = coordinator({"station": data or station()})
    source.client.token = token
    calls = []

    async def command(name, payload):
        selects.api.validate_command(source.data[name], payload)
        calls.append((name, payload))
        source.data[name]["metrics"][KEYS[payload["window"]]] = int(payload["high"])

    source.async_command = command
    description = next(item for item in selects.module.DESCRIPTIONS if item.key == KEYS[window])
    return selects.module.SolixSelect(source, "station", description), calls


@pytest.mark.parametrize("window", [1, 2])
@pytest.mark.parametrize("option,high", [("normal", False), ("high", True)])
def test_options_dispatch_the_selected_window_and_preserve_the_peer(selects, window, option, high):
    control, calls = entity(selects, window)
    peer_before = control.snapshot["metrics"][KEYS[3-window]]
    assert control.options == ["normal", "high"] and control.available
    assert control.current_option == ("normal" if window == 1 else "high")
    asyncio.run(control.async_select_option(option))
    assert calls == [("station", {"command": "set-clock-brightness", "window": window, "high": high})]
    assert control.current_option == option
    assert control.snapshot["metrics"][KEYS[3-window]] == peer_before
    assert control.extra_state_attributes["solix_link_role"] == KEYS[window]
    assert control.extra_state_attributes["solix_link_protocol"] == "native_mqtt"
    assert "does not enable clock" in control.extra_state_attributes["setting_scope"]
    assert control.entity_description.entity_registry_enabled_default is False


@pytest.mark.parametrize("key,value", [
    ("software_version", "1.1.4.3"), ("software_version", None),
    ("usage_mode", "time_of_use"), ("active_tariff", "peak"),
    ("clock_screen_enabled", 1), ("clock_screen_enabled", False),
    ("clock_screen_transfer_status_raw", 1), ("clock_screen_transfer_status_raw", None),
    ("ac_output_timeout_seconds", 60), ("dc_output_timeout_seconds", 60),
    (KEYS[1], None), (KEYS[2], None), (KEYS[1], 2), (KEYS[2], -1),
    (KEYS[1], True), (KEYS[2], 0.0),
])
def test_incomplete_unknown_or_active_clock_state_disables_both_selects(selects, key, value):
    data = station()
    data["metrics"][key] = value
    for window in (1, 2):
        control, calls = entity(selects, window, data)
        assert control.options == [] and control.current_option is None and not control.available
        with pytest.raises(selects.error):
            asyncio.run(control.async_select_option("high"))
        assert not calls


@pytest.mark.parametrize("changes", [{"model": "c2000_gen2"}, {"model": "c1000"}, {"protocol": "prime"},
                                     {"protocol": "legacy"}, {"controls": []}])
def test_wrong_model_transport_or_revoked_capability_never_dispatches(selects, changes):
    control, calls = entity(selects, 1, station(**changes))
    assert not control.options and not control.available
    with pytest.raises(selects.error):
        asyncio.run(control.async_select_option("high"))
    assert not calls


@pytest.mark.parametrize("changes", [{"last_seen_timestamp": NOW-31}, {"last_seen_timestamp": NOW+6},
                                     {"connected": False}, {"available": False}])
def test_freshness_and_connectivity_remain_authoritative(selects, changes):
    control, calls = entity(selects, 1, station(**changes))
    assert control.options == ["normal", "high"] and not control.available
    with pytest.raises(selects.error):
        asyncio.run(control.async_select_option("high"))
    assert not calls


@pytest.mark.parametrize("token,poll_ok", [("", True), ("synthetic-token", False)])
def test_read_only_or_failed_poll_blocks_setting_commands(selects, token, poll_ok):
    control, calls = entity(selects, 1, token=token)
    control.coordinator.last_update_success = poll_ok
    assert not control.available
    with pytest.raises(selects.error):
        asyncio.run(control.async_select_option("high"))
    assert not calls


@pytest.mark.parametrize("option", ["off", "auto", "1", 1, True, None])
def test_select_rejects_unknown_option_without_enabling_clock_or_retry(selects, option):
    control, calls = entity(selects, 1)
    with pytest.raises(selects.error):
        asyncio.run(control.async_select_option(option))
    assert not calls


def test_existing_select_rechecks_current_clock_state_and_preserves_role_identity(selects):
    control, calls = entity(selects, 1)
    unique_id = control._attr_unique_id
    control.coordinator.data["station"]["metrics"]["clock_screen_enabled"] = 1
    assert not control.available
    with pytest.raises(selects.error):
        asyncio.run(control.async_select_option("high"))
    assert not calls and control._attr_unique_id == unique_id


def test_discovery_adds_both_optional_selects_once_and_handles_removed_capability(selects):
    source = coordinator({"station": station()})
    source.client.token = "synthetic-token"
    entry = SimpleNamespace(runtime_data=source, async_on_unload=lambda callback: None)
    added = []
    asyncio.run(selects.module.async_setup_entry(None, entry, added.extend))
    assert {control.entity_description.key for control in added} == set(KEYS.values())
    source.listeners[0]()
    assert len(added) == 2
    source.data["station"]["controls"] = []
    assert all(not control.available for control in added)


def test_clock_select_translations_icons_and_current_options_match():
    strings = json.loads((ROOT/"strings.json").read_text())
    assert strings == json.loads((ROOT/"translations/en.json").read_text())
    icons = json.loads((ROOT/"icons.json").read_text())
    for key in ("clock_first_brightness", "clock_second_brightness"):
        assert set(strings["entity"]["select"][key]["state"]) == {"normal", "high"}
        assert key in icons["entity"]["select"]
