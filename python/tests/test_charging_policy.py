"""Synthetic cached policy previews: no devices, HA service calls or commands."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from solix_link.charging_policy import ChargingPolicyRequestError, preview_charging_policy


NOW = 1000.0


def station(model="c1000_gen2"):
    return {
        "name": "PRIVATE-STATION", "model": model, "protocol": "native_mqtt",
        "connected": True, "available": True, "last_seen_timestamp": NOW, "error": None,
        "metrics": {
            "battery_percentage": 50, "ac_input_connected": 1, "ac_output_enabled": 1,
            "ac_fast_charge_enabled": 0, "usage_mode": "standard", "active_tariff": "none",
            "ac_charging_power_limit_w": 300, "backup_reserve_percentage": 10,
            "max_charge_percentage": 100, "min_charge_percentage": 1,
        },
    }


def request(mode="price"):
    signals = {
        "price": {"value": -0.01, "timestamp": NOW},
        "export": {"value": 700, "timestamp": NOW, "unit": "W", "positive_means": "export"},
    }
    return {
        "config": {
            "signal_mode": mode, "armed": True, "command_latch": False,
            "latch_changed_at": NOW - 300, "charging_watts": 1000, "idle_watts": 300,
            "minimum_reserve": 20, "cooldown": 180,
            "price_start": 0, "price_stop": 0.05, "price_max_age": 7200,
            "export_start": 600, "export_stop": 300, "export_max_age": 30,
        },
        "signals": signals if mode == "either" else {mode: signals[mode]},
    }


def preview(snapshot=None, body=None, **kwargs):
    return preview_charging_policy(station() if snapshot is None else snapshot,
                                   request() if body is None else body, now=NOW, **kwargs)


@pytest.mark.parametrize("model", ["c1000_gen2", "c2000_gen2"])
def test_opportunity_proposes_only_reserve_then_saved_power(model):
    snapshot, body = station(model), request()
    original = deepcopy((snapshot, body))
    result = preview(snapshot, body)
    assert result["decision"] == "opportunity"
    assert result["proposed_settings"] == [
        {"command": "set-backup-reserve", "reserve": 20},
        {"command": "set-charge-power", "watts": 1000},
    ]
    assert result["dry_run"] is True and result["commands_sent"] == 0
    assert result["desired_settings"]["max_charge_percentage"] == 100
    assert result["desired_settings"]["min_charge_percentage"] == 1
    assert (snapshot, body) == original


def test_idle_never_lowers_reserve_or_changes_cap_and_unchanged_has_no_proposal():
    snapshot, body = station(), request()
    snapshot["metrics"].update(ac_charging_power_limit_w=1000, backup_reserve_percentage=30,
                               max_charge_percentage=85)
    body["signals"]["price"]["value"] = 0.5
    result = preview(snapshot, body)
    assert result["decision"] == "idle"
    assert result["proposed_settings"] == [{"command": "set-charge-power", "watts": 300}]
    assert result["desired_settings"]["backup_reserve_percentage"] == 30
    assert result["desired_settings"]["max_charge_percentage"] == 85
    snapshot["metrics"]["ac_charging_power_limit_w"] = 300
    result = preview(snapshot, body)
    assert result["decision"] == "unchanged"
    assert result["eligible"] is True and result["proposed_settings"] == []


@pytest.mark.parametrize(("power", "price", "wanted"), [
    (300, 0, 1000), (300, 0.00001, 300), (600, 0.05, 1000), (600, 0.05001, 300),
])
def test_price_hysteresis_uses_current_saved_power(power, price, wanted):
    snapshot, body = station(), request()
    snapshot["metrics"]["ac_charging_power_limit_w"] = power
    body["signals"]["price"]["value"] = price
    assert preview(snapshot, body)["desired_settings"]["ac_charging_power_limit_w"] == wanted


@pytest.mark.parametrize(("power", "export", "wanted"), [
    (300, 600, 1000), (300, 599.99, 300), (600, 300, 1000), (600, 299.99, 300), (300, -200, 300),
])
def test_export_hysteresis_preserves_sign_and_watts(power, export, wanted):
    snapshot, body = station(), request("export")
    snapshot["metrics"]["ac_charging_power_limit_w"] = power
    body["signals"]["export"]["value"] = export
    result = preview(snapshot, body)
    assert result["desired_settings"]["ac_charging_power_limit_w"] == wanted
    assert "battery_power_w" not in result


def test_either_uses_or_opportunities_but_requires_both_fresh_signals():
    body = request("either")
    body["signals"]["price"]["value"] = 0.5
    assert preview(body=body)["decision"] == "opportunity"
    body["signals"]["export"]["timestamp"] = NOW - 30.001
    result = preview(body=body)
    assert result["decision"] == "blocked" and result["proposed_settings"] == []
    assert "export_signal_stale" in result["reasons"]


@pytest.mark.parametrize(("battery", "reserve", "minimum", "emergency"), [
    (19, 10, 20, True), (20, 10, 20, False), (29, 30, 20, True), (30, 30, 20, False),
])
def test_emergency_uses_effective_reserve_not_just_configured_minimum(battery, reserve, minimum, emergency):
    snapshot, body = station(), request()
    snapshot["metrics"].update(battery_percentage=battery, backup_reserve_percentage=reserve)
    body["config"]["minimum_reserve"] = minimum
    body["signals"]["price"]["value"] = 1
    result = preview(snapshot, body)
    assert ("below_effective_reserve" in result["reasons"]) == emergency
    assert result["desired_settings"]["ac_charging_power_limit_w"] == (1000 if emergency else 300)


def test_stale_or_missing_signal_cannot_be_overridden_by_emergency():
    snapshot, body = station(), request()
    snapshot["metrics"]["battery_percentage"] = 0
    body["config"]["price_max_age"] = 30
    body["signals"]["price"]["timestamp"] = NOW - 30.001
    result = preview(snapshot, body)
    assert result["decision"] == "blocked" and not result["proposed_settings"]
    body["signals"] = {}
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview(snapshot, body)


@pytest.mark.parametrize(("changes", "reason"), [
    ({"model": "c1000"}, "unsupported_model"), ({"model": "c300"}, "unsupported_model"),
    ({"model": ["c1000_gen2"]}, "unsupported_model"), ({"protocol": "prime"}, "unsupported_transport"),
    ({"connected": False}, "station_disconnected"), ({"connected": 1}, "station_disconnected"),
    ({"available": False}, "station_unavailable"), ({"available": "yes"}, "station_unavailable"),
    ({"error": "PRIVATE-ERROR-SECRET"}, "station_error"), ({"metrics": None}, "metrics_missing"),
    ({"last_seen_timestamp": None}, "telemetry_missing"), ({"last_seen_timestamp": True}, "telemetry_missing"),
    ({"last_seen_timestamp": "PRIVATE"}, "telemetry_missing"),
    ({"last_seen_timestamp": float("nan")}, "telemetry_missing"),
    ({"last_seen_timestamp": float("inf")}, "telemetry_missing"),
    ({"last_seen_timestamp": 10**400}, "telemetry_missing"),
    ({"last_seen_timestamp": NOW - 30.001}, "telemetry_stale"),
    ({"last_seen_timestamp": NOW + 5.001}, "telemetry_future"),
])
def test_snapshot_failures_block_without_private_reflection(changes, reason):
    snapshot = station()
    snapshot.update(changes)
    result = preview(snapshot)
    assert reason in result["reasons"]
    assert result["decision"] == "blocked" and result["proposed_settings"] == []
    assert result["dry_run"] is True and result["commands_sent"] == 0
    assert "PRIVATE" not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("age", [-5, 0, 30])
def test_inclusive_station_freshness_boundaries(age):
    snapshot = station()
    snapshot["last_seen_timestamp"] = NOW - age
    assert preview(snapshot)["eligible"] is True


@pytest.mark.parametrize(("metric", "value", "reason"), [
    ("ac_input_connected", 0, "mains_not_confirmed"),
    ("ac_input_connected", True, "mains_not_confirmed"),
    ("ac_output_enabled", 0, "ac_output_not_confirmed"),
    ("ac_fast_charge_enabled", 1, "fast_not_confirmed_off"),
    ("ac_fast_charge_enabled", False, "fast_not_confirmed_off"),
    ("usage_mode", "time_of_use", "standard_mode_required"),
    ("active_tariff", "peak", "no_active_tariff_required"),
    ("battery_percentage", 101, "battery_invalid"),
    ("battery_percentage", 50.5, "battery_invalid"),
    ("battery_percentage", True, "battery_invalid"),
    ("ac_charging_power_limit_w", 350, "current_settings_invalid"),
    ("ac_charging_power_limit_w", 1300, "current_settings_invalid"),
    ("backup_reserve_percentage", 5, "current_settings_invalid"),
    ("backup_reserve_percentage", 15.0, "current_settings_invalid"),
    ("max_charge_percentage", 81, "current_settings_invalid"),
    ("min_charge_percentage", 2, "current_settings_invalid"),
])
def test_authoritative_native_metrics_must_be_complete_and_valid(metric, value, reason):
    snapshot = station()
    snapshot["metrics"][metric] = value
    result = preview(snapshot)
    assert reason in result["reasons"] and not result["proposed_settings"]
    assert result["desired_settings"] is None


@pytest.mark.parametrize("metric", list(station()["metrics"]))
def test_missing_required_native_metric_never_uses_a_default(metric):
    snapshot = station()
    del snapshot["metrics"][metric]
    assert preview(snapshot)["decision"] == "blocked"


@pytest.mark.parametrize(("changes", "reason"), [
    ({"armed": False}, "policy_disarmed"), ({"command_latch": True}, "command_latched"),
    ({"latch_changed_at": NOW - 179.999}, "cooldown_active"),
    ({"latch_changed_at": NOW + 3}, "cooldown_active"),
    ({"minimum_reserve": 5}, "policy_reserve_outside_current_caps"),
    ({"charging_watts": 1800}, "policy_power_outside_model_range"),
])
def test_arming_latch_cooldown_and_model_caps_block_changes(changes, reason):
    body = request()
    body["config"].update(changes)
    original = deepcopy(body)
    result = preview(body=body)
    assert reason in result["reasons"] and not result["proposed_settings"]
    assert body == original


def test_cooldown_boundary_and_latch_are_not_an_execution_or_reset_path():
    body = request()
    body["config"]["latch_changed_at"] = NOW - 180
    assert preview(body=body)["eligible"] is True
    body["config"]["command_latch"] = True
    for _ in range(2):
        assert preview(body=body)["eligible"] is False
        assert body["config"]["command_latch"] is True


def test_c2000_limits_and_current_floor_reserve_coupling():
    snapshot, body = station("c2000_gen2"), request()
    body["config"]["idle_watts"] = 100
    assert "policy_power_outside_model_range" in preview(snapshot, body)["reasons"]
    body["config"].update(idle_watts=300, charging_watts=1800, minimum_reserve=25)
    snapshot["metrics"].update(min_charge_percentage=20, backup_reserve_percentage=25)
    assert preview(snapshot, body)["desired_settings"]["ac_charging_power_limit_w"] == 1800
    body["config"]["minimum_reserve"] = 20
    assert "policy_reserve_outside_current_caps" in preview(snapshot, body)["reasons"]


@pytest.mark.parametrize(("role", "age", "allowed"), [
    ("price", -5, True), ("price", -5.001, False), ("price", 7200, True), ("price", 7200.001, False),
    ("export", -5, True), ("export", -5.001, False), ("export", 30, True), ("export", 30.001, False),
])
def test_signal_freshness_uses_explicit_timestamp(role, age, allowed):
    # NOW is increased so the oldest valid price timestamp remains nonnegative.
    body, snapshot, now = request(role), station(), 10000.0
    body["config"]["latch_changed_at"] = now - 300
    snapshot["last_seen_timestamp"] = now
    body["signals"][role]["timestamp"] = now - age
    assert preview_charging_policy(snapshot, body, now=now)["eligible"] is allowed


@pytest.mark.parametrize(("field", "value"), [
    ("armed", 1), ("command_latch", "off"), ("latch_changed_at", True),
    ("cooldown", 59), ("cooldown", 3601), ("cooldown", 180.0),
    ("charging_watts", 1050), ("charging_watts", True), ("idle_watts", 0),
    ("minimum_reserve", 21), ("price_start", float("nan")),
    ("price_stop", float("inf")), ("price_start", True), ("price_start", 1001),
    ("price_max_age", 0), ("export_max_age", 301), ("export_start", 0),
    ("export_stop", -1), ("export_start", 20001), ("signal_mode", "PRIVATE-TOKEN"),
])
def test_invalid_config_raises_only_fixed_error(field, value):
    body = request()
    body["config"][field] = value
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview(body=body)


@pytest.mark.parametrize("value", [None, True, "PRIVATE-SIGNAL", float("nan"), float("inf"), 10**400])
def test_invalid_signals_are_rejected_even_during_emergency(value):
    snapshot, body = station(), request()
    snapshot["metrics"]["battery_percentage"] = 0
    body["signals"]["price"]["value"] = value
    with pytest.raises(ChargingPolicyRequestError, match="^InvalidChargingPreview$"):
        preview(snapshot, body)


@pytest.mark.parametrize(("field", "value"), [("unit", "kW"), ("unit", "kWh"),
    ("unit", " W"), ("positive_means", "import"), ("timestamp", "PRIVATE-TIME")])
def test_no_implicit_export_unit_sign_or_timestamp_conversion(field, value):
    body = request("export")
    body["signals"]["export"][field] = value
    with pytest.raises(ChargingPolicyRequestError):
        preview(body=body)


def test_unknown_fields_missing_roles_and_credentials_do_not_enter_the_contract():
    variants = [None, [], {"config": {}}, request(), request(), request(), request()]
    variants[3]["token"] = "PRIVATE-TOKEN"
    variants[4]["config"]["entity_id"] = "PRIVATE-ENTITY"
    variants[5]["signals"]["price"]["url"] = "https://private.invalid/secret"
    variants[6]["signals"]["export"] = {"value": 1, "timestamp": NOW}
    for body in variants:
        with pytest.raises(ChargingPolicyRequestError) as error:
            preview(body=body) if body is not None else preview_charging_policy(station(), body, now=NOW)
        assert str(error.value) == "InvalidChargingPreview"


def test_each_preview_recalculates_from_new_cache_and_signal_without_planned_state():
    snapshot, body = station(), request()
    first = preview(snapshot, body)
    assert len(first["proposed_settings"]) == 2
    # A hypothetical reserve update completed, then price changed. The second
    # preview must not keep the first power proposal or pretend it was applied.
    snapshot["metrics"]["backup_reserve_percentage"] = 20
    body["signals"]["price"]["value"] = 0.5
    second = preview(snapshot, body)
    assert second["decision"] == "unchanged"
    assert second["proposed_settings"] == []
    assert second["current_settings"]["ac_charging_power_limit_w"] == 300
    body["config"]["command_latch"] = True
    third = preview(snapshot, body)
    assert third["decision"] == "blocked" and not third["proposed_settings"]


def test_privacy_no_io_and_output_does_not_alias_inputs(monkeypatch):
    snapshot, body = station(), request()
    private = "PRIVATE-SYNTHETIC-IDENTITY"
    snapshot.update(name=private, owner_id=private, address=private, raw_tlvs={"secret": private})
    snapshot["metrics"].update(serial_number=private, password=private, ac_input_power_w=9000,
                               ac_output_power_w=8000, battery_status="charging")
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("Preview attempted file I/O"))
    result = preview(snapshot, body)
    assert private not in json.dumps(result, allow_nan=False)
    assert not any("battery_power" in key or "input_power" in key for key in result)
    result["current_settings"]["ac_charging_power_limit_w"] = 999
    result["desired_settings"]["backup_reserve_percentage"] = 99
    assert snapshot["metrics"]["ac_charging_power_limit_w"] == 300
    assert snapshot["metrics"]["backup_reserve_percentage"] == 10


@pytest.mark.parametrize("now", [True, "PRIVATE-CLOCK", float("nan"), float("inf"), 10**400, -1])
def test_invalid_clock_is_diagnostic_and_never_raises_raw_data(now):
    result = preview_charging_policy(station(), request(), now=now)
    assert result["reasons"] == ["clock_invalid"] and result["proposed_settings"] == []


def test_default_clock_is_injected_and_snapshot_not_live_polled(monkeypatch):
    monkeypatch.setattr("solix_link.charging_policy.time.time", lambda: NOW)
    assert preview_charging_policy(station(), request())["decision"] == "opportunity"


@pytest.mark.parametrize(("mode", "watts", "price", "export", "soc", "reserve"), [
    ("price", 300, 0, 0, 50, 10), ("price", 300, 0.001, 0, 50, 20),
    ("price", 600, 0.05, 0, 50, 20), ("price", 600, 0.051, 0, 50, 20),
    ("export", 300, 1, 600, 50, 20), ("export", 300, 1, 599, 50, 20),
    ("export", 600, 1, 300, 50, 20), ("export", 600, 1, 299, 50, 20),
    ("either", 300, 1, 700, 50, 20), ("either", 300, -1, 0, 50, 20),
    ("either", 300, 1, 0, 19, 20), ("either", 300, 1, 0, 20, 20),
])
def test_decision_matches_actual_blueprint_template(mode, watts, price, export, soc, reserve):
    yaml = pytest.importorskip("yaml")
    native = pytest.importorskip("jinja2.nativetypes")

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))
    root = Path(__file__).resolve().parents[2]
    blueprint = yaml.load((root / "blueprints/automation/solix_link/opportunistic_charging.yaml").read_text(), Loader=Loader)
    template = next(item["variables"]["desired_watts"] for item in blueprint["actions"]
                    if "desired_watts" in item.get("variables", {}))
    snapshot, body = station(), request(mode)
    snapshot["metrics"].update(ac_charging_power_limit_w=watts, battery_percentage=soc,
                               backup_reserve_percentage=reserve)
    for role, value in (("price", price), ("export", export)):
        if role in body["signals"]:
            body["signals"][role]["value"] = value
    values = {"power_entity": watts, "battery_entity": soc, "reserve_entity": reserve,
              "price_entity": price, "export_entity": export}
    variables = {**body["config"], "power": "power_entity", "battery": "battery_entity",
                 "reserve": "reserve_entity", "price_sensor": "price_entity", "export_sensor": "export_entity"}
    env = native.NativeEnvironment()
    env.globals["states"] = lambda entity: str(values[entity])
    expected = env.from_string(template).render(**variables)
    assert preview(snapshot, body)["desired_settings"]["ac_charging_power_limit_w"] == int(expected)
