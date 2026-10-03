"""Read-only Gen 2 saved charging-power policy preview from cached telemetry."""

from __future__ import annotations

import math
import time


MAX_PREVIEW_BYTES = 4096
CONFIG_FIELDS = frozenset({
    "signal_mode", "armed", "command_latch", "latch_changed_at",
    "charging_watts", "idle_watts", "minimum_reserve", "cooldown",
    "price_start", "price_stop", "price_max_age",
    "export_start", "export_stop", "export_max_age",
})
SETTING_FIELDS = (
    "ac_charging_power_limit_w", "backup_reserve_percentage",
    "max_charge_percentage", "min_charge_percentage",
)
POWER_RANGES = {"c1000_gen2": (100, 1200), "c2000_gen2": (300, 1800)}
CHARGE_CAPS = frozenset({80, 85, 90, 95, 100})
DISCHARGE_FLOORS = frozenset({1, *range(5, 76, 5)})
MAX_TIMESTAMP = 253402300799


class ChargingPolicyRequestError(ValueError):
    """Invalid input with fixed text that cannot expose caller-supplied data."""

    def __init__(self) -> None:
        super().__init__("InvalidChargingPreview")


def _number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _timestamp(value: object) -> bool:
    return _number(value) and 0 <= value <= MAX_TIMESTAMP


def _integer(value: object, minimum: int, maximum: int, step: int = 1) -> bool:
    return type(value) is int and minimum <= value <= maximum and value % step == 0


def _parse_request(request: object) -> tuple[dict, dict]:
    if type(request) is not dict or set(request) != {"config", "signals"}:
        raise ChargingPolicyRequestError()
    config, signals = request["config"], request["signals"]
    if type(config) is not dict or set(config) != CONFIG_FIELDS:
        raise ChargingPolicyRequestError()
    mode = config["signal_mode"]
    if (type(mode) is not str or mode not in ("price", "export", "either")
            or type(config["armed"]) is not bool or type(config["command_latch"]) is not bool
            or not _timestamp(config["latch_changed_at"])
            or not _integer(config["cooldown"], 60, 3600)
            or not _integer(config["price_max_age"], 30, 86400)
            or not _integer(config["export_max_age"], 5, 300)
            or not _integer(config["charging_watts"], 100, 1800, 100)
            or not _integer(config["idle_watts"], 100, 1800, 100)
            or config["idle_watts"] > config["charging_watts"]
            or not _integer(config["minimum_reserve"], 5, 100, 5)
            or any(not _number(config[key]) or not -1000 <= config[key] <= 1000
                   for key in ("price_start", "price_stop"))
            or config["price_start"] >= config["price_stop"]
            or any(not _integer(config[key], 0, 20000) for key in ("export_start", "export_stop"))
            or config["export_stop"] >= config["export_start"]):
        raise ChargingPolicyRequestError()
    roles = {"price", "export"} if mode == "either" else {mode}
    if type(signals) is not dict or set(signals) != roles:
        raise ChargingPolicyRequestError()
    for role in roles:
        signal = signals[role]
        fields = {"value", "timestamp"} if role == "price" else {"value", "timestamp", "unit", "positive_means"}
        if (type(signal) is not dict or set(signal) != fields
                or not _number(signal["value"]) or not _timestamp(signal["timestamp"])):
            raise ChargingPolicyRequestError()
        if role == "export" and (signal["unit"] != "W" or signal["positive_means"] != "export"):
            raise ChargingPolicyRequestError()
    # Copy only validated primitives: no caller-owned dictionary is retained.
    return config.copy(), {role: signals[role].copy() for role in roles}


def _result(*, reasons: list[str], current: dict | None = None, age: float | None = None,
            decision: str = "blocked", desired: dict | None = None,
            proposed: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "dry_run": True,
        "commands_sent": 0,
        "eligible": decision != "blocked",
        "decision": decision,
        "reasons": reasons,
        "proposed_settings": proposed or [],
        "current_settings": current,
        "desired_settings": desired,
        "telemetry_age_seconds": round(age, 3) if age is not None else None,
    }


def preview_charging_policy(snapshot: object, request: object, *, now: float | None = None) -> dict:
    """Propose settings without commands, ownership claims or policy state changes.

    Request errors raise a fixed ChargingPolicyRequestError. Invalid cached
    telemetry/safety conditions return a blocked result. Selected external
    signals must pass before emergency reserve logic can influence the proposal.
    The caller must enforce MAX_PREVIEW_BYTES before decoding an HTTP body.
    """
    config, signals = _parse_request(request)
    now = time.time() if now is None else now
    if not _timestamp(now):
        return _result(reasons=["clock_invalid"])
    if type(snapshot) is not dict:
        return _result(reasons=["snapshot_invalid"])
    # Read only the required public fields. Never echo names, IDs or error text.
    station = snapshot.copy()
    model = station.get("model")
    if type(model) is not str or model not in POWER_RANGES:
        return _result(reasons=["unsupported_model"])
    if station.get("protocol") != "native_mqtt":
        return _result(reasons=["unsupported_transport"])
    metrics = station.get("metrics")
    if type(metrics) is not dict:
        return _result(reasons=["metrics_missing"])
    metrics = metrics.copy()
    reasons = []
    if not config["armed"]:
        reasons.append("policy_disarmed")
    if config["command_latch"]:
        reasons.append("command_latched")
    if now - config["latch_changed_at"] < config["cooldown"]:
        reasons.append("cooldown_active")
    if station.get("connected") is not True:
        reasons.append("station_disconnected")
    if station.get("available") is not True:
        reasons.append("station_unavailable")
    if station.get("error") is not None:
        reasons.append("station_error")
    seen = station.get("last_seen_timestamp")
    age = now - seen if _timestamp(seen) else None
    if age is None:
        reasons.append("telemetry_missing")
    elif age < -5:
        reasons.append("telemetry_future")
    elif age > 30:
        reasons.append("telemetry_stale")
    for field, wanted, reason in (
        ("ac_input_connected", 1, "mains_not_confirmed"),
        ("ac_output_enabled", 1, "ac_output_not_confirmed"),
        ("ac_fast_charge_enabled", 0, "fast_not_confirmed_off"),
    ):
        if type(metrics.get(field)) is not int or metrics[field] != wanted:
            reasons.append(reason)
    if metrics.get("usage_mode") != "standard":
        reasons.append("standard_mode_required")
    if metrics.get("active_tariff") != "none":
        reasons.append("no_active_tariff_required")
    battery = metrics.get("battery_percentage")
    if not _integer(battery, 0, 100):
        reasons.append("battery_invalid")
    minimum, maximum = POWER_RANGES[model]
    watts, reserve, cap, lower = (metrics.get(key) for key in SETTING_FIELDS)
    settings_valid = (
        _integer(watts, minimum, maximum, 100)
        and type(cap) is int and cap in CHARGE_CAPS
        and type(lower) is int and lower in DISCHARGE_FLOORS
        and _integer(reserve, 5, 100, 5) and lower + 5 <= reserve <= cap
    )
    current = {key: metrics[key] for key in SETTING_FIELDS} if settings_valid else None
    if not settings_valid:
        reasons.append("current_settings_invalid")
    if not minimum <= config["idle_watts"] <= config["charging_watts"] <= maximum:
        reasons.append("policy_power_outside_model_range")
    if settings_valid:
        reserve_minimum = max(5, math.ceil((lower + 5) / 5) * 5)
        if not reserve_minimum <= config["minimum_reserve"] <= cap:
            reasons.append("policy_reserve_outside_current_caps")
    for role in ("price", "export"):
        if role not in signals:
            continue
        signal_age = now - signals[role]["timestamp"]
        if signal_age < -5:
            reasons.append(role + "_signal_future")
        elif signal_age > config[role + "_max_age"]:
            reasons.append(role + "_signal_stale")
    if reasons:
        return _result(reasons=reasons, current=current, age=age)

    # All settings and selected signals have passed. No forecast or battery
    # power estimate is inferred from saved charging watts or AC input/output.
    active = watts > config["idle_watts"]
    cheap = "price" in signals and signals["price"]["value"] <= config["price_stop" if active else "price_start"]
    surplus = "export" in signals and signals["export"]["value"] >= config["export_stop" if active else "export_start"]
    emergency = battery < max(config["minimum_reserve"], reserve)
    desired_watts = config["charging_watts"] if cheap or surplus or emergency else config["idle_watts"]
    desired_reserve = max(config["minimum_reserve"], reserve)
    desired = {**current, "ac_charging_power_limit_w": desired_watts,
               "backup_reserve_percentage": desired_reserve}
    proposed = []
    decision_reasons = []
    if emergency:
        decision_reasons.append("below_effective_reserve")
    if cheap:
        decision_reasons.append("price_opportunity")
    if surplus:
        decision_reasons.append("export_opportunity")
    if not decision_reasons:
        decision_reasons.append("no_opportunity")
    if desired_reserve != reserve:
        proposed.append({"command": "set-backup-reserve", "reserve": desired_reserve})
        decision_reasons.append("reserve_raise_proposed")
    if desired_watts != watts:
        proposed.append({"command": "set-charge-power", "watts": desired_watts})
        decision_reasons.append("charging_power_change_proposed")
    if not proposed:
        decision_reasons.append("settings_already_match")
    decision = ("unchanged" if not proposed else "emergency" if emergency
                else "opportunity" if cheap or surplus else "idle")
    return _result(reasons=decision_reasons, current=current, age=age,
                   decision=decision, desired=desired, proposed=proposed)
