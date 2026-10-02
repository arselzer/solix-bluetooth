#!/usr/bin/env python3
"""Offline A1761 1.5.9 second-input qualification and charging-rule handoff.

Actual first-two-slot registration, sampler, predicates/callbacks and selected
healthy rule groups execute in Unicorn. Synthetic DSP/RAM only; event delivery
is captured, and rule invocation is explicit, not a whole event dispatcher.
No device, transport, physical input, DSP, GPIO or persistent-storage access.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R5

from emulate_c1000_original_commands import changed_as
from emulate_original_charge_gates import GateMachine
from emulate_original_power_paths import FLAGS, IMAGE, IMAGE_SHA256

DSP, INPUT, CHANGE, SLOTS = 0x20002274, 0x20000690, 0x20000696, 0x20002508
EVENT_ID = 0x200004f5
ENTRY, REMOVAL, CALLBACK = 0x0802169c, 0x08021710, 0x080216b4


class InputMachine(GateMachine):
    def __init__(self, image: bytes):
        self.sampling = False
        super().__init__(image)

    def step(self, uc, address, size, data):
        if self.sampling and address == 0x08020952 and uc.reg_read(UC_ARM_REG_R5) == 1:
            # End after actual slots0/1; the other four qualification slots are
            # unrelated and may involve peripherals which are deliberately absent.
            self.stopped = True
            uc.emu_stop()
        else:
            super().step(uc, address, size, data)

    def setup(self):
        self.seed(outputs=0x40000012)
        self.uc.mem_write(SLOTS, bytes(40))
        self.run(0x0800ddfc, stop=0x0800de20)
        assert self.get(SLOTS + 0x14) == ENTRY | 1
        assert self.get(SLOTS + 0x18) == REMOVAL | 1
        assert self.get(SLOTS + 0x1c) == CALLBACK | 1
        assert self.get(SLOTS + 0x22, "B") == 200
        self.put(INPUT, 0xa0, "B")
        self.put(CHANGE, 0, "H")
        self.put(EVENT_ID, 9, "B")
        self.uc.mem_write(DSP, bytes(0x50))
        self.events.clear()

    def raw(self, *, dc: int = 0, ac: bool = False, ac_fault: int = 0):
        self.put(DSP + 0x4c, dc, "H")
        self.put(DSP, int(ac), "B")
        self.put(DSP + 2, ac_fault, "H")
        self.put(DSP + 4, 0, "B")
        self.put(DSP + 8, 0, "B")

    def sample(self):
        before, flags = self.low_ram(), self.get(FLAGS)
        self.events.clear()
        self.sampling = True
        try:
            self.run(0x080208a8, stop=0x0802095a)
        finally:
            self.sampling = False
        allowed = {INPUT: bytes(self.uc.mem_read(INPUT, 1)),
                   CHANGE: bytes(self.uc.mem_read(CHANGE, 2))}
        for slot in (SLOTS, SLOTS + 20):
            for offset, size in ((0xc, 1), (0xd, 1), (0x10, 2), (0x12, 2)):
                allowed[slot + offset] = bytes(self.uc.mem_read(slot + offset, size))
        changed_as(before, self.low_ram(), allowed)
        assert self.get(FLAGS) == flags and not self.calls and not self.queued and not self.replies
        return {"qualified_input_byte": self.get(INPUT, "B"),
                "change_flags": self.get(CHANGE, "H"),
                "ac_state": self.get(SLOTS + 0xc, "B"),
                "ac_counter": self.get(SLOTS + 0xd, "B"),
                "dc_state": self.get(SLOTS + 0x20, "B"),
                "dc_counter": self.get(SLOTS + 0x21, "B"),
                "events": list(self.events), "power_state_unchanged": True}

    def samples(self, count: int):
        result = None
        for _ in range(count):
            result = self.sample()
        assert result is not None
        return result


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    m = InputMachine(image)
    rows, counts = [], Counter()

    def record(group, data):
        rows.append([group, data])
        counts[group] += 1

    m.setup()
    record("actual_second_slot_registration", {
        "slot": f"{SLOTS + 20:08x}", "entry": f"{ENTRY:08x}",
        "removal": f"{REMOVAL:08x}", "callback": f"{CALLBACK:08x}", "threshold": 200})
    for high, low in itertools.product((0, 1, 0x80, 0xff), range(256)):
        word = high * 256 + low
        m.raw(dc=word)
        before = m.low_ram()
        m.run(ENTRY)
        entry = m.uc.reg_read(UC_ARM_REG_R0)
        m.run(REMOVAL)
        removal = m.uc.reg_read(UC_ARM_REG_R0)
        assert entry == int(bool(word & 0x10)) and removal == 1 - entry
        assert m.low_ram() == before and not m.events and not m.calls and not m.queued
        record("dc_predicate_words", {"dsp_word_4c": word, "entry": entry, "removal": removal})

    for qualified, mask in itertools.product(range(4), (0, 1, 2, 3, 4, 0xffff)):
        m.setup()
        m.put(INPUT, 0xa0 | qualified, "B")
        m.put(CHANGE, 0x4000, "H")
        before = m.low_ram()
        m.run(CALLBACK, mask)
        enabled = bool(mask & 2)
        removed = bool(mask & 1) and not enabled
        expected = ((0xa0 | qualified | 2) if enabled
                    else ((0xa0 | qualified) & ~2) if removed else 0xa0 | qualified)
        changes = 0x4000 | (2 if enabled else 0x80 if removed else 0)
        changed_as(before, m.low_ram(), {INPUT: bytes((expected,)), CHANGE: struct.pack("<H", changes)})
        assert m.events == ([{"kind": "event_queue", "id": 9, "data": 0}]
                            if enabled or removed else [])
        assert not m.calls and not m.queued and not m.replies
        record("callback_mask_and_unrelated_bits", {"qualified_before": qualified, "mask": mask,
                                                    "qualified_after": expected,
                                                    "change_flags": changes, "event_count": len(m.events)})

    m.setup()
    m.raw(dc=0x10)
    prior, final = m.samples(201), m.sample()
    assert prior["qualified_input_byte"] == 0xa0 and prior["dc_counter"] == 201
    assert final["qualified_input_byte"] == 0xa2 and final["dc_counter"] == 0
    assert final["events"] == [{"kind": "event_queue", "id": 9, "data": 0}]
    record("qualification_sequences", {"sequence": "fresh_dc_entry", "transition_sample": 202,
                                       "before": prior, "after": final})

    m.setup()
    m.raw(dc=0)
    blocked = m.samples(10)
    assert blocked["dc_counter"] == 1
    m.raw(dc=0x10)
    prior, final = m.samples(200), m.sample()
    assert prior["qualified_input_byte"] == 0xa0 and final["qualified_input_byte"] == 0xa2
    record("qualification_sequences", {"sequence": "invalid_then_valid_entry", "valid_transition_sample": 201,
                                       "blocked": blocked, "before": prior, "after": final})

    m.setup()
    m.raw(dc=0x10)
    m.samples(202)
    m.put(CHANGE, 0, "H")
    m.raw(dc=0)
    prior, final = m.samples(201), m.sample()
    assert prior["qualified_input_byte"] == 0xa2 and final["qualified_input_byte"] == 0xa0
    assert final["change_flags"] == 0x80
    record("qualification_sequences", {"sequence": "dc_removal", "transition_sample": 202,
                                       "before": prior, "after": final})

    m.setup()
    m.raw(dc=0x10)
    m.samples(202)
    m.raw(dc=0xffff)
    final = m.samples(300)
    assert final["qualified_input_byte"] == 0xa2 and final["dc_counter"] == 0 and not final["events"]
    record("qualification_sequences", {"sequence": "other_dc_word_bits_after_entry", "samples": 300,
                                       "after": final})

    m.setup()
    m.raw(dc=0x10)
    m.samples(202)
    m.raw(dc=0)
    interrupted = m.samples(100)
    assert interrupted["dc_counter"] == 100
    m.raw(dc=0x10)
    returned = m.sample()
    assert returned["dc_counter"] == 0
    m.raw(dc=0)
    prior, final = m.samples(201), m.sample()
    assert prior["qualified_input_byte"] == 0xa2 and final["qualified_input_byte"] == 0xa0
    record("qualification_sequences", {"sequence": "dc_removal_interrupted", "first_absence": interrupted,
                                       "presence_return": returned, "before": prior, "after": final})

    m.setup()
    m.raw(dc=0x10, ac=True)
    prior, final = m.samples(201), m.sample()
    assert prior["qualified_input_byte"] == 0xa0 and final["qualified_input_byte"] == 0xa3
    assert final["change_flags"] == 3 and len(final["events"]) == 2
    record("qualification_sequences", {"sequence": "simultaneous_ac_dc_entry", "transition_sample": 202,
                                       "slot_order": ["AC", "second input"], "before": prior, "after": final})

    handoffs = []
    for name, raw_ac, fault, preparation, group, expected in (
            ("DC entry without AC", False, 0, "none", 3, 1),
            ("DC entry with raw AC but blocked AC qualification", True, 1, "none", 3, 1),
            ("AC entry after qualified DC", True, 0, "dc", 5, 0x20),
            ("AC removal while DC remains qualified", False, 0, "both", 6, 1)):
        m.setup()
        if preparation == "dc":
            m.raw(dc=0x10)
            m.samples(202)
            m.put(FLAGS, 0x40000013)
        elif preparation == "both":
            m.raw(dc=0x10, ac=True)
            m.samples(202)
            m.put(FLAGS, 0x40000032)
        m.put(CHANGE, 0, "H")
        m.raw(dc=0x10, ac=raw_ac, ac_fault=fault)
        qualification = m.samples(202)
        m.events.clear()  # Captured event delivery is not actually dispatched.
        rule = m.rule(group)
        assert rule["source_bits_after"] == expected
        assert m.get(FLAGS) & ~0x21 == 0x40000012
        row = {"scenario": name, "raw_ac_presence": raw_ac, "raw_ac_fault_word": fault,
               "qualified_inputs": qualification["qualified_input_byte"] & 3,
               "qualifier": qualification, "explicit_synthetic_rule_group": group, "rule": rule}
        handoffs.append(row)
        record("qualified_input_to_explicit_rule", row)

    # Check only direct handler membership, including the separate diagnostic
    # tables. This is not an exhaustive indirect-reachability proof.
    for name, address, count in (("app0f", 0x20000254, 32), ("module10", 0x20000154, 32),
                                 ("library01", 0x200009d8, 14),
                                 ("diagnostic0", 0x08029b30, 40), ("diagnostic1", 0x08029c70, 8)):
        handlers = {int(row["handler"], 16) for row in m.table(address, count)}
        assert not handlers.intersection((ENTRY, REMOVAL, CALLBACK))
        record("no_direct_qualifier_command_entry", {"table": name, "entries": count})

    assert counts == {"actual_second_slot_registration": 1, "dc_predicate_words": 1024,
                      "callback_mask_and_unrelated_bits": 24, "qualification_sequences": 6,
                      "qualified_input_to_explicit_rule": 4, "no_direct_qualifier_command_entry": 5}
    return {"model": "A1761 original C1000", "main_version": "1.5.9", "input_sha256": IMAGE_SHA256,
            "cases": dict(counts) | {"total": len(rows)},
            "cases_sha256": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest(),
            "qualification_sequences": [row[1] for row in rows if row[0] == "qualification_sequences"],
            "explicit_rule_handoffs": handoffs,
            "dc_predicate": "DSP uint16[4c] bit4 only; removal is the inverse of that bit",
            "registration": {"slot": "2000251c", "threshold": 200,
                             "entry": f"{ENTRY:08x}", "removal": f"{REMOVAL:08x}", "callback": f"{CALLBACK:08x}"},
            "substitutes": {"08010278": "event capture, no dispatcher", "08006b38": "alarm capture; healthy cases",
                            "sampler boundary": "stop after slot1, before other qualification slots"},
            "limits": ["Available main1.5.9 differs from installed1.7.1; DSP execution, original radio image and physical inputs absent.",
                       "Other DSP word bits have unassigned meanings; qualification is not a complete health/protection test.",
                       "No wall-clock transition guarantee; real DSP cache and scheduling may add latency.",
                       "Rule groups3/5/6 invoked explicitly after qualification, not through the complete event dispatcher.",
                       "Output/power RAM preserved synchronously; no physical converter/relay behavior is established.",
                       "Direct command-table absence does not exclude every indirect path or firmware version.",
                       "No external charging pause, source override or force-battery command established."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    sources = [Path(__file__)] + [Path(__file__).with_name(name) for name in (
        "emulate_original_charge_gates.py", "emulate_original_charge_source.py", "emulate_original_fast_retention.py",
        "emulate_original_power_paths.py", "emulate_original_timers_modes.py", "emulate_c1000_original_commands.py")]
    manifest = {"firmware_sha256": IMAGE_SHA256,
                "sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
                "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "cases": result["cases"]["total"]}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{result['cases']['total']} offline original DC-input qualification cases passed")


if __name__ == "__main__":
    main()
