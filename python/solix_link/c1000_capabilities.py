"""Translate verified original settings into public interface names."""

from . import protocol


# Routing names are distinct from the MCU setting names. Inclusion here alone
# does not enable a control; the versioned protocol whitelist remains decisive.
C1000_PRIME_OPERATIONS = {
    "ac_charging_power": "ac_charging_power",
    "device_timeout": "device_timeout",
    "display_brightness": "display_brightness",
    "display_timeout": "display_timeout",
    "light_mode": "light_mode",
    "temperature_unit": "temperature_unit_fahrenheit",
}
C1000_PRIME_COMMAND_OPERATIONS = {
    "set-charge-power": "ac_charging_power",
    "set-device-timeout": "device_timeout",
    "set-display-brightness": "display_brightness",
    "set-display-timeout": "display_timeout",
    "set-light": "light_mode",
    "set-temperature-unit": "temperature_unit",
}
# Native writes require their own live verification, independent of BLE Prime.
# The private 2026-10-01 trial confirmed these six with restoration of all
# protected settings, three final samples and AC output remaining enabled.
C1000_NATIVE_SETTINGS = frozenset(("ac_charging_power", "device_timeout", "display_brightness",
                                    "display_timeout", "light_mode", "temperature_unit_fahrenheit"))


def original_prime_operation_supported(operation: str) -> bool:
    setting = C1000_PRIME_OPERATIONS.get(operation)
    return setting is not None and setting in protocol.C1000_PRIME_SETTINGS


def original_prime_commands() -> list[str]:
    return [command for command, operation in C1000_PRIME_COMMAND_OPERATIONS.items()
            if original_prime_operation_supported(operation)]


def original_native_commands() -> tuple[str, ...]:
    return tuple(command for command, operation in C1000_PRIME_COMMAND_OPERATIONS.items()
                 if C1000_PRIME_OPERATIONS[operation] in C1000_NATIVE_SETTINGS)
