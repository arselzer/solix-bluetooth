"""Telemetry for C300/C300X AC (A1722/A1723), separate from Gen 2 records.

The C300 DC and browser application's historical ``C300X_PARAMS`` use
different layouts. Neither is a fallback for this decoder.
"""

from __future__ import annotations


def decode_c300_telemetry(payload: bytes) -> tuple[dict[str, int | str], dict[int, bytes]]:
    """Decode C300 AC typed TLVs, preserving unknown or malformed values.

    Only complete values with the expected integer type width are decoded.
    AC input watts are not interpreted as mains presence: an idle station
    can draw no charging power while connected to the wall.
    """
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
        0xA9: "usb_c3_power_w",
        0xAA: "usb_a1_power_w",
        0xAB: "dc_output_power_w",
        0xAC: "solar_input_power_w",
        0xAD: "input_power_w",
        0xAE: "output_power_w",
        0xB7: "ac_output_enabled",
        0xB8: "dc_charging_status",
        0xB9: "temperature_c",
        0xBB: "battery_percentage",
        0xBD: "usb_c1_status",
        0xBE: "usb_c2_status",
        0xBF: "usb_c3_status",
        0xC0: "usb_a1_status",
        0xC1: "dc_output_enabled",
        0xC6: "ac_charging_power_limit_w",
        0xC8: "display_timeout_seconds",
        # Verified by 404f off -> low -> off. CF is the light bar, despite
        # the reference MQTT map's conflicting display-brightness label.
        0xCF: "light_mode",
    }
    for tag, name in fields.items():
        decoded = number(tag, signed=tag == 0xB9)
        if decoded is not None:
            metrics[name] = decoded

    # The reference implementation renders this decimal version code as
    # dotted digits. The tested C300X reports 1049 (1.0.4.9); preserve the
    # original value as well. Other component/version fields remain raw.
    version = number(0xB1)
    if version is not None:
        metrics["software_version_code"] = version
        if 1000 <= version <= 9999:
            metrics["software_version"] = ".".join(str(version))

    status = number(0xBA)
    if status is not None:
        work = {0: "idle", 1: "discharging", 2: "charging"}.get(status, "unknown")
        metrics["battery_status"] = work
        metrics["battery_discharging"] = int(work == "discharging")
        remaining = number(0xA4)
        if remaining is not None:
            metrics["time_remaining_minutes"] = remaining * 6 if status in (1, 2) else 0

    identity = values.get(0xC5, b"")
    if len(identity) > 1 and identity[0] in (0, 4):
        try:
            serial = identity[1:].rstrip(b"\x00").decode("ascii")
        except UnicodeDecodeError:
            pass
        else:
            if serial and serial.isalnum():
                metrics["serial_number"] = serial

    return metrics, values
