"""Original C1000/A1761 support derived from reference maps, not hardware tested.

These typed TLVs differ from C300 and packed C1000 Gen 2 records. Keep
ambiguous fields raw rather than borrowing names from another device.
"""

from __future__ import annotations


def decode_c1000_telemetry(payload: bytes) -> tuple[dict[str, int | str], dict[int, bytes]]:
    from .protocol import parse_tlvs

    values = parse_tlvs(payload)
    metrics: dict[str, int | str] = {}

    def number(tag: int, *, signed: bool = False) -> int | None:
        value = values.get(tag, b"")
        if not value:
            return None
        width = {1: 1, 2: 2, 3: 4}.get(value[0])
        if width is None or len(value) != width + 1:
            return None
        return int.from_bytes(value[1:], "little", signed=signed)

    fields = {
        0xA2: "ac_output_timer_remaining_seconds",
        0xA3: "dc_output_timer_remaining_seconds",
        0xA5: "ac_input_power_w",
        0xA6: "ac_output_power_w",
        0xA7: "usb_c1_power_w",
        0xA8: "usb_c2_power_w",
        0xA9: "usb_a1_power_w",
        0xAA: "usb_a2_power_w",
        0xAE: "dc_input_power_w",
        0xB0: "output_power_w",
        0xBB: "ac_inverter_enabled",
        0xBC: "charging_source_code",
        0xBD: "temperature_c",
        0xBE: "expansion_temperature_c",
        0xC1: "battery_percentage",
        0xC2: "expansion_battery_percentage",
        0xC3: "battery_health",
        0xC4: "expansion_battery_health",
        0xC5: "expansion_battery_count",
        0xC6: "usb_c1_status",
        0xC7: "usb_c2_status",
        0xC8: "usb_a1_status",
        0xC9: "usb_a2_status",
        0xCC: "dc_output_active",
        0xD1: "ac_charging_power_limit_w",
        0xD2: "device_timeout_minutes",
        0xD3: "display_timeout_seconds",
        0xD7: "ac_output_enabled",
        0xD8: "dc_output_enabled",
        0xD9: "display_brightness",
        0xDC: "light_mode",
        0xDE: "display_enabled",
        0xE5: "ac_fast_charge_enabled",
    }
    for tag, name in fields.items():
        decoded = number(tag, signed=tag in (0xBD, 0xBE))
        if decoded is not None:
            metrics[name] = decoded

    remaining = number(0xA4)
    if remaining is not None:
        metrics["time_remaining_minutes"] = remaining * 6

    dc = values.get(0xB2, b"")
    if len(dc) == 4 and dc[0] == 4:
        metrics["dc_output_power_w"] = int.from_bytes(dc[2:4], "little")

    version = number(0xB3)
    if version is not None:
        metrics["software_version_code"] = version
        if 1000 <= version <= 9999:
            metrics["software_version"] = ".".join(str(version))

    serial = values.get(0xD0, b"")
    if len(serial) > 1 and serial[0] in (0, 4):
        try:
            text = serial[1:].rstrip(b"\x00").decode("ascii")
        except UnicodeDecodeError:
            pass
        else:
            if text and text.isalnum():
                metrics["serial_number"] = text
    return metrics, values


C1000_SETTINGS = (
    "display_timeout", "display_brightness", "display_enabled", "light_mode",
    "ac_charging_power", "ac_output_enabled", "dc_output_enabled",
)


def c1000_setting(setting: str, value: int | bool) -> tuple[str, bytes, dict[str, int]]:
    """Return a validated command body and telemetry expectation, without I/O.

    Original C1000 packet shapes are reference-derived and hardware untested.
    """
    from .protocol import tlv

    definitions = {
        "display_timeout": ("4046", "display_timeout_seconds", 2, (20, 30, 60, 300, 1800)),
        "display_brightness": ("404c", "display_brightness", 1, (0, 1, 2, 3)),
        "display_enabled": ("4052", "display_enabled", 1, (False, True)),
        "light_mode": ("404f", "light_mode", 1, (0, 1, 2, 3, 4)),
        "ac_charging_power": ("4044", "ac_charging_power_limit_w", 2, tuple(range(100, 1001, 100))),
        "ac_output_enabled": ("404a", "ac_output_enabled", 1, (False, True)),
        "dc_output_enabled": ("404b", "dc_output_enabled", 1, (False, True)),
    }
    if setting not in definitions:
        raise ValueError("Unsupported original C1000 setting")
    command, field, width, options = definitions[setting]
    expected_type = bool if setting.endswith("_enabled") else int
    if type(value) is not expected_type or value not in options:
        raise ValueError(f"Invalid {setting}; expected {expected_type.__name__} in {options}")
    typed = bytes((width,)) + int(value).to_bytes(width, "little")
    return command, b"\xa1\x01\x21" + tlv(0xA2, typed), {field: int(value)}
