"""Replay C1000 Gen 2 clock, plan storage and tariff selection offline.

Actual ARM instructions execute for 10/0026, app0090, offset/RTC helpers,
tariff selection, epoch conversion and D9/FE serialization. The inherited
harness substitutes persistence, logging, ticks, reply/refresh transport and
backup status. RTC registers and readiness/power gates are synthetic. No
device, capture, network, wall clock or hardware timer is accessed.
"""

from __future__ import annotations

import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0

from emulate_tou_controller_encoding import tlv
from emulate_tou_d9 import D9Machine
from replay_io import FIRMWARE_SHA256, ROOT

PEAK, OFF_PEAK = 1, 3
PLAN = ((PEAK, 0, 15), (OFF_PEAK, 15, 24))
UTC = int(datetime(2026, 9, 30, 14, 58, 5, tzinfo=timezone.utc).timestamp())


class ScheduleClockMachine(D9Machine):
    def __init__(self, *, old_offset: int = 0, rtc: int = UTC, ready: bool = True):
        super().__init__(rtc=rtc)
        self.uc.mem_write(0x20000768, struct.pack("<Ii", UTC - 300, old_offset))
        self.uc.mem_write(0x20001d4d, struct.pack("<i", old_offset))
        self.uc.mem_write(0x40002804, struct.pack("<I", 0x20 if ready else 0))
        self.selecting = False
        self.selector_writes: list[tuple[int, int]] = []
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.record_write)

    def record_write(self, uc, access, address, size, value, data):
        if self.selecting:
            assert (0x2001e000 <= address and address + size <= 0x2001f000
                    or 0x20009aac <= address and address + size <= 0x20009ad0), hex(address)
            self.selector_writes.append((address, size))

    def sync(self, utc: int, offset: int):
        self.responses.clear()
        return super().sync(utc, offset)

    def set_plan(self, slots=PLAN, *, enabled: bool = True):
        body = (tlv(0xa1, b"\x22") + tlv(0xa2, bytes((1, int(enabled))))
                + tlv(0xa6, bytes((1, len(slots))))
                + tlv(0xa7, b"\x04" + b"".join(bytes(slot) for slot in slots)))
        stored = self.command(body)
        assert stored[:2 + 3 * len(slots)] == (
            bytes((int(enabled), len(slots))) + b"".join(bytes(slot) for slot in slots))

    def set_rtc(self, epoch: int):
        """Advance synthetic MMIO between calls; this is not a sync command."""
        self.uc.mem_write(0x40002818, struct.pack("<II", epoch >> 16, epoch & 0xffff))

    def observe(self, expected: int) -> dict:
        self.selector_writes.clear()
        self.selecting = True
        try:
            self.run(0x0801bcc4, 0)
            active = self.uc.reg_read(UC_ARM_REG_R0)
        finally:
            self.selecting = False
        assert active == expected, (active, expected)
        d9 = self.d9()
        assert d9[1] == expected, (d9.hex(), expected)
        cached_offset = struct.unpack("<i", self.uc.mem_read(0x2000076c, 4))[0]
        stored_offset = struct.unpack("<i", self.uc.mem_read(0x20001d4d, 4))[0]
        assert cached_offset == stored_offset
        fe = self.fe()
        assert fe == (self.rtc() + stored_offset) & 0xffffffff
        return {"rtc_epoch": self.rtc(), "rtc_hour": self.rtc() // 3600 % 24,
                "stored_offset_seconds_west": stored_offset, "fe_utc": fe,
                "active_tariff": active, "d9_prefix_hex": d9[:13].hex(),
                "selector_writes_only_stack_and_tm": True}


def run_cases() -> list[dict]:
    results = []

    def record(name, machine, expected, **inputs):
        results.append({"case": name, "inputs": inputs, **machine.observe(expected)})

    # UTC and radio's zero-zone sentinel both retain a previous controller offset.
    for old in (-7200, -3600, 0, 3600, 19800):
        for incoming in (0, -1):
            machine = ScheduleClockMachine(old_offset=old)
            machine.set_plan()
            machine.sync(UTC, incoming)
            expected = PEAK if (UTC - old) // 3600 % 24 < 15 else OFF_PEAK
            record(f"retained_{old}_incoming_{incoming}", machine, expected,
                   old_offset=old, incoming_offset=incoming, utc=UTC)
            assert machine.rtc() == UTC - old and machine.fe() == UTC
            assert machine.persistence_calls == 0

    for incoming in (-7200, -3600, 3600, 19800):
        machine = ScheduleClockMachine(old_offset=0)
        machine.set_plan()
        machine.sync(UTC, incoming)
        expected = PEAK if (UTC - incoming) // 3600 % 24 < 15 else OFF_PEAK
        record(f"nonzero_offset_{incoming}", machine, expected, incoming_offset=incoming)
        assert machine.persistence_calls == 1
        assert machine.rtc() == UTC - incoming and machine.fe() == UTC

    # A second, zero-offset sync cannot undo an earlier nonzero sync.
    machine = ScheduleClockMachine(old_offset=0)
    machine.set_plan()
    machine.sync(UTC - 30, -7200)
    machine.sync(UTC, 0)
    record("zero_after_nonzero_same_instance", machine, OFF_PEAK)
    assert machine.rtc() == UTC + 7200 and machine.fe() == UTC

    # Repeated calls cross and recross an hour boundary without reloading a plan.
    machine = ScheduleClockMachine()
    machine.set_plan()
    boundary = UTC - UTC % 3600 + 3600
    for label, delta, expected in (("before", -1, PEAK), ("at", 0, OFF_PEAK),
                                   ("after", 1, OFF_PEAK), ("rollback", -1, PEAK)):
        machine.set_rtc(boundary + delta)
        record(f"same_instance_boundary_{label}", machine, expected)

    # Changing the stored plan is immediately visible to selector and D9.
    machine.set_plan(((OFF_PEAK, 0, 24),))
    record("same_instance_changed_plan", machine, OFF_PEAK)
    machine.set_plan(PLAN, enabled=False)
    record("same_instance_disabled_plan", machine, 0)
    machine.set_plan(PLAN)
    record("same_instance_reenabled_plan", machine, PEAK)

    # A successful outer ACK does not guarantee RTC was written.
    old_rtc = boundary - 3 * 3600
    machine = ScheduleClockMachine(old_offset=-7200, rtc=old_rtc, ready=False)
    machine.set_plan()
    machine.sync(UTC, -1)
    record("rtc_not_ready_ack_but_old_hour", machine, PEAK)
    assert machine.rtc() == old_rtc and machine.responses == [b"\x00"]

    # Exercise every hour with one retained offset and one unmodified plan.
    machine = ScheduleClockMachine(old_offset=-7200)
    machine.set_plan()
    midnight = UTC - UTC % 86400
    for hour in range(24):
        machine.sync(midnight + hour * 3600, -1)
        local_hour = (hour + 2) % 24
        record(f"retained_offset_utc_hour_{hour:02}", machine,
               PEAK if local_hour < 15 else OFF_PEAK)
    return results


def main():
    results = run_cases()
    report = {
        "firmware_sha256": FIRMWARE_SHA256,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "harness_source_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("emulate_clock_semantics.py", "emulate_soc_cap_handler.py",
                         "emulate_tou_controller_encoding.py", "emulate_tou_d9.py", "replay_io.py")
        },
        "cases_passed": len(results), "real_hardware": False,
        "selector_function_substitutes": False,
        "peripheral_and_gate_inputs": "synthetic",
        "results": results,
    }
    path = ROOT / "schedule-clock-results.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("results", "harness_source_sha256")}))


if __name__ == "__main__":
    main()
