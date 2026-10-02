#!/usr/bin/env python3
"""Offline A1763 clock-screen readback and optional-field preservation.

Actual parser/0091, full280-byte stage/commit, DA/0092 serializers, display
helpers and one-shot callback execute. Inherited ACK/logging/persistence,
software timers, allocation and asset-transfer boundaries are substituted.
Synthetic settings, RTC and RAM only; no device, network or runtime control.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
import struct

from unicorn import UC_HOOK_MEM_WRITE, UC_PROT_EXEC, UC_PROT_READ
from unicorn.arm_const import UC_ARM_REG_PC

from emulate_clock_semantics import BUFFER, DESC, LENGTH
from emulate_timer_plan import ASSET, FLAGS, STAGED, STATUS, THEME, ClockScreenMachine, byte, local_epoch, short
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
SETTINGS = 0x20001d48
BACKUP = SETTINGS + 0x161


class PreservationMachine(ClockScreenMachine):
    def __init__(self, *, flags=0x73, first_enable=0, brightness=0, **kwargs):
        super().__init__(**kwargs)
        blob = bytearray((i * 37 + 19) & 255 for i in range(0x118))
        blob[0] = 0xa0 | first_enable | (brightness << 1)
        blob[2:6] = struct.pack("<HH", 480, 1380)
        blob[6] = 0xb1 | (brightness << 1)
        blob[8:12] = struct.pack("<HH", 1320, 360)
        blob[0x10] = flags
        blob[0x15] = 1
        blob[0x16] = 0x7f
        text = b"SYNTHETIC-CLOCK-TEXT\0"
        blob[0x17:0x17 + len(text)] = text
        self.uc.mem_write(THEME, bytes(blob))
        locator = b"SYNTHETIC-RESOURCE\0"
        self.uc.mem_write(ASSET, locator + bytes(range(256 - len(locator))))
        self.uc.mem_write(BACKUP, bytes(range(38)))
        self.uc.mem_protect(0x08000000, 0x40000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write_guard)

    def write_guard(self, uc, _access, address, size, _value, _data):
        assert (0x20000000 <= address < address + size <= 0x20020000
                or 0x21000000 <= address < address + size <= 0x21002000), (
                    "Non-synthetic instruction write", hex(address), hex(uc.reg_read(UC_ARM_REG_PC)))

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))

    def snapshot(self):
        return {"theme": self.read(THEME, 0x118), "saved": self.read(SETTINGS, 0x19f),
                "locator": self.read(ASSET, 256), "status": self.read(STATUS, 1)[0],
                "flags": int.from_bytes(self.read(FLAGS, 4), "little")}

    def check_changes(self, old, allowed):
        new = self.snapshot()
        changed = [i for i, (a, b) in enumerate(zip(old["theme"], new["theme"])) if a != b]
        assert set(changed) <= set(allowed)
        expected_saved = bytearray(old["saved"])
        start = THEME - SETTINGS
        expected_saved[start:start + 0x118] = new["theme"]
        assert new["saved"] == expected_saved
        assert new["locator"] == old["locator"] and new["status"] == old["status"]
        assert new["flags"] & ~(1 << 20) == old["flags"] & ~(1 << 20)
        assert not self.transfers
        return changed

    def da_mode(self, mode):
        self.uc.mem_write(BUFFER, bytes(128))
        self.uc.mem_write(LENGTH, bytes(2))
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBx", 4, BUFFER, LENGTH, 128, mode))
        self.run(0x08018730, DESC)
        raw = self.read(BUFFER, 25)
        assert raw[:2] == b"\x18\x04"
        self.protected()
        return raw[1:]


def main():
    firmware_image()
    rows = []

    def record(group, **values):
        rows.append({"group": group, **values})

    # Distinct complete configurations have exactly the same DA.
    for difference in ("first_enable", "text", "reserved_window_bits"):
        left, right = PreservationMachine(), PreservationMachine()
        before = right.read(THEME, 0x118)
        if difference == "first_enable":
            right.uc.mem_write(THEME, bytes((before[0] ^ 1,)))
        elif difference == "text":
            right.uc.mem_write(THEME + 0x17, b"OTHER-SYNTHETIC-TEXT\0")
        else:
            right.uc.mem_write(THEME, bytes((before[0] ^ 0x40,)))
            right.uc.mem_write(THEME + 6, bytes((before[6] ^ 0x40,)))
        assert left.read(THEME, 0x118) != right.read(THEME, 0x118)
        assert left.da() == right.da()
        record("da_collision", hidden_difference=difference, da_hex=left.da().hex(),
               da_is_not_complete_config=True)

    for flags, first, ready, busy in itertools.product(
            (0, 1, 2, 3, 0x73, 0x80, 0x81, 0x82, 0xf3), (0, 1), (False, True), (False, True)):
        m = PreservationMachine(flags=flags, first_enable=first, ready=ready, busy=busy)
        before = m.snapshot()
        before_da = m.da()
        target = flags ^ 0x80
        m.command({0xa2: byte(target)})
        m.check_changes(before, (0, 0x10))
        mid = m.read(THEME, 0x118)
        assert mid[0x10] == target and mid[0] & 1 == target >> 7
        m.command({0xa2: byte(flags)})
        changed = m.check_changes(before, (0,))
        expected = bytearray(before["theme"])
        expected[0] = (expected[0] & ~1) | (flags >> 7)
        assert m.read(THEME, 0x118) == expected
        assert m.da() == before_da
        assert bool(changed) == (first != flags >> 7)
        record("a2_roundtrip", flags=flags, first_enable=first, ready=ready, busy=busy,
               changed_after_restore=changed, da_restored=True,
               hidden_state_restored=not changed, opaque_assets_text_and_windows_preserved=True)

    # DA contains the exact affected selector/endpoints for these scalar writes.
    for tag, first, brightness, ready in itertools.product((0xac, 0xad), (0, 1), (0, 1), (False, True)):
        m = PreservationMachine(first_enable=first, brightness=brightness, ready=ready)
        before = m.snapshot()
        original_da = m.da()
        offset, da_offset = (0, 18) if tag == 0xac else (6, 19)
        original = original_da[da_offset]
        assert original == brightness
        m.command({tag: byte(1 - original)})
        m.check_changes(before, (offset,))
        expected = bytearray(before["theme"])
        expected[offset] = (expected[offset] & ~2) | ((1 - original) << 1)
        assert m.read(THEME, 0x118) == expected and m.da()[da_offset] == 1 - original
        m.command({tag: byte(original)})
        assert m.check_changes(before, ()) == [] and m.da() == original_da
        record("brightness_roundtrip", tag=f"{tag:02x}", first_enable=first,
               brightness=brightness, ready=ready, all_280_bytes_restored=True,
               unknown_assets_text_and_reserved_bits_preserved=True)

    endpoints = {0xa8: (2, 12, 600), 0xa9: (4, 14, 1200),
                 0xae: (8, 20, 60), 0xaf: (10, 22, 300)}
    for (tag, (offset, da_offset, target)), first, ready in itertools.product(
            endpoints.items(), (0, 1), (False, True)):
        m = PreservationMachine(first_enable=first, ready=ready)
        before, before_da = m.snapshot(), m.da()
        original = struct.unpack_from("<H", before_da, da_offset)[0]
        m.command({tag: short(target)})
        m.check_changes(before, (offset, offset + 1))
        expected = bytearray(before["theme"])
        expected[offset:offset + 2] = struct.pack("<H", target)
        assert m.read(THEME, 0x118) == expected
        assert struct.unpack_from("<H", m.da(), da_offset)[0] == target
        m.command({tag: short(original)})
        assert m.check_changes(before, ()) == [] and m.da() == before_da
        record("window_endpoint_roundtrip", tag=f"{tag:02x}", first_enable=first, ready=ready,
               original_minutes=original, temporary_minutes=target, all_280_bytes_restored=True)

    # Reachable hidden inconsistency from the actual one-shot callback.
    m = PreservationMachine(flags=0x80, first_enable=1, rtc=local_epoch(hour=8))
    m.uc.mem_write(THEME + 0x16, bytes(1))
    m.uc.mem_write(THEME + 4, struct.pack("<H", 1020))
    m.uc.mem_write(THEME + 6, bytes((m.read(THEME + 6, 1)[0] & ~1,)))
    assert m.timer()
    m.set_rtc(local_epoch(hour=17))
    assert not m.timer()
    assert m.read(THEME + 0x10, 1)[0] == 0 and m.read(THEME, 1)[0] & 1 == 1
    expired, expired_da = m.snapshot(), m.da()
    m.command({0xa2: byte(0x80)})
    m.command({0xa2: byte(0)})
    assert m.check_changes(expired, (0,)) == [0] and m.da() == expired_da
    record("reachable_one_shot_hidden_state", expired_flags=0, expired_first_enable=1,
           restored_flags=0, restored_first_enable=0, da_identical=True,
           complete_configuration_not_restored=True)

    # Any0091 request overwrites shared stage while a transfer is already pending.
    for tag in (0xa2, 0xac):
        m = PreservationMachine(ready=True)
        pending = bytearray(m.read(THEME, 0x118))
        pending[0x11:0x15] = b"NEW!"
        pending[0xc:0x10] = struct.pack("<I", 1234)
        m.uc.mem_write(STAGED, bytes(pending))
        m.uc.mem_write(STATUS, b"\x01")
        m.command({tag: byte(0xf3 if tag == 0xa2 else 1)})
        staged = m.read(STAGED, 0x118)
        assert staged == m.read(THEME, 0x118) and staged != pending
        assert staged[0x11:0x15] != b"NEW!"
        assert not m.transfers and m.read(STATUS, 1)[0] == 1
        m.asset_callback(True)
        assert m.read(THEME, 0x118) == staged and m.read(STATUS, 1)[0] == 0
        record("pending_stage_overwrite", tag=f"{tag:02x}", new_transfer_started=False,
               old_pending_metadata_discarded=True, callback_commits_replaced_stage=True,
               physical_transfer_not_executed=True)

    # DA rebuilds from RAM in all three modes and does not clear failure status.
    for mode, status in itertools.product((1, 2, 3), (0, 1, 2)):
        m = PreservationMachine()
        m.uc.mem_write(STATUS, bytes((status,)))
        before = m.snapshot()
        da = m.da_mode(mode)
        assert da == m.da() and da[2] == status
        assert m.snapshot() == before
        record("da_modes_nonclearing", serializer_mode=mode, transfer_status=status,
               da_hex=da.hex(), complete_saved_state_unchanged=True)

    for status in (0, 2):
        left, right = PreservationMachine(), PreservationMachine(first_enable=1)
        left.uc.mem_write(STATUS, bytes((status,)))
        right.uc.mem_write(STATUS, bytes((status,)))
        assert left.readback() == right.readback()
        assert left.read(STATUS, 1)[0] == right.read(STATUS, 1)[0] == 0
        record("query0092_hidden_collision", original_status=status,
               hidden_first_enable_not_exported=True, failure_status_cleared=status == 2)

    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256, "cases": len(rows),
              "hardware_access": False, "results": rows}
    path = ROOT / "gen2-clock-screen-preservation-results.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    path.chmod(0o600)
    sources = (Path(__file__).name, "emulate_timer_plan.py", "emulate_soc_cap_handler.py",
               "emulate_clock_semantics.py", "emulate_tou_controller_encoding.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows), "hardware_access": False,
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in sources}, "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "substitutes": ["ACK/logging/persistence", "software timers and normal-display timer query",
                        "allocation", "asset-transfer startup", "synthetic RTC/readiness/power gate"],
        "excluded": ["device/display rendering", "actual asset transfer", "radio/app/cloud",
                     "real timer/task scheduling", "flash", "electrical outputs and protections"]}
    manifest_path = ROOT / "gen2-clock-screen-preservation-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "hardware_access": False, "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
