"""Read-only C1000 Gen 2 clock-screen status from typed DA telemetry.

Offsets are reproduced from main 1.1.4.9 instructions and retained telemetry.
No asset identifiers, asset URLs or write commands are exposed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import Model


def decode_clock_screen(value: bytes, *, model: Model | str) -> dict[str, int]:
    """Decode a complete typed DA value; reject unverified model layouts.

    Unknown flags remain raw. Brightness is the firmware's 5/20 LCD value,
    not a live brightness measurement. Times use controller-local minutes
    since midnight. A zero weekday mask has one-shot semantics.
    """
    from .protocol import Model

    if Model(model) != Model.C1000_GEN2:
        raise ValueError("Clock-screen telemetry supports C1000 Gen 2 only")
    if len(value) != 24 or value[0] != 4:
        return {}
    fields = {
        "clock_screen_enabled": (value[1] >> 7) & 1,
        "clock_screen_flags_raw": value[1],
        "clock_screen_theme_raw": value[1] & 0x0f,
        "clock_screen_transfer_status_raw": value[2],
        "clock_screen_format_flag_raw": value[11],
        "clock_screen_weekday_mask": value[16],
        "clock_screen_second_window_enabled": value[17],
        "clock_screen_first_brightness_flag_raw": value[18],
        "clock_screen_second_brightness_flag_raw": value[19],
    }
    for offset, name in ((12, "first_start"), (14, "first_end"),
                         (20, "second_start"), (22, "second_end")):
        fields[f"clock_screen_{name}_minute"] = int.from_bytes(value[offset:offset + 2], "little")
    for offset, name in ((18, "first"), (19, "second")):
        if value[offset] in (0, 1):
            fields[f"clock_screen_{name}_brightness"] = 20 if value[offset] else 5
    return fields
