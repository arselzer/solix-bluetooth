"""Passive C1000 Gen 2 disaster-preparation fields from the typed D9 tail."""

from __future__ import annotations


def decode_disaster_plan(value: bytes) -> dict[str, int | str]:
    """Decode the exact 1.1.4.9 layout; dormant automatic records are absent.

    Timestamps are the stored window bounds, not proof of correct clock sync.
    This function neither reconstructs a restorable plan nor sends a command.
    Call only for the C1000 Gen 2 model.
    """
    if len(value) < 26 or value[0] != 4 or value[6] > 6:
        return {}
    start = 7 + 3 * value[6]
    if len(value) != start + 19:
        return {}
    tail = value[start:]
    fields: dict[str, int | str] = {
        "disaster_preparation_mode": {0: "none", 1: "manual", 2: "automatic"}.get(tail[0], "unknown"),
    }
    if tail[0] in (0, 1, 2):
        fields["disaster_preparation_active"] = int(tail[0] != 0)
    for offset, name in ((1, "manual_backup_enabled"), (2, "storm_guard_enabled")):
        if tail[offset] in (0, 1):
            fields[name] = tail[offset]
    for offset, name in ((3, "manual_backup_start"), (7, "manual_backup_end"),
                         (11, "active_backup_start"), (15, "active_backup_end")):
        fields[f"{name}_timestamp_s"] = int.from_bytes(tail[offset:offset + 4], "little")
    return fields
