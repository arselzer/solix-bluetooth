#!/usr/bin/env python3
"""Offline A1763 output countdown, Smart policy and real caller fragments.

Actual parser/handlers/getters/policy/serializer and caller counter increments
execute on synthetic RAM. Transport/persistence/LCD/logging/libc are inherited
substitutes; the product-prefix comparison and diagnostics are explicit host
boundaries. The real event-marking helper runs, but no event worker, DSP/output
switch, radio, battery protection or physical timer/clock executes.
"""

import hashlib
import itertools
import json
import os
from pathlib import Path
import struct

from unicorn import UC_HOOK_MEM_WRITE, UC_PROT_EXEC, UC_PROT_READ
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_SP,
)

from emulate_clock_semantics import BUFFER, DESC, LENGTH, PAYLOAD, STACK, STOP
from emulate_general_settings import REQUEST, SETTINGS, TIMERS, tlv
from emulate_preference_candidates import PreferenceMachine
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
COUNTDOWNS = 0x20002174
EVENTS = 0x20007218
TICKS = 0x200008d0
DSP = 0x20003f00
CONFIG = {
    "ac": {"policy": 0x080134fc, "state": 0x20000020,
           "counter": 0x20000030, "power": DSP + 0x1e, "threshold": 20,
           "smart": SETTINGS + 0x1f, "remaining": COUNTDOWNS,
           "previous": 0x20000026, "timer_saved": SETTINGS + 0x22,
           "event": 1, "request_flag": 2, "handler": 0x0800bd1c,
           "gate": 0x08024a16, "eligible": 0x08024a26,
           "skip": 0x08024a88, "tail": 0x08024a6c, "tail_end": 0x08024a86,
           "sample_timer": 3, "sample_timer_byte": 0x2000002d},
    "car_dc": {"policy": 0x08014a08, "state": 0x20000000,
               "counter": 0x2000000c, "power": DSP + 0x86, "threshold": 3,
               "smart": SETTINGS + 0x20, "remaining": COUNTDOWNS + 8,
               "previous": 0x20000004, "timer_saved": SETTINGS + 0x23,
               "event": 1, "request_flag": 32, "handler": 0x0800bf94,
               "gate": 0x08024506, "eligible": 0x08024518,
               "skip": 0x08024544, "tail": 0x08024518, "tail_end": 0x0802453e,
               "sample_timer": 4, "sample_timer_byte": 0x20000007},
}


class OutputPolicyMachine(PreferenceMachine):
    def __init__(self):
        super().__init__()
        self.endpoints = set()
        self.reached_endpoint = None
        self.pending_events = []
        self.diagnostic_calls = 0
        self.uc.mem_protect(0x08000000, 0x40000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write)
        self.uc.mem_write(COUNTDOWNS, bytes(24))
        self.uc.mem_write(0x20000148 + 4, b"\x01")
        self.uc.mem_write(0x200004be, b"\x00")
        self.uc.mem_write(0x2000015e, b"\x01\x02")
        self.uc.mem_write(0x20000140, b"\x40")
        self.uc.mem_write(0x20000141, b"\x07")
        self.uc.mem_write(0x200000c2, b"\x07")
        self.uc.mem_write(0x20000112, b"YHqz")
        for c in CONFIG.values():
            self.uc.mem_write(EVENTS + c["event"] * 12, b"\x01" + bytes(11))
            self.uc.mem_write(c["sample_timer_byte"], bytes((c["sample_timer"],)))
            self.uc.mem_write(TIMERS + c["sample_timer"] * 20,
                              struct.pack("<BB2xIIII", 3, 0, 2000, 0, 0, 0))

    def write(self, uc, access, address, size, value, data):
        assert (0x20000000 <= address < address + size <= 0x20020000
                or 0x21000000 <= address < address + size <= 0x21002000), (
                    "Write outside synthetic RAM", hex(address), size)

    def step(self, uc, address, size, data):
        r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if address in self.endpoints:
            self.reached_endpoint = address
            self.stopped = True
            uc.emu_stop()
        elif address == 0x08005400:
            # Actual memcmp call boundary; do not invent a product enum.
            a, b = bytes(uc.mem_read(r0, r2)), bytes(uc.mem_read(r1, r2))
            self.back((a > b) - (a < b))
        elif address == 0x08011984:
            self.back(0)  # Diagnostic formatter; no output-policy decision.
        elif address == 0x08026ca4:
            self.diagnostic_calls += 1
            self.back(0)
        elif address == 0x08010eb0:
            self.back(int.from_bytes(uc.mem_read(TICKS, 4), "little"))
        elif address == 0x080106b4:
            self.pending_events.append([r0, r1])
            # Keep the actual event-marking helper, inherited from SettingsMachine.
        elif any(lo <= address < hi for lo, hi in (
            (0x080134fc, 0x08013612), (0x08014a08, 0x08014ad4),
            (0x08017fac, 0x08017fb2), (0x080185b4, 0x080185ba),
            (0x080180f8, 0x080180fe), (0x0801926c, 0x08019278),
            (0x0801953c, 0x08019556), (0x0802ad74, 0x0802ad90),
            (0x0802a070, 0x0802a076), (0x0802a150, 0x0802a156),
            (0x0802a26c, 0x0802a282), (0x0802b1b4, 0x0802b1ba),
            (0x0802b268, 0x0802b26e), (0x0802b2e8, 0x0802b2f8),
            (0x0802b348, 0x0802b358), (0x0800c04c, 0x0800c05a),
            (0x08024a16, 0x08024a26), (0x08024a6c, 0x08024a86),
            (0x08024506, 0x08024544), (0x0801095c, 0x08010992),
            (0x0802b788, 0x0802b79c),
        )):
            return
        else:
            super().step(uc, address, size, data)

    def put(self, address, value, fmt="I"):
        self.uc.mem_write(address, struct.pack("<" + fmt, value))

    def get(self, address, fmt="I"):
        return struct.unpack("<" + fmt, self.uc.mem_read(address, struct.calcsize(fmt)))[0]

    def prepare(self, kind, *, counter=0, power=0, smart=True, countdown=False,
                remaining=0, previous=True, prefix=b"YH", ac_ready=True, ac_block=False):
        c = CONFIG[kind]
        self.put(c["counter"], counter, "H")
        self.put(c["power"], power, "h")
        self.put(c["smart"], int(smart), "B")
        self.put(c["remaining"], remaining)
        self.put(c["previous"], int(previous), "B")
        self.put(COUNTDOWNS + 0x14, (1 if kind == "ac" else 2) if countdown else 0, "B")
        self.put(c["timer_saved"], int(countdown), "B")
        self.uc.mem_write(0x20000112, prefix + b"qz")
        self.put(0x2000014c, int(ac_ready), "B")
        self.put(0x200004be, int(ac_block), "B")

    def sample(self, kind):
        c = CONFIG[kind]
        self.pending_events = []
        self.run(c["policy"], c["counter"])
        return {"counter": self.get(c["counter"], "H"),
                "remaining": self.get(c["remaining"]),
                "countdown_bit": bool(self.get(COUNTDOWNS + 0x14, "B") & (1 if kind == "ac" else 2)),
                "timer_saved": self.get(c["timer_saved"], "B"),
                "event_pending": self.get(EVENTS + c["event"] * 12 + 1, "B"),
                "requests": self.pending_events.copy(),
                "stop_flags": self.get(0x20000140, "B")}

    def fragment(self, start, endpoints, kind):
        c = CONFIG[kind]
        self.endpoints = set(endpoints)
        self.reached_endpoint = None
        self.stopped = False
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.reg_write(UC_ARM_REG_R4, 0)
        self.uc.reg_write(UC_ARM_REG_R5, c["state"])
        self.uc.reg_write(UC_ARM_REG_R6, c["state"] if kind == "ac" else DSP)
        try:
            self.uc.emu_start(start | 1, 0, count=30000)
            assert self.stopped and self.reached_endpoint is not None
        finally:
            self.endpoints = set()
        return self.reached_endpoint

    def setting(self, kind, tag, value):
        c = CONFIG[kind]
        self.acked = 0
        body = tlv(0xa1, b"\x22") + tlv(tag, value) + tlv(0xfe, bytes.fromhex("0300f15365"))
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(c["handler"], REQUEST)
        assert self.acked == 1
        assert self.get(0x20000164) == 0x30

    def a4(self, mode=1):
        self.uc.mem_write(BUFFER, bytes(128))
        self.uc.mem_write(LENGTH, b"\0\0")
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBx", 4, BUFFER, LENGTH, 128, mode))
        self.run(0x0801a018, DESC)
        size = self.get(LENGTH, "H")
        raw = bytes(self.uc.mem_read(BUFFER, size))
        assert size == 35 and raw[:2] == bytes((34, 4))
        return raw[1:]


class ReportPathMachine(OutputPolicyMachine):
    """Actual 0100, 0421 and internal comparison paths; only A4 measurement real."""

    def __init__(self):
        super().__init__()
        image = firmware_image()
        table = struct.unpack_from("<I", image, 0x08022534 - 0x08005000)[0]
        self.callbacks = {struct.unpack_from("<I", image, table - 0x08005000 + 12 * i + 4)[0] & ~1
                          for i in range(19)}
        assert 0x0801a018 in self.callbacks
        self.a4_modes = []
        self.published = []

    def step(self, uc, address, size, data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in (
            UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        if address in self.callbacks:
            if address == 0x0801a018:
                self.a4_modes.append(uc.mem_read(r0 + 14, 1)[0])
            else:
                destination, length = struct.unpack("<II", uc.mem_read(r0 + 4, 8))
                position = self.get(length, "H")
                kind = uc.mem_read(r0, 1)[0]
                uc.mem_write(destination + position, bytes((1, kind)))
                self.put(length, position + 2, "H")
                self.back(0)
                return
        if address == 0x08020a4c:
            assert r0 == 1024
            self.back(BUFFER + 0x800)
        elif address == 0x08017a8c:
            assert r0 == BUFFER + 0x800
            self.back(0)
        elif address == 0x08013f20:
            assert (r0, r1) == (15, 0x421)
            self.published.append(bytes(uc.mem_read(r2, r3)))
            self.back(0)
        elif any(lo <= address < hi for lo, hi in (
            (0x0800b57c, 0x0800b62c), (0x080224a0, 0x08022538),
            (0x0800cfd4, 0x0800cfe0), (0x08012634, 0x0801269c),
            (0x0801bd40, 0x0801bd9c), (0x08013c46, 0x08013c4c),
        )):
            return
        else:
            super().step(uc, address, size, data)

    def status_query(self):
        body = tlv(0xa1, b"\x22")
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800b57c, REQUEST)
        assert self.responses[-1][0] == 0
        return self.responses[-1][1:]

    @staticmethod
    def fields(body):
        fields, position = {}, 0
        while position < len(body):
            tag, size = body[position:position + 2]
            position += 2
            fields[tag] = body[position:position + size]
            position += size
        assert position == len(body)
        return fields


def main():
    image = firmware_image()
    table = dict(struct.unpack_from("<II", image, 0x08032e60 - 0x08005000 + i * 8)
                 for i in range(17))
    assert table[0x101] == CONFIG["ac"]["handler"] | 1
    assert table[0x102] == CONFIG["car_dc"]["handler"] | 1
    rows = []

    def record(group, **data):
        rows.append({"group": group, **data})

    # Exact policy boundaries; seeded counter units are not yet elapsed time.
    for kind, prefix, power, smart, counter in itertools.product(
        CONFIG, (b"YH", b"ZZ"), (-1, 0, 3, 4, 20, 21), (False, True),
        (0, 900, 902, 18000, 18002, 28800, 28802, 65535),
    ):
        c = CONFIG[kind]
        m = OutputPolicyMachine()
        m.prepare(kind, counter=counter, power=power, smart=smart, prefix=prefix)
        before = bytes(m.uc.mem_read(SETTINGS, 0x190))
        outputs = bytes(m.uc.mem_read(0x20000164, 4))
        limit = (28800 if prefix == b"YH" else 900) if kind == "ac" else 18000
        blocked = power > c["threshold"] or not smart
        stop = not blocked and counter > limit
        result = m.sample(kind)
        assert result["counter"] == (0 if blocked or stop else counter), (kind, prefix, power, smart, counter, result)
        assert result["requests"] == ([[c["event"], 0]] if stop else []), (kind, prefix, power, smart, counter, result)
        assert result["event_pending"] == int(stop)
        assert result["stop_flags"] == (0x40 | (c["request_flag"] if stop else 0))
        assert m.uc.mem_read(SETTINGS, 0x190) == before
        assert m.uc.mem_read(0x20000164, 4) == outputs
        record("smart_boundary", kind=kind, prefix=prefix.decode(), power=power,
               smart=smart, counter_before=counter, limit=limit, **result)

    for ready, blocked in itertools.product((False, True), repeat=2):
        m = OutputPolicyMachine()
        m.prepare("ac", counter=28802, ac_ready=ready, ac_block=blocked)
        result = m.sample("ac")
        stop = ready and not blocked
        assert result["counter"] == 0 and bool(result["requests"]) == stop
        record("ac_blockers", ready=ready, blocker_bit=blocked, **result)

    # Countdown subtracts the counter, takes priority over Smart and expires at equality.
    for kind, counter, remaining, previous, smart in itertools.product(
        CONFIG, (0, 2, 4, 65535), (0, 1, 2, 3, 3600, 0xffffffff),
        (False, True), (False, True),
    ):
        c = CONFIG[kind]
        m = OutputPolicyMachine()
        m.prepare(kind, counter=counter, power=32767, smart=smart, countdown=True,
                  remaining=remaining, previous=previous, ac_ready=False, ac_block=True)
        before = bytearray(m.uc.mem_read(SETTINGS, 0x190))
        effective = counter if previous else 2
        stop = effective >= remaining
        result = m.sample(kind)
        assert result["counter"] == 0
        assert result["remaining"] == (remaining if stop else remaining - effective)
        assert result["countdown_bit"] is True
        assert result["requests"] == ([[c["event"], 0]] if stop else [])
        assert result["event_pending"] == int(stop)
        before[c["timer_saved"] - SETTINGS] = int(not stop)
        assert m.uc.mem_read(SETTINGS, 0x190) == bytes(before)
        expected_reason = (0x55 if kind == "ac" else 0x56) if stop else 7
        assert m.get(0x200000c2, "B") == expected_reason
        record("countdown", kind=kind, counter_before=counter, remaining_before=remaining,
               previous_enabled=previous, smart=smart, effective=effective, **result)

    for kind in CONFIG:
        c = CONFIG[kind]
        for value in (0, 1, 2, 3600, 47100, 0xffffffff):
            m = OutputPolicyMachine()
            m.prepare(kind, counter=123, remaining=60, countdown=True)
            checkpoint = COUNTDOWNS + (0x10 if kind == "ac" else 0x12)
            m.put(checkpoint, 55, "H")
            m.put(COUNTDOWNS + 0xc, 78)
            settings_before = bytes(m.uc.mem_read(SETTINGS, 0x190))
            m.setting(kind, 0xa3, b"\x03" + struct.pack("<I", value))
            assert m.get(c["remaining"]) == value
            assert bool(m.get(COUNTDOWNS + 0x14, "B") & (1 if kind == "ac" else 2)) == bool(value)
            assert m.get(c["counter"], "H") == 123
            assert m.get(checkpoint, "H") == (55 if value else 0)
            assert m.get(COUNTDOWNS + 0xc) == (value if kind == "car_dc" and value else 78)
            assert m.uc.mem_read(SETTINGS, 0x190) == settings_before
            a4 = m.a4()
            offset = 1 if kind == "ac" else 9
            assert int.from_bytes(a4[offset:offset + 4], "little") == value
            periodic = m.a4(mode=3)
            assert periodic[1:5] == periodic[9:13] == bytes(4)
            record("wire_timer_readback", kind=kind, supplied_seconds=value,
                   a4_mode1_remaining=value, a4_mode3_remaining=0,
                   counter_after=m.get(c["counter"], "H"),
                   checkpoint_after=m.get(checkpoint, "H"),
                   dc_initial_duration_after=m.get(COUNTDOWNS + 0xc),
                   persistent_settings_preserved=True)

        for mode, counter in itertools.product((False, True), (0, 902, 18002, 28802)):
            m = OutputPolicyMachine()
            m.prepare(kind, counter=counter, smart=not mode)
            tag = 0xa6 if kind == "ac" else 0xa4
            m.setting(kind, tag, bytes((1, int(mode))))
            assert m.get(c["counter"], "H") == counter
            result = m.sample(kind)
            expected_stop = mode and counter > (28800 if kind == "ac" else 18000)
            assert bool(result["requests"]) == expected_stop
            record("smart_command_counter", kind=kind, enabled=mode,
                   counter_at_write=counter, command_cleared_counter=False, **result)

        for intermediate in ("none", "policy_sample", "active_sample_timer"):
            m = OutputPolicyMachine()
            limit = 28800 if kind == "ac" else 18000
            m.prepare(kind, counter=limit + 2, smart=True)
            baseline = bytes(m.uc.mem_read(SETTINGS, 0x190))
            tag = 0xa6 if kind == "ac" else 0xa4
            m.setting(kind, tag, b"\x01\x00")
            assert m.get(c["counter"], "H") == limit + 2
            if intermediate == "policy_sample":
                assert m.sample(kind)["counter"] == 0
            elif intermediate == "active_sample_timer":
                m.put(TIMERS + c["sample_timer"] * 20, 2, "B")
                assert m.fragment(c["gate"], (c["eligible"], c["skip"]), kind) == c["skip"]
                assert m.get(c["counter"], "H") == limit + 2
            m.setting(kind, tag, b"\x01\x01")
            result = m.sample(kind)
            assert bool(result["requests"]) == (intermediate != "policy_sample")
            assert m.uc.mem_read(SETTINGS, 0x190) == baseline
            record("smart_off_on_history", kind=kind, intermediate=intermediate,
                   complete_preferences_restored=True, **result)

        for active, counter in itertools.product((False, True), (0, 65534, 65535)):
            m = OutputPolicyMachine()
            m.prepare(kind, counter=counter, smart=True)
            m.put(TIMERS + c["sample_timer"] * 20, 2 if active else 3, "B")
            m.put(TICKS, 123456)
            reached = m.fragment(c["gate"], (c["eligible"], c["skip"]), kind)
            assert reached == (c["skip"] if active else c["eligible"])
            assert m.get(c["counter"], "H") == counter
            if not active:
                m.fragment(c["tail"], (c["tail_end"],), kind)
                wrapped = (counter + 2) & 0xffff
                limit = 28800 if kind == "ac" else 18000
                assert m.get(c["counter"], "H") == (0 if wrapped > limit else wrapped)
                assert m.get(TIMERS + c["sample_timer"] * 20, "B") == 2
                assert m.get(TIMERS + c["sample_timer"] * 20 + 8) == 123456
            record("actual_caller_fragment", kind=kind, timer_active=active,
                   counter_before=counter, counter_after=m.get(c["counter"], "H"),
                   sample_skipped=active, timer_period_ticks=2000)

    for ac_remaining, dc_remaining in itertools.product((0, 3600, 0xffffffff), repeat=2):
        m = ReportPathMachine()
        m.put(COUNTDOWNS, ac_remaining)
        m.put(COUNTDOWNS + 8, dc_remaining)
        saved = bytes(m.uc.mem_read(SETTINGS, 0x190))
        fields = m.fields(m.status_query())
        assert m.a4_modes == [1]
        a4 = fields[0xa4]
        assert (int.from_bytes(a4[1:5], "little"), int.from_bytes(a4[9:13], "little")) == (ac_remaining, dc_remaining)
        m.run(0x08012634, 0)
        assert m.a4_modes == [1, 1] and len(m.published) == 1
        periodic = m.fields(m.published[-1])[0xa4]
        assert periodic[1:5] == a4[1:5] and periodic[9:13] == a4[9:13]
        m.fragment(0x08013c46, (0x08013c4c,), "ac")
        assert m.a4_modes == [1, 1, 3] and len(m.published) == 1
        assert m.uc.mem_read(SETTINGS, 0x190) == saved
        record("actual_report_paths", ac_remaining=ac_remaining, dc_remaining=dc_remaining,
               status_0100_a4_mode=1, publication_0421_a4_mode=1,
               internal_comparison_a4_mode=3, comparison_published=False,
               saved_settings_preserved=True)

    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
              "cases": len(rows), "hardware_access": False,
              "ordinary_table_routes": {"0101": "0800bd1c", "0102": "0800bf94"},
              "results": rows}
    output = ROOT / "gen2-output-policy-results.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.chmod(0o600)
    names = (Path(__file__).name, "emulate_preference_candidates.py",
             "emulate_charging_followup.py", "emulate_general_settings.py",
             "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows),
                "hardware_access": False,
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in names},
                "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "boundaries": ["logging/diagnostic formatting", "memcmp", "system ticks",
                               "persistence and response/refresh transports", "LCD delivery",
                               "allocation/free", "non-A4 measurement callbacks in report paths",
                               "memory copy/clear"],
                "excluded": ["radio and MQTT", "event worker and output switch",
                             "DSP/physical protection and power", "full output task",
                             "actual timer scheduler and elapsed time"]}
    manifest_path = ROOT / "gen2-output-policy-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "hardware_access": False,
                      "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
