#!/usr/bin/env python3
"""Offline original C1000 v1.5.9 Smart auto-off branches and software timers.

No transport, device access, or firmware modification. Synthetic peripheral reads
and captured stop requests do not exercise physical output shutdown.
"""

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_SP,
)

from emulate_c1000_original_commands import (
    CTX, IMAGE, IMAGE_SHA256, Machine, OUTPUT, PAYLOAD, RAM, SP,
)

DSP = 0x20002274
TIMERS = 0x20004e28
TICK = 0x2000070c
STOP_FLAGS = 0x200004ec
EVENT_ID = 0x200004f8
CONFIG = {
    "ac": {"state": 0x2000001c, "counter": 0x20000028,
           "mode": 0x200004ac, "handler": 0x0800bd28,
           "power": DSP+0x1e, "cache": 0x200020ea,
           "start": 0x0801f82c, "end": 0x0801f8ee,
           "queued": 0x0801f8bc, "threshold": 20, "limit": 450,
           "flag": 2, "countdown_bit": 1, "timer_offset": 9},
    "car_dc": {"state": 0x20000000, "counter": 0x2000000a,
               "mode": 0x200004ab, "handler": 0x0800bd48,
               "power": DSP+0x86, "cache": 0x200020e2,
               "start": 0x0801f35c, "end": 0x0801f3f0,
               "queued": 0x0801f3ca, "threshold": 3, "limit": 9000,
               "flag": 32, "countdown_bit": 2, "timer_offset": 4},
}


class PolicyMachine(Machine):
    def __init__(self, image):
        self.endpoints = set()
        self.events = []
        self.mmio_writes = []
        super().__init__(image)
        self.uc.mem_map(0x40011000, 0x1000)
        self.uc.mem_map(0xe000e000, 0x1000)

    def write(self, uc, access, address, size, value, data):
        if address in (0xe000e010, 0xe000e014, 0xe000e018) and size == 4:
            self.mmio_writes.append([address, value])
        else:
            super().write(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        if address in self.endpoints:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x08010278:
            self.events.append([uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)])
            self.back(0)
        elif address in (0x0800ccf0, 0x0800d282, 0x08014e90):
            # Debug log, interrupt priority setup, unrelated SysTick housekeeping.
            self.calls.append(f"substitute_{address:08x}")
            self.back(0)
        else:
            super().step(uc, address, size, data)

    def put(self, address, value, fmt="I"):
        self.uc.mem_write(address, struct.pack("<"+fmt, value))

    def get(self, address, fmt="I"):
        return struct.unpack("<"+fmt, self.uc.mem_read(address, struct.calcsize(fmt)))[0]

    def prepare(self, kind, power=0, mode=2, counter=0, countdown=False,
                gpio=True, global_block=False):
        self.reset()
        self.events, self.mmio_writes = [], []
        cfg = CONFIG[kind]
        self.put(cfg["power"], power, "h")
        self.put(cfg["mode"], mode, "B")
        self.put(cfg["counter"], counter, "H")
        self.put(0x200020c4, cfg["countdown_bit"] if countdown else 0, "B")
        self.put(0x20000690, int(global_block), "B")
        self.put(0x40011408, 4 if gpio else 0)
        self.put(STOP_FLAGS, 0x40, "B")
        self.put(EVENT_ID, 7, "B")
        self.uc.mem_write(TIMERS, bytes(70*20))
        self.put(TIMERS+20, 3, "B")
        self.put(TIMERS+24, 2000)
        self.put(cfg["state"]+cfg["timer_offset"], 1, "B")
        self.put(TICK, 123456)

    def sample(self, kind):
        cfg = CONFIG[kind]
        if kind == "ac":
            registers = {UC_ARM_REG_R4: 0, UC_ARM_REG_R5: DSP,
                         UC_ARM_REG_R6: cfg["state"], UC_ARM_REG_R7: STOP_FLAGS,
                         UC_ARM_REG_R8: EVENT_ID}
        else:
            registers = {UC_ARM_REG_R4: 0, UC_ARM_REG_R5: cfg["state"],
                         UC_ARM_REG_R6: STOP_FLAGS, UC_ARM_REG_R7: EVENT_ID,
                         UC_ARM_REG_R8: DSP}
        self.endpoints = {cfg["queued"]} | ({0x0801f3f6} if kind == "car_dc" else set())
        try:
            self.run(cfg["start"], stop=cfg["end"], registers=registers)
        finally:
            self.endpoints = set()
        return self.get(cfg["counter"], "H"), bool(self.events)


def run_suite(image):
    m = PolicyMachine(image)
    cases, counts = [], Counter()

    def record(group, row):
        counts[group] += 1
        cases.append([group, row])

    for kind, cfg in CONFIG.items():
        powers = (-32768, -1, 0, 1, 2, 3, 4, 15, 16, 19, 20, 21, 32767)
        counters = (0, cfg["limit"]-1, cfg["limit"], cfg["limit"]+1, 65535)
        guards = itertools.product((False, True), repeat=3) if kind == "ac" else [(False, True, False), (True, True, False)]
        guards = list(guards)
        for power, mode, counter, (countdown, gpio, global_block) in itertools.product(
                powers, (1, 2), counters, guards):
            m.prepare(kind, power, mode, counter, countdown, gpio, global_block)
            before = m.low_ram()
            blocked = power > cfg["threshold"] or countdown or (
                kind == "ac" and (not gpio or global_block))
            incremented = ((0 if blocked else counter) + 1) & 0xffff
            expected_stop = incremented > cfg["limit"] and mode != 1
            expected_counter = 0 if expected_stop else incremented
            actual_counter, stopped = m.sample(kind)
            assert (actual_counter, stopped) == (expected_counter, expected_stop)
            assert m.events == ([[7, 0]] if expected_stop else [])
            assert m.get(STOP_FLAGS, "B") == (0x40 | (cfg["flag"] if expected_stop else 0))
            cached = max(power, 0) if power > (15 if kind == "ac" else 1) else 0
            assert m.get(cfg["cache"], "H") == cached
            assert m.get(TIMERS+20, "B") == 2 and m.get(TIMERS+28) == 123456
            # Restrict non-stack RAM writes to the actual policy's known fields.
            expected = bytearray(before)
            updates = {cfg["counter"]: struct.pack("<H", expected_counter),
                       cfg["cache"]: struct.pack("<H", cached),
                       STOP_FLAGS: bytes((0x40 | (cfg["flag"] if expected_stop else 0),)),
                       TIMERS+20: b"\x02", TIMERS+28: struct.pack("<I", 123456)}
            if kind == "ac":
                updates[cfg["state"]+6] = bytes(((before[cfg["state"]+6-RAM]+1) & 255,))
            for address, data in updates.items():
                expected[address-RAM:address-RAM+len(data)] = data
            assert m.low_ram() == expected
            record("policy_boundary_and_guards", [kind, power, mode, counter, countdown,
                                                  gpio, global_block, actual_counter, stopped])

    for kind, cfg in CONFIG.items():
        for mode in (1, 2):
            m.prepare(kind, power=0, mode=mode, counter=cfg["limit"]+1)
            m.put(TIMERS+20, 2, "B")
            before = m.low_ram()
            assert m.sample(kind) == (cfg["limit"]+1, False)
            assert m.low_ram() == before
            record("active_timer_skips_policy", [kind, mode])

    inheritance = []
    for kind, cfg in CONFIG.items():
        m.prepare(kind, power=0, mode=1, counter=cfg["limit"])
        assert m.sample(kind) == (cfg["limit"]+1, False)
        m.put(PAYLOAD+6, 1, "B")
        m.run(cfg["handler"], CTX)
        assert m.get(cfg["mode"], "B") == 2
        assert m.get(cfg["counter"], "H") == cfg["limit"]+1
        m.put(TICK, m.get(TICK)+2000)
        m.run(0x08010448)
        assert m.sample(kind) == (0, True)
        inheritance.append({"domain": kind, "normal_counter": cfg["limit"]+1,
                            "smart_command_cleared_counter": False,
                            "next_sample_queued_stop": True})
        record("normal_to_smart_inherits_counter", inheritance[-1])

    # Actual cache getter -> info-field store -> typed TLV serializer, no float/unit conversion.
    telemetry = []
    for kind, start, end, info_offset, tag in (
            ("ac", 0x08010ba8, 0x08010bb0, 0x0c, 0xa6),
            ("car_dc", 0x08010bd4, 0x08010bdc, 0x1a, 0xad)):
        for raw in (0, 1, 3, 20, 1234, 32767):
            m.reset()
            m.put(CONFIG[kind]["cache"], raw, "H")
            m.run(start, stop=end, registers={UC_ARM_REG_R4: 0x20001374})
            assert m.get(0x20001374+info_offset, "H") == raw
            m.put(OUTPUT+0x200, 0, "H")
            m.run(0x08009164, OUTPUT, stop=0x08009212,
                  registers={UC_ARM_REG_R1: OUTPUT+0x200})
            payload = bytes(m.uc.mem_read(OUTPUT, m.uc.reg_read(UC_ARM_REG_R0)))
            pos, fields = 0, {}
            while pos < len(payload):
                field, length = payload[pos:pos+2]
                fields[field] = payload[pos+2:pos+2+length]
                pos += length+2
            assert pos == len(payload)
            assert fields[tag] == b"\x02"+struct.pack("<H", raw)
            telemetry.append([kind, raw, f"{tag:02x}", fields[tag].hex()])
            record("cache_to_typed_telemetry", telemetry[-1])

    # Actual SysTick configuration and ISR increment with only IRQ/housekeeping substituted.
    m.reset()
    declared_hz = m.get(0x20000708)
    assert declared_hz == 72000000
    m.mmio_writes = []
    m.run(0x08025c50)
    assert m.mmio_writes == [[0xe000e014, 71999], [0xe000e018, 0], [0xe000e010, 7]]
    assert struct.unpack_from("<I", image, 15*4)[0] == 0x08010189
    record("systick_configuration", m.mmio_writes)
    for seed in (0, 1, 1999, 0xfffffffe, 0xffffffff):
        m.put(TICK, seed)
        m.put(0x20000704, seed)
        m.run(0x08010188)
        assert m.get(TICK) == (seed+1) & 0xffffffff
        assert m.get(0x20000704) == (seed+1) & 0xffffffff
        record("systick_increment", [seed, m.get(TICK)])

    timer_cases = []
    for start in (0, 10000, 0xfffffff0):
        for elapsed in (0, 1999, 2000, 2001, 100000):
            m.reset()
            m.uc.mem_write(TIMERS, bytes(70*20))
            # Reserve timer0 as startup does; allocate target timer1 using firmware.
            m.put(TIMERS, 1, "B")
            m.run(0x080101ac, 2000, registers={UC_ARM_REG_R1: 0, UC_ARM_REG_R2: 0,
                                              UC_ARM_REG_R3: OUTPUT})
            assert m.get(OUTPUT, "B") == 1 and m.get(TIMERS+24) == 2000
            assert m.get(TIMERS+21, "B") == 0
            m.put(TICK, start)
            m.run(0x08010508, 1)
            assert m.get(TIMERS+28) == start
            m.put(TICK, (start+elapsed) & 0xffffffff)
            m.run(0x08010448)
            m.run(0x080103ec, 1, registers={UC_ARM_REG_R1: OUTPUT})
            assert m.get(OUTPUT, "B") == int(elapsed < 2000)
            state = m.get(TIMERS+20, "B")
            assert state == (2 if elapsed < 2000 else 3)
            if elapsed >= 2000:
                m.run(0x08010508, 1)
                assert m.get(TIMERS+28) == (start+elapsed) & 0xffffffff
            timer_cases.append([start, elapsed, state])
            record("one_shot_timer_expiry_and_rearm", timer_cases[-1])

    # Exact output countdown handlers establish the two Smart-policy blocking flags.
    timer_handlers = []
    app_table = m.table(0x20000254, 32)
    for kind, handler, flag, remaining in (("ac", 0x0800b990, 1, 0x200020b0),
                                          ("car_dc", 0x0800ba88, 2, 0x200020b8)):
        # Handler addresses are pinned independently below from the recovered table.
        matching = [row for row in app_table if row["handler"] == f"{handler:08x}"]
        assert len(matching) == 1
        for value in (0, 1, 30, 3600):
            m.reset()
            m.put(PAYLOAD+6, value)
            m.put(0x200020c4, 0xfc, "B")
            m.run(handler, CTX)
            assert m.get(remaining) == value
            assert m.get(0x200020c4, "B") == (0xfc | (flag if value else 0))
            record("countdown_flag_provenance", [kind, matching[0]["opcode"], value])
        timer_handlers.append({"domain": kind, "opcode": matching[0]["opcode"],
                               "handler": f"{handler:08x}", "blocking_bit": flag.bit_length()-1})

    return {
        "model": "A1761 original C1000", "firmware_version": "1.5.9",
        "input_sha256": IMAGE_SHA256,
        "environment": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "cases": dict(counts) | {"total": len(cases)},
        "cases_sha256": hashlib.sha256(json.dumps(cases, separators=(",", ":")).encode()).hexdigest(),
        "policy": {kind: {"raw_power_threshold_inclusive": cfg["threshold"],
                           "eligible_samples_from_zero": cfg["limit"]+1,
                           "nominal_sample_period_ticks": 2000,
                           "nominal_seconds_from_zero": (cfg["limit"]+1)*2,
                           "queued_stop_flag": cfg["flag"]} for kind, cfg in CONFIG.items()},
        "counter_inheritance": inheritance,
        "telemetry": telemetry, "timer_cases": timer_cases,
        "countdown_handlers": timer_handlers,
        "systick": {"declared_clock_hz": declared_hz, "reload": 71999,
                    "nominal_tick_hz": 1000, "vector": "08010189"},
        "substitutions": {"08010278": "capture output-stop event, no dispatch",
                          "0800ccf0": "debug log", "0800d282": "interrupt priority programming",
                          "08014e90": "unrelated SysTick housekeeping",
                          "080251f0": "Smart setting persistence (existing helper)",
                          "08007718": "command acknowledgment (existing helper)"},
        "boundaries": ["Policy replay enters after output-enabled outer gate; actual timer-active gate runs",
                       "Stops immediately after queue request; does not execute physical shutdown",
                       "Actual GPIO reader uses synthetic 40011408; physical pin meaning unproven",
                       "Timer and SysTick tests use independent synthetic ticks/peripheral registers",
                       "No original DSP image or independent physical power calibration",
                       "Installed 1.5.1 and other models may differ from analyzed 1.5.9",
                       "Normal skips only this low-load shutdown path, not faults or other timers"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    encoded = json.dumps(result, indent=2, sort_keys=True)+"\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
