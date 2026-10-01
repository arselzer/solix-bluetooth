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
    "dc_power_saving": "dc_power_saving_mode_enabled",
    "fast_charge": "fast_charge_enabled",
}
C1000_PRIME_COMMAND_OPERATIONS = {
    "set-charge-power": "ac_charging_power",
    "set-device-timeout": "device_timeout",
    "set-display-brightness": "display_brightness",
    "set-display-timeout": "display_timeout",
    "set-light": "light_mode",
    "set-temperature-unit": "temperature_unit",
    "set-dc-power-saving": "dc_power_saving",
    "set-fast-charge": "fast_charge",
}
# Native writes require their own live verification, independent of BLE Prime.
# Independent 2026-10-01 native trials confirmed these seven with restoration;
# DC Smart additionally requires fresh DC output OFF and protects the F8 tail.
C1000_NATIVE_SETTINGS = frozenset(("ac_charging_power", "device_timeout", "display_brightness",
                                    "display_timeout", "light_mode", "temperature_unit_fahrenheit", "dc_power_saving_mode_enabled"))
ORIGINAL_DC_SMART_WARNING = ("Requires fresh DC output OFF. Smart may inherit an inactivity counter and later "
                          "turn the DC output off at low load; enabling does not guarantee a new grace period.")
ORIGINAL_FAST_CHARGE_WARNING = ("Use an adequate AC supply. The fast-charge flag may clear when AC input is removed; "
                               "stored readback does not establish charging speed or reboot persistence.")


def original_prime_operation_supported(operation: str) -> bool:
    setting = C1000_PRIME_OPERATIONS.get(operation)
    return setting is not None and setting in protocol.C1000_PRIME_SETTINGS


def original_prime_commands() -> list[str]:
    return [command for command, operation in C1000_PRIME_COMMAND_OPERATIONS.items()
            if original_prime_operation_supported(operation)]


def original_native_commands() -> tuple[str, ...]:
    return tuple(command for command, operation in C1000_PRIME_COMMAND_OPERATIONS.items()
                 if C1000_PRIME_OPERATIONS[operation] in C1000_NATIVE_SETTINGS)
