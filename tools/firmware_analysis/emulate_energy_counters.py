#!/usr/bin/env python3
"""Offline A1763 energy arithmetic, timer gaps and protobuf snapshot replay.

Port-power RAM, timer records, clock values and backup states are synthetic.
Pre-update statistics, persistence, dynamic protobuf callbacks and sensor
diagnostics are substituted. SysTick registers are synthetic, with interrupt
priority/housekeeping substituted. No station, transport, flash or real clock runs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_SP,
)

from replay_io import FIRMWARE_SHA256, firmware_image

RAM, COUNTERS, PORTS = 0x20000000, 0x200032c0, 0x20002198
TICK, TIMER = 0x200008d0, 0x20007344
BUFFER, LENGTH, DESC, BACKUP = 0x21000000, 0x21000800, 0x21000900, 0x21000a00
STACK, STOP = 0x2001f000, 0x08005000
GROUP_TAGS = {0: 19, 1: 18, 2: 20, 3: 21}
FIELD_OFFSETS = {3: 0, 4: 16, 7: 8, 8: 24}
SUBSTITUTES = {
    0x0800fe44: "ancillary pre-update statistics",
    0x0802a9ac: "accounting persistence copy",
    0x08015c88: "persistence flush request",
    0x08016bb0: "dynamic protobuf callback: succeed without output",
    0x08018384: "battery diagnostics: synthetic zero",
    0x0801a6bc: "backup configuration diagnostic: synthetic zero",
    0x0801a460: "port diagnostic: synthetic zero",
}


class Machine:
    def __init__(self, *, group=0):
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000, 0x40000)
        self.uc.mem_write(0x08005000, firmware_image())
        self.uc.mem_protect(0x08000000, 0x40000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_map(RAM, 0x20000)
        self.uc.mem_map(BUFFER, 0x1000)
        self.uc.mem_map(0x40002000, 0x1000)  # Synthetic RTC registers only.
        self.uc.mem_map(0x40011000, 0x1000)  # Synthetic mains GPIO for A7 only.
        self.uc.mem_write(0x40002818, struct.pack("<II", 1700006400 >> 16, 1700006400 & 0xffff))
        self.uc.mem_write(0x200007af, b"\x01")
        self.uc.mem_write(0x200007b6, b"\x02")
        self.uc.mem_write(0x200004be, b"\x01")
        self.uc.mem_write(0x20000164, struct.pack("<I", 0x63))
        self.uc.mem_write(0x20001d76, bytes((group == 1, group == 1, 1, 0, 24)) + bytes(15))
        self.uc.mem_write(BACKUP+9, bytes((group == 3, group >= 2)))
        self.calls = []
        self.callbacks = 0
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write)

    def write(self, uc, access, address, size, value, data):
        if not (RAM <= address < address + size <= RAM+0x20000
                or BUFFER <= address < address + size <= BUFFER+0x1000):
            raise AssertionError(f"Unexpected write outside synthetic RAM: {address:08x}")

    def back(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def step(self, uc, address, size, data):
        if address == self.stop:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x08018aac:
            self.back(BACKUP)
        elif address in SUBSTITUTES:
            self.calls.append(SUBSTITUTES[address])
            self.back(1 if address == 0x08016bb0 else 0)
        elif address == 0x0802df3c:
            self.callbacks += 1
        elif not 0x08005000 <= address < 0x08034000:
            raise AssertionError(f"Unexpected instruction: {address:08x}")

    def run(self, address, argument=0, *, second=0, stop=STOP):
        self.stop, self.stopped = stop, False
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        self.uc.reg_write(UC_ARM_REG_R1, second)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=100000)
        assert self.stopped, f"Instruction limit at {self.uc.reg_read(UC_ARM_REG_PC):08x}"

    def powers(self, ac_in=0, ac_out=0, dc_in=0, other_out=0):
        # Execute the original indexed stores, not a substituted getter.
        for index, value in ((5, ac_in), (6, dc_in)):
            self.run(0x0802b778, index, second=value)
        for index, value in ((5, ac_out), (0, other_out)):
            self.run(0x0802b788, index, second=value)

    def tick(self, count=1):
        for _ in range(count):
            self.run(0x0802df3c)

    def sums(self, group=0):
        return list(struct.unpack("<4Q", self.uc.mem_read(COUNTERS+group*48, 32)))

    def report(self):
        before = bytes(self.uc.mem_read(COUNTERS, 0x118))
        self.run(0x0802db30, BUFFER, second=2048)
        count = self.uc.reg_read(UC_ARM_REG_R0)
        assert 0 < count < 2048
        body = bytes(self.uc.mem_read(BUFFER, count))
        assert bytes(self.uc.mem_read(COUNTERS, 0x118)) == before
        return {tag: parse_fields(value) for tag, value in parse_fields(body).items()
                if tag in GROUP_TAGS.values()}

    def timer_poll(self, now):
        self.uc.mem_write(TICK, struct.pack("<I", now & 0xffffffff))
        self.run(0x0801089c)

    def install_timer(self, started=0):
        # Actual firmware constructor stores this 20-byte timer shape.
        self.uc.mem_write(TIMER, struct.pack("<BB2xIIII", 2, 1, 1000, started, 0x0802df3d, 0))

    def ac_telemetry(self):
        self.uc.mem_write(LENGTH, bytes(2))
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBx", 4, BUFFER, LENGTH, 255, 1))
        self.run(0x08017ee0, DESC)
        value = bytes(self.uc.mem_read(BUFFER, 8))
        assert value[:2] == b"\x07\x04"
        return {"ac_input": int.from_bytes(value[6:8], "little"),
                "ac_output": int.from_bytes(value[3:5], "little")}


class SysTickMachine(Machine):
    def __init__(self, frequency):
        super().__init__()
        self.uc.mem_map(0xe000e000, 0x1000)
        self.uc.mem_write(0x200008cc, struct.pack("<I", frequency))
        self.register_writes = []

    def write(self, uc, access, address, size, value, data):
        if address in (0xe000e010, 0xe000e014, 0xe000e018) and size == 4:
            self.register_writes.append([f"{address:08x}", value])
        else:
            super().write(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        if address in (0x0800d78e, 0x08015a44):
            self.calls.append("SysTick priority" if address == 0x0800d78e else "SysTick housekeeping")
            self.back()
        else:
            super().step(uc, address, size, data)


def varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, position
    raise ValueError("Invalid protobuf varint")


def parse_fields(data):
    result, position = {}, 0
    while position < len(data):
        key, position = varint(data, position)
        tag, wire = key >> 3, key & 7
        assert tag
        if wire == 0:
            value, position = varint(data, position)
        elif wire == 2:
            size, position = varint(data, position)
            value = data[position:position+size]
            assert len(value) == size
            position += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = int.from_bytes(data[position:position+size], "little")
            position += size
        else:
            raise ValueError("Unknown protobuf wire type")
        result[tag] = value
    assert position == len(data)
    return result


def suite():
    results = {"systick": [], "power_sources": [], "report_division": [], "sampling": [],
               "scheduler": [], "duration_remainders": [], "delta_rounding": []}
    for frequency in (32000000, 64000000, 96000000, 120000000):
        m = SysTickMachine(frequency)
        m.run(0x0802d048)
        assert m.register_writes == [["e000e014", frequency//1000-1],
                                     ["e000e018", 0], ["e000e010", 7]]
        for _ in range(10):
            m.run(0x08010560)
        assert int.from_bytes(m.uc.mem_read(TICK, 4), "little") == 10
        results["systick"].append({"core_frequency_variable": frequency,
                                    "register_writes": m.register_writes,
                                    "interrupts": 10, "software_tick_increment": 10})
    for group in range(4):
        for ac_in, ac_out, dc_in, other in ((0, 0, 0, 0), (360, 270, 180, 90),
                                          (65535, 65535, 65535, 65535)):
            m = Machine(group=group)
            m.powers(ac_in, ac_out, dc_in, other)
            telemetry = m.ac_telemetry()
            m.tick(10)
            assert telemetry == {"ac_input": ac_in, "ac_output": ac_out}
            assert m.sums(group) == [ac_in, dc_in, ac_out, other]
            for other_group in set(range(4)) - {group}:
                assert m.sums(other_group) == [0]*4
            results["power_sources"].append({"group": group, "A7": telemetry,
                                              "sample_sums": m.sums(group)})
    values = [0, 1, 359, 360, 361, 719, 720, 721, 2**32-1, 2**32,
              360*(2**32-1), 360*2**32-1, 360*2**32, 360*2**32+360, 2**64-1]
    for value in values:
        m = Machine()
        for group in range(4):
            for offset in FIELD_OFFSETS.values():
                m.uc.mem_write(COUNTERS+group*48+offset, struct.pack("<Q", value))
        reports = m.report()
        expected = (value // 360) & 0xffffffff
        for fields in reports.values():
            assert all(fields[tag] == expected for tag in FIELD_OFFSETS)
        results["report_division"].append({"sum": value, "encoded": expected})
    for pattern in ([1000]*9+[0], [0]*9+[1000], [1]*10):
        m = Machine()
        for power in pattern:
            m.powers(power, power)
            m.tick()
        assert m.sums() == [pattern[-1], 0, pattern[-1], 0]
        results["sampling"].append({"ten_callback_powers": pattern, "sample_sum": m.sums()[0]})
    for power in (1, 7, 359, 360, 1000):
        m = Machine()
        m.powers(power, power)
        snapshots = []
        for callbacks in (90, 10, 490, 10, 3000):
            m.tick(callbacks)
            snapshots.append(m.report()[19][3])
        assert m.sums()[0] == power*360
        assert snapshots[-1] == power
        results["sampling"].append({"constant_power": power, "report_snapshots": snapshots,
                                     "callbacks": 3600, "sum": m.sums()[0]})
    for times in ([0, 999, 1000, 1999, 2000], [0, 1000, 9000, 9001, 9999, 10000],
                  list(range(0, 25001, 2500)), list(range(0, 10001, 1000))):
        m = Machine()
        m.powers(360, 360)
        m.install_timer()
        observed = []
        expected_count, previous = 0, 0
        for now in times:
            if now-previous >= 1000:
                expected_count += 1
                previous = now
            m.timer_poll(now)
            assert m.callbacks == expected_count
            observed.append(m.callbacks)
        assert m.sums()[0] == (expected_count//10)*360
        results["scheduler"].append({"poll_ticks": times, "cumulative_callbacks": observed,
                                      "energy_sum": m.sums()[0], "report": m.report()[19][3]})
    for remainder in (0, 1, 58, 59, 60, 119):
        m = Machine()
        # One enabled sample at the global 60-sample consolidation boundary.
        m.uc.mem_write(0x20000260, struct.pack("<I", 59))
        m.uc.mem_write(COUNTERS+0xd8, struct.pack("<4I", *([remainder]*4)))
        m.tick(10)
        duration = list(struct.unpack("<4I", m.uc.mem_read(COUNTERS+32, 16)))
        carry = list(struct.unpack("<4I", m.uc.mem_read(COUNTERS+0xd8, 16)))
        assert duration == [(remainder+1)//60]*4
        assert carry == [(remainder+1)%60]*4
        results["duration_remainders"].append({"previous": remainder,
                                               "duration": duration, "carry": carry})
    for before in range(360):
        for delta in (1, 359, 360, 361, 18954):
            # Exact integer consequence of the independently executed division.
            difference = (before+delta)//360 - before//360
            error_numerator = difference*360-delta
            assert -360 < error_numerator < 360
            results["delta_rounding"].append([before, delta, difference, error_numerator])
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = suite()
    counts = {name: len(rows) for name, rows in result.items()}
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # Keep the exhaustive mathematical bound compact; it contains no extra firmware executions.
    result["delta_rounding"] = {"cases": counts["delta_rounding"], "absolute_error_less_than_one": True}
    result.update(model="A1763 C1000 Gen 2", main_version="1.1.4.9",
                  firmware_sha256=FIRMWARE_SHA256, python_version=platform.python_version(),
                  unicorn_version=unicorn.__version__, counts=counts, all_cases_sha256=digest,
                  substitutions={f"{a:08x}": name for a, name in SUBSTITUTES.items()} |
                                {"08018aac": "synthetic backup-state pointer",
                                 "0800d78e": "SysTick interrupt-priority setup",
                                 "08015a44": "SysTick housekeeping callback"},
                  hardware_access=False, limits=__doc__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
