"""Original C1000/A1761 typed telemetry and controls.

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
        0xDD: "temperature_unit_fahrenheit",
        0xDE: "display_enabled",
        0xE5: "ac_fast_charge_enabled",
    }
    for tag, name in fields.items():
        decoded = number(tag, signed=tag in (0xBD, 0xBE))
        if decoded is not None and (tag != 0xDD or decoded in (0, 1)):
            metrics[name] = decoded

    # Main 1.5.9 getters/serializer emit byte BMS state codes; 1.7.1 captures
    # confirm this shape. Keep unknown codes raw, without inferring mains or
    # assigning charging/discharging labels from another model.
    for tag, name in ((0xBF, "battery_state_code"), (0xC0, "expansion_battery_state_code")):
        state = values.get(tag, b"")
        if len(state) == 2 and state[0] == 1:
            metrics[name] = state[1]

    modes = values.get(0xF8, b"")
    # Code151 reports type01 with two mode bytes. Code171 reports the
    # type04/21-byte structure reproduced from main1.5.9's F8 serializer.
    # Both start with DC/AC modes: 1=Normal / 2=Smart. Preserve the expanded
    # structure's remaining bytes as raw data; their meanings are separate.
    if (len(modes) == 3 and modes[0] == 1) or (len(modes) == 21 and modes[0] == 4):
        for offset, key in ((1, "dc_power_saving_mode_enabled"), (2, "ac_power_saving_mode_enabled")):
            if modes[offset] in (1, 2):
                metrics[key] = int(modes[offset] == 2)

    remaining = number(0xA4)
    if remaining is not None:
        metrics["time_remaining_raw"] = remaining
        # Live A1761: AC connected -> ffff, isolated supply -> 153..178.
        # The display saturates at 99.9 h. Publish an explicit unknown value
        # so a new unavailable estimate clears the last numeric reading.
        metrics["time_remaining_minutes"] = remaining * 6 if remaining <= 999 else "unknown"

    dc = values.get(0xB2, b"")
    if len(dc) == 4 and dc[0] == 4:
        metrics["dc_output_power_w"] = int.from_bytes(dc[2:4], "little")

    version = number(0xB3)
    if version is not None:
        metrics["software_version_code"] = version
        if 100 <= version <= 9999:
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
    "ac_charging_power", "ac_output_enabled", "dc_output_enabled", "device_timeout",
    "temperature_unit_fahrenheit", "fast_charge_enabled",
    "ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled",
)


def c1000_setting(setting: str, value: int | bool) -> tuple[str, bytes, dict[str, int]]:
    """Return a validated command body and telemetry expectation, without I/O.

    Live changes/restorations are documented for A1761 version code 151.
    Not every value or firmware version has been tested.
    """
    from .protocol import DEVICE_TIMEOUT_MINUTES, tlv

    definitions = {
        "device_timeout": ("4045", "device_timeout_minutes", 2, DEVICE_TIMEOUT_MINUTES),
        "display_timeout": ("4046", "display_timeout_seconds", 2, (20, 30, 60, 300, 1800)),
        "display_brightness": ("404c", "display_brightness", 1, (0, 1, 2, 3)),
        "display_enabled": ("4052", "display_enabled", 1, (False, True)),
        "light_mode": ("404f", "light_mode", 1, (0, 1, 2, 3, 4)),
        "ac_charging_power": ("4044", "ac_charging_power_limit_w", 2, tuple(range(100, 1001, 100))),
        "ac_output_enabled": ("404a", "ac_output_enabled", 1, (False, True)),
        "dc_output_enabled": ("404b", "dc_output_enabled", 1, (False, True)),
        "temperature_unit_fahrenheit": ("4050", "temperature_unit_fahrenheit", 1, (False, True)),
        "fast_charge_enabled": ("405e", "ac_fast_charge_enabled", 1, (False, True)),
        "ac_power_saving_mode_enabled": ("4077", "ac_power_saving_mode_enabled", 1, (False, True)),
        "dc_power_saving_mode_enabled": ("4076", "dc_power_saving_mode_enabled", 1, (False, True)),
    }
    if setting not in definitions:
        raise ValueError("Unsupported original C1000 setting")
    command, field, width, options = definitions[setting]
    expected_type = bool if setting.endswith("_enabled") or setting == "temperature_unit_fahrenheit" else int
    if type(value) is not expected_type or value not in options:
        raise ValueError(f"Invalid {setting}; expected {expected_type.__name__} in {options}")
    wire_value = int(value)
    typed = bytes((width,)) + wire_value.to_bytes(width, "little")
    return command, b"\xa1\x01\x21" + tlv(0xA2, typed), {field: int(value)}
