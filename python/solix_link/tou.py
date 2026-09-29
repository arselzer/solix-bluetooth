"""Validated native Time-of-Use schedules and observed AC power flow."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

TARIFF_VALUES = {"peak": 1, "mid_peak": 2, "off_peak": 3}


@dataclass(frozen=True)
class TouPeriod:
    """One local-hour interval; split overnight ranges at midnight.

    Value 3 is called Off-Peak in the app map and SuperOffPeak in firmware.
    Whole-hour intervals and up to six slots follow recovered controller code.
    Only single, all-day slots have been exercised on the C2000 hardware.
    """

    tariff: str
    start_hour: int
    end_hour: int

    def __post_init__(self) -> None:
        if not isinstance(self.tariff, str) or self.tariff not in TARIFF_VALUES:
            raise ValueError("Tariff must be peak, mid_peak or off_peak")
        if (type(self.start_hour) is not int or type(self.end_hour) is not int
                or not 0 <= self.start_hour < self.end_hour <= 24):
            raise ValueError("Period must have integer hours 0 <= start < end <= 24")

    def to_bytes(self) -> bytes:
        return bytes((TARIFF_VALUES[self.tariff], self.start_hour, self.end_hour))

    def to_dict(self) -> dict:
        return {"tariff": self.tariff, "start_hour": self.start_hour, "end_hour": self.end_hour}


def validate_periods(periods: Iterable[TouPeriod]) -> tuple[TouPeriod, ...]:
    try:
        result = tuple(periods)
    except TypeError:
        raise ValueError("Schedule must contain TouPeriod values") from None
    if len(result) > 6 or any(not isinstance(p, TouPeriod) for p in result):
        raise ValueError("Schedule must contain at most six TouPeriod values")
    ordered = sorted(result, key=lambda p: p.start_hour)
    if any(left.end_hour > right.start_hour for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Schedule periods must not overlap")
    return result


def periods_from_d9(value: bytes) -> tuple[TouPeriod, ...]:
    if (len(value) < 26 or value[0] != 4 or value[6] > 6
            or len(value) != 26 + 3 * value[6]):
        raise ValueError("Incomplete or invalid Time-of-Use status block")
    names = {v: k for k, v in TARIFF_VALUES.items()}
    try:
        periods = tuple(TouPeriod(names[value[i]], value[i + 1], value[i + 2])
                        for i in range(7, 7 + 3 * value[6], 3))
    except KeyError:
        raise ValueError("Unknown tariff in Time-of-Use status") from None
    return validate_periods(periods)


def power_flow(metrics: dict) -> str:
    """Classify a fresh sample; mode/tariff alone cannot confirm power flow."""
    if metrics.get("ac_output_enabled") != 1 or metrics.get("ac_input_connected") != 1:
        return "unknown"
    incoming, outgoing = metrics.get("ac_input_power_w"), metrics.get("ac_output_power_w")
    if type(incoming) is not int or type(outgoing) is not int or outgoing <= 0:
        return "unknown"
    if incoming == 0 and metrics.get("battery_status") == "discharging":
        return "battery"
    if incoming >= max(1, outgoing - 20) and metrics.get("battery_status") in ("idle", "charging"):
        return "grid"
    return "transitioning"


class PowerFlowTimeout(RuntimeError):
    """Settings may have changed, but the requested power flow was not confirmed."""

    def __init__(self, snapshot: dict) -> None:
        super().__init__("Grid power was not confirmed; inspect fresh status before retrying")
        self.snapshot = snapshot
