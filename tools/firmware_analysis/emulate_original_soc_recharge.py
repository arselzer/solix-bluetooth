#!/usr/bin/env python3
"""Replay original C1000 SOC/history and near-full charge-policy blocks offline.

Only public 1.5.9 bundle images and synthetic RAM enter this tool. Selected
BMS instruction blocks exclude ADC, real clocks, persistent learning and GPIO.
The complete MainMcu charging callback is executed; DSP enforcement is not.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
)

from emulate_original_bms_state import BmsMachine, IMAGES, PACKET, STACK, STATE
from emulate_original_fast_power import ChargeMachine
from emulate_original_fast_retention import BMS, INFO, OUTPUT, PAYLOAD, POLICY_STATE
from emulate_original_power_paths import CEILING, DESCRIPTOR
from emulate_c1000_original_commands import IMAGE, IMAGE_SHA256

SOC = {
    "main": {
        "capacity": 0x20001164, "ratio": 0x20001134, "reported": 0x200011c4,
        "round": (0x08009d0c, 0x08009d48), "capacity_register": UC_ARM_REG_R5,
        "final": (0x0800a1d8, 0x0800a1f0), "candidate_register": UC_ARM_REG_R3,
        "final_capacity_register": UC_ARM_REG_R5,
        "clear": (0x0800af14, 0x0800af50), "timer": 0x200004ac,
    },
    "expansion": {
        "capacity": 0x20000ab0, "ratio": 0x20000a80, "reported": 0x20000b10,
        "round": (0x0800772c, 0x08007766), "capacity_register": UC_ARM_REG_R4,
        "final": (0x08007ae4, 0x08007afc), "candidate_register": UC_ARM_REG_R2,
        "final_capacity_register": UC_ARM_REG_R4,
        "clear": (0x0800882c, 0x08008866), "timer": 0x20000472,
    },
}


class SocMachine(BmsMachine):
    """Keep fragment stops explicit when a prior run cached the target block."""

    def code_guard(self, uc, address, size, data):
        if address == getattr(self, "fragment_stop", None):
            uc.emu_stop()
            return
        super().code_guard(uc, address, size, data)

    def run(self, start, stop, registers=None):
        self.fragment_stop = stop
        try:
            super().run(start, stop, registers)
        finally:
            self.fragment_stop = None


def soc_fields(m, primary, expansion):
    """Real incoming copies, getter calls and typed status serializer prefix."""
    for pointer, value in ((BMS, primary), (BMS + 0x38, expansion)):
        m.put(PAYLOAD + 0x32, value, "H")
        m.run(0x080134c8, stop=0x080134ce,
              registers={UC_ARM_REG_R4: PAYLOAD + 0x3c,
                         UC_ARM_REG_R5: pointer + 0x14})
    m.run(0x08010c9a, stop=0x08010caa,
          registers={UC_ARM_REG_R4: INFO + 0x3b})
    m.put(OUTPUT + 512, 0, "H")
    m.run(0x08009164, OUTPUT, stop=0x0800924a,
          registers={UC_ARM_REG_R1: OUTPUT + 512})
    raw = bytes(m.uc.mem_read(OUTPUT, 214))
    fields, offset = {}, 0
    while offset < len(raw):
        tag, size = raw[offset:offset + 2]
        fields[tag] = raw[offset + 2:offset + 2 + size]
        offset += size + 2
    assert offset == len(raw)
    assert fields[0xc1] == bytes((1, primary))
    assert fields[0xc2] == bytes((1, expansion))
    return {"c1": fields[0xc1].hex(), "c2": fields[0xc2].hex()}


def run_suite(directory):
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not use Python -O")
    result = {
        "main_image_sha256": IMAGE_SHA256,
        "bms_image_sha256": {k: v["sha256"] for k, v in IMAGES.items()},
        "unicorn_version": unicorn.__version__,
        "scope": "Public original 1.5.9 bundle; synthetic RAM; no 1.7.1, physical SOC, timing or DSP validation",
        "capacity_rounding": [], "full_soc_latch": [], "history_denominator": [],
        "full_latch_clear": [], "expansion_packet_override": [],
        "primary_packet_soc": [], "independent_status_fields": [],
        "charge_phase_resync": [], "full_flag_policy": [], "serialized_latch_flag": [],
    }
    machines = {kind: SocMachine((directory / cfg["filename"]).read_bytes(), kind)
                for kind, cfg in IMAGES.items()}
    for kind, m in machines.items():
        c = SOC[kind]
        for remaining in (0, 500, 98500, 99400, 99499, 99500, 99900, 100000):
            m.reset()
            m.put(c["capacity"] + 4, remaining)
            m.put(c["capacity"] + 8, 100000)
            m.run(*c["round"], {c["capacity_register"]: c["capacity"]})
            tenths = m.get(c["ratio"] + 0x20, "H")
            percent = m.get(c["ratio"] + 0x24, "H")
            assert tenths == remaining // 100
            assert percent == (tenths + 5) // 10
            result["capacity_rounding"].append({"bms": kind,
                "remaining_raw": remaining, "full_raw": 100000,
                "ratio_tenths": tenths, "candidate_percent": percent})

        for candidate, latch in itertools.product((98, 99, 100), (0, 0x5a)):
            m.reset()
            m.put(c["capacity"] + 0x2b, latch, "B")
            m.run(*c["final"], {c["final_capacity_register"]: c["capacity"],
                                   c["candidate_register"]: candidate})
            actual = m.get(c["reported"], "H")
            assert actual == (99 if candidate == 100 and latch != 0x5a else candidate)
            result["full_soc_latch"].append({"bms": kind,
                "candidate_percent": candidate, "latch_byte": latch,
                "cached_reported_percent": actual})

        # Start immediately before the real latch-clear policy. The external
        # timer is seeded, not advanced by this fragment or converted to time.
        cases = (
            (97, 100, 3320, 0, 201, False),
            (97, 100, 3319, 1, 201, False),
            (97, 100, 3319, 0, 200, False),
            (97, 100, 3319, 0, 201, True),
            (96, 100, 3400, 2, 200, False),
            (96, 100, 3400, 2, 201, True),
            (99, 99, 3400, 2, 201, True),
        )
        for candidate, reported, cell_cache, phase, timer, clear in cases:
            m.reset(phase)
            m.put(c["capacity"] + 0x2b, 0x5a, "B")
            m.put(c["ratio"] + 0x24, candidate, "H")
            m.put(c["reported"], reported, "H")
            m.put(c["timer"], timer, "H")
            m.put(STACK + 0xd8, cell_cache)
            registers = {UC_ARM_REG_R1: phase, UC_ARM_REG_R5: c["ratio"],
                         UC_ARM_REG_R7: cell_cache}
            m.run(*c["clear"], registers)
            latch_after = m.get(c["capacity"] + 0x2b, "B")
            assert latch_after == (0 if clear else 0x5a)
            keep_condition = candidate >= 97 and reported >= 100 and (
                cell_cache >= 3320 or phase != 0)
            assert m.get(c["timer"], "H") == (0 if keep_condition else timer)
            result["full_latch_clear"].append({"bms": kind,
                "candidate_percent": candidate, "reported_percent": reported,
                "cell_cache_raw": cell_cache, "phase": phase,
                "timer_before": timer, "latch_after": latch_after,
                "timer_after": m.get(c["timer"], "H")})

    main = machines["main"]
    for denominator, latch in itertools.product((100000, 99000, 98000), (0, 0x5a)):
        main.reset()
        main.put(0x20000018, denominator)
        main.put(STACK + 0xcc, 1000)
        main.put(STACK + 0xd4, 99000)
        main.put(STACK + 0xc4, 10)
        main.put(SOC["main"]["capacity"] + 0x2b, latch, "B")
        main.run(0x0800a1a4, 0x0800a1f0, {
            UC_ARM_REG_R1: 0xfff6, UC_ARM_REG_R3: 99,
            UC_ARM_REG_R5: SOC["main"]["capacity"]})
        actual = main.get(SOC["main"]["reported"], "H")
        assert actual == (100 if denominator < 100000 and latch == 0x5a else 99)
        result["history_denominator"].append({"remaining_raw": 99000,
            "full_raw": 100000, "primary_candidate": 99,
            "history_denominator_raw": denominator, "latch_byte": latch,
            "reported_percent": actual,
            "note": "Denominator deliberately seeded; its learning and previous history are not simulated"})

    for reported in (50, 99, 100):
        main.reset()
        main.put(SOC["main"]["reported"], reported, "H")
        main.run(0x08008350, 0x08008356, {UC_ARM_REG_R6: PACKET})
        actual = main.get(PACKET + 0x32, "H")
        assert actual == reported
        result["primary_packet_soc"].append({"cached_soc": reported, "body_32_soc": actual})

    sub = machines["expansion"]
    for reported, flag in itertools.product((50, 99, 100), (0, 1)):
        sub.reset()
        sub.put(SOC["expansion"]["reported"], reported, "H")
        sub.put(0x20000974, flag, "B")
        sub.put(STACK + 0x38, 1)
        sub.run(0x08005b5e, 0x08005b8a, {
            UC_ARM_REG_R4: 0x20000078, UC_ARM_REG_R7: PACKET})
        actual = sub.get(PACKET + 0x32, "H")
        assert actual == (reported if flag else 100)
        result["expansion_packet_override"].append({"cached_soc": reported,
            "raw_byte_20000974": flag, "serialized_soc": actual,
            "cached_soc_after": sub.get(SOC["expansion"]["reported"], "H"),
            "note": "Meaning of this raw mode byte is not assigned"})

    # A direction-change branch replaces remaining capacity using the prior
    # displayed SOC. This is real arithmetic, but the condition/history flag is
    # synthetic; no coulomb counter or periodic update rate is simulated.
    for kind, m in machines.items():
        c = SOC[kind]
        start, stop, seen, stack_offset = (
            (0x08009ce2, 0x08009d0c, 0x20001002, 0x98) if kind == "main" else
            (0x08007702, 0x0800772c, 0x20000a62, 0x90))
        for phase, already_seen in ((1, 0), (2, 0), (2, 1)):
            m.reset(phase)
            m.put(c["capacity"] + 4, 98000)
            m.put(c["capacity"] + 8, 100000)
            m.put(c["reported"], 100, "H")
            m.put(seen, already_seen, "B")
            m.put(STACK + stack_offset, 1)
            m.run(start, stop, {c["capacity_register"]: c["capacity"]})
            remaining = m.get(c["capacity"] + 4)
            assert remaining == (100000 if phase == 2 and not already_seen else 98000)
            result["charge_phase_resync"].append({"bms": kind, "phase": phase,
                "history_seen_before": already_seen, "reported_soc": 100,
                "remaining_before": 98000, "remaining_after": remaining,
                "history_seen_after": m.get(seen, "B")})

    m = ChargeMachine((directory / "MainMcu-decoded.bin").read_bytes())
    for raw_flags, latch in itertools.product((0, 1, 2), (0, 0x5a)):
        main.reset()
        main.put(SOC["main"]["capacity"] + 0x2b, latch, "B")
        main.put(PACKET + 0x54, raw_flags, "B")
        main.run(0x0800835e, 0x0800836e, {
            UC_ARM_REG_R0: 0x54, UC_ARM_REG_R5: raw_flags, UC_ARM_REG_R6: PACKET})
        serialized = main.get(PACKET + 0x54, "B")
        assert serialized == (raw_flags | int(latch == 0x5a))
        m.prepare()
        m.put(PAYLOAD + 0x54, serialized, "B")
        m.run(0x080134ce, stop=0x080134d6, registers={
            UC_ARM_REG_R4: PAYLOAD + 0x3c, UC_ARM_REG_R5: BMS + 0x14})
        cached = m.get(BMS + 0x31, "B")
        assert cached == serialized & 1
        result["serialized_latch_flag"].append({"raw_status_low_byte": raw_flags,
            "full_related_latch": latch, "body_54_low_byte": serialized,
            "main_mcu_bms_31": cached})

    for primary, expansion in ((100, 50), (99, 100), (50, 99)):
        m.prepare()
        fields = soc_fields(m, primary, expansion)
        result["independent_status_fields"].append({"primary_soc": primary,
            "expansion_soc": expansion, "fields": fields})

    for soc, saved in itertools.product((99, 100), (100, 300, 1000)):
        m.prepare(soc=soc, fast=False)
        m.put(CEILING, saved, "H")
        before = m.protected()
        m.put(BMS + 0x31, 1, "B")
        traces = []
        for full_flag in (1, 1, 0, 0):
            m.put(BMS + 0x31, full_flag, "B")
            m.policy()
            assert m.protected() == before
            assert not m.events and not m.calls
            traces.append({"bms_full_flag": full_flag,
                "policy_state": m.get(POLICY_STATE, "B"),
                "descriptor_power_voltage_current": list(struct.unpack(
                    "<3H", m.uc.mem_read(DESCRIPTOR, 6)))})
        assert [row["policy_state"] for row in traces[:3]] == [7, 7, 0]
        assert traces[-1]["policy_state"] == (2 if soc == 100 else 0)
        assert all(row["descriptor_power_voltage_current"][0] == saved * 91 // 100
                   for row in traces[:3])
        result["full_flag_policy"].append({"soc_held": soc,
            "saved_normal_watts": saved, "traces": traces,
            "protected_settings_unchanged": True,
            "note": "Charging descriptor, not measured total AC input or proof of battery current"})

    result["cases"] = sum(len(value) for value in result.values() if isinstance(value, list))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, default=IMAGE.parent,
                        help="Original 1.5.9 bundle directory; all three image hashes checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware_dir)
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
