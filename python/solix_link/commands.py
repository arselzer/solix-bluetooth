"""Allowlisted gateway command shapes; protocol clients validate value ranges."""

from .tou import TouPeriod, validate_periods

COMMAND_FIELDS = {
    "set-charge-power": {"watts": int},
    "set-charge-cap": {"upper": int},
    "set-discharge-floor": {"lower": int},
    "set-backup-reserve": {"reserve": int},
    "set-tou-plan": {"periods": list, "enabled": bool},
    "return-grid": {"timeout": int},
    "set-display-timeout": {"seconds": int},
    "set-fast-charge": {"enabled": bool},
    "set-light": {"mode": int},
    "set-temperature-unit": {"fahrenheit": bool},
    "set-off-grid-alert": {"enabled": bool},
}
NATIVE_COMMANDS = ("set-charge-power", "set-charge-cap", "set-backup-reserve", "set-tou-plan", "return-grid")
NATIVE_C1000_COMMANDS = ("set-temperature-unit", "set-off-grid-alert", "set-discharge-floor")


def validate_command(command: str, values: dict) -> None:
    expected = COMMAND_FIELDS.get(command) if isinstance(command, str) else None
    if expected is None or set(values) != set(expected) or any(type(values[k]) is not kind for k, kind in expected.items()):
        raise ValueError("Unsupported command or invalid fields/types")
    if command == "set-tou-plan":
        periods = values["periods"]
        if len(periods) > 6:
            raise ValueError("Schedule has more than six periods")
        parsed = []
        for period in periods:
            if not isinstance(period, dict) or set(period) != {"tariff", "start_hour", "end_hour"}:
                raise ValueError("Invalid schedule period")
            parsed.append(TouPeriod(**period))
        validate_periods(parsed)
        if values["enabled"] and not parsed:
            raise ValueError("Enabled Time-of-Use requires a nonempty schedule")
    if command == "return-grid" and not 5 <= values["timeout"] <= 120:
        raise ValueError("Grid confirmation timeout must be 5–120 seconds")
