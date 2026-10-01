#!/usr/bin/env python3
"""Offline original C1000 1.5.9 AC Smart qualification and counter history.

Runs public MCU instructions against synthetic DSP, GPIO and timer state.
Stop events, diagnostic replies and GPIO configuration writes are captured
inside Unicorn. No devices, real peripherals, transport or output dispatch.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R10,
)

from emulate_c1000_original_commands import CTX, IMAGE, IMAGE_SHA256, PAYLOAD
from emulate_c1000_smart_policy import CONFIG, DSP, TICK, TIMERS, PolicyMachine

INPUT = 0x20000690
DEBOUNCE = 0x20002508
GPIO = 0x40011400
COUNTER = CONFIG["ac"]["counter"]
OUTPUT_FLAGS = 0x200004fc


class BlockerMachine(PolicyMachine):
    def __init__(self, image):
        self.gpio_writes = []
        self.diagnostic_replies = []
        self.allow_gpio_config = False
        super().__init__(image)

    def write(self, uc, access, address, size, value, data):
        if self.allow_gpio_config and address == GPIO and size == 4:
            self.gpio_writes.append([address, value])
            return
        super().write(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        if address == 0x0800d6b0:
            self.diagnostic_replies.append({
                "source": uc.reg_read(UC_ARM_REG_R0),
                "tag": uc.reg_read(UC_ARM_REG_R1),
                "length": uc.reg_read(UC_ARM_REG_R2),
                "payload": bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R3),
                                             uc.reg_read(UC_ARM_REG_R2))).hex(),
            })
            self.back(0)
            return
        super().step(uc, address, size, data)

    def setup(self, *, inherited=0, smart=True):
        self.prepare("ac", mode=2 if smart else 1, counter=inherited)
        self.put(OUTPUT_FLAGS, 0x10)
        self.put(0x200004f5, 9, "B")  # Synthetic input-event ID; stop ID is 7.
        self.put(INPUT, 0xa0, "B")  # Preserve unrelated input bits.
        self.uc.mem_write(DEBOUNCE, bytes(0x14))
        # Execute actual registration of the complete first debounce slot.
        self.run(0x0800ddfc, stop=0x0800de10)
        assert [self.get(DEBOUNCE + off) for off in (0, 4, 8)] == [
            0x08021015, 0x080210a1, 0x08021045]
        assert self.get(DEBOUNCE + 0xe, "B") == 200

    def raw_input(self, present=1, fault_word=0, fault_byte=0, extra=0):
        self.put(DSP, present, "B")
        self.put(DSP + 2, fault_word, "H")
        self.put(DSP + 4, fault_byte, "B")
        self.put(DSP + 8, extra, "B")

    def qualification_sample(self):
        self.events.clear()
        # Actual callback prologue and first table entry, stopping before the
        # other five unrelated USB/DC entries are processed.
        self.run(0x080208a8, stop=0x08020952)
        return {"state": self.get(DEBOUNCE + 0xc, "B"),
                "counter": self.get(DEBOUNCE + 0xd, "B"),
                "global_bit": self.get(INPUT, "B") & 1,
                "events": list(self.events)}

    def policy_sample(self, advance=True):
        self.events.clear()
        if advance:
            self.put(TICK, (self.get(TICK) + 2000) & 0xffffffff)
            self.run(0x08010448)
        count, stopped = self.sample("ac")
        return {"counter": count, "stop_requested": stopped,
                "events": list(self.events)}


def run_suite(image):
    if not __debug__:
        raise RuntimeError("Assertions are required; do not use Python -O")
    m = BlockerMachine(image)
    result = {"image_sha256": IMAGE_SHA256, "unicorn_version": unicorn.__version__,
        "scope": "Public original MainMcu 1.5.9; synthetic DSP/GPIO/ticks; no 1.7.1 or hardware validation",
        "gpio_configuration": [], "gpio_reader": [], "gpio_diagnostic": [],
        "dsp_predicates": [], "qualification_sequences": [], "timer_registration": [],
        "counter_history": [], "output_lifecycle": []}

    m.setup()
    m.put(TIMERS, 1, "B")  # Reserve timer zero, as firmware startup does.
    m.run(0x0800ddfc)
    timer_id = m.get(INPUT + 5, "B")
    timer = TIMERS + timer_id * 20
    assert timer_id == 2 and m.get(timer, "B") == 2
    assert m.get(timer + 1, "B") == 1 and m.get(timer + 4) == 10
    assert m.get(timer + 0xc) == 0x080208a9
    result["timer_registration"].append({"allocated_id": timer_id,
        "mode": m.get(timer + 1, "B"), "period_ticks": m.get(timer + 4),
        "callback": f"{m.get(timer + 0xc):08x}",
        "note": "Actual registration; periodic interrupt/scheduler execution not simulated"})

    m.setup()
    m.put(GPIO, 0x87654321)
    m.allow_gpio_config = True
    try:
        m.run(0x08008064)
    finally:
        m.allow_gpio_config = False
    assert m.get(GPIO) == 0x87654421
    assert m.gpio_writes == [[GPIO, 0x87654421]]
    result["gpio_configuration"].append({"before": "87654321", "after": "87654421",
        "pin2_configuration_nibble": 4,
        "note": "Numeric configuration proven; no electrical board signal name assigned"})

    for input_value, output_value in itertools.product((0, 4, 0xffff0000, 0xffffffff), (0, 4)):
        m.setup()
        m.put(GPIO + 8, input_value)
        m.put(GPIO + 0xc, output_value)
        m.run(0x08007fb8, 3, registers={UC_ARM_REG_R1: 2})
        actual = m.uc.reg_read(UC_ARM_REG_R0)
        assert actual == int(bool(input_value & 4))
        result["gpio_reader"].append({"input_register": input_value,
            "output_register": output_value, "reader_result": actual})

    # Existing firmware property-table entry F0 delegates to a GPIO report.
    # This does not establish a public BLE/native command envelope for it.
    assert image[0x08029be0 - 0x08005000:0x08029be8 - 0x08005000] == bytes.fromhex(
        "f00000005f250108")
    for pin in (0, 1):
        m.setup()
        m.put(GPIO + 8, pin * 4)
        m.put(CTX + 4, 0x22, "B")
        m.put(CTX + 5, 0xf0, "H")
        m.diagnostic_replies.clear()
        m.run(0x0801255e, CTX)
        expected = {"source": 0x22, "tag": 0xf0, "length": 1, "payload": f"{pin:02x}"}
        assert m.diagnostic_replies == [expected]
        result["gpio_diagnostic"].append({"synthetic_pin": pin, **expected})

    for present, fault_word, fault_byte, extra in itertools.product(
            (0, 1), (0, 1, 0x2000, 0x4000, 0x8000), (0, 1), (0, 1, 2)):
        m.setup()
        m.raw_input(present, fault_word, fault_byte, extra)
        m.run(0x08021014)
        enter = m.uc.reg_read(UC_ARM_REG_R0)
        m.run(0x080210a0)
        leave = m.uc.reg_read(UC_ARM_REG_R0)
        assert enter == int(bool(present & 1) and not (fault_word & 0x3fff)
                            and not fault_byte and not (extra & 1))
        assert leave == int(not (present & 1))
        result["dsp_predicates"].append({"dsp_status_byte": present,
            "dsp_fault_word": fault_word, "dsp_byte4": fault_byte,
            "dsp_byte8": extra, "entry_predicate": enter, "removal_predicate": leave})

    m.setup()
    m.raw_input()
    rows = {}
    for sample in range(1, 204):
        row = m.qualification_sample()
        assert row["global_bit"] == int(sample >= 202)
        assert row["events"] == ([[9, 0]] if sample == 202 else [])
        if sample in (1, 200, 201, 202, 203):
            rows[str(sample)] = row
    assert m.get(INPUT, "B") == 0xa1
    result["qualification_sequences"].append({"sequence": "fresh_entry",
        "checkpoints": rows, "unrelated_global_bits_preserved": True})

    m.raw_input(fault_word=0x2000, fault_byte=1, extra=1)
    for _ in range(300):
        row = m.qualification_sample()
        assert row == {"state": 1, "counter": 0, "global_bit": 1, "events": []}
    result["qualification_sequences"].append({"sequence": "fault_after_entry",
        "samples": 300, "final": row})

    m.raw_input(0)
    rows = {}
    for sample in range(1, 204):
        row = m.qualification_sample()
        assert row["global_bit"] == int(sample < 202)
        assert row["events"] == ([[9, 0]] if sample == 202 else [])
        if sample in (1, 201, 202, 203):
            rows[str(sample)] = row
    assert m.get(INPUT, "B") == 0xa0
    result["qualification_sequences"].append({"sequence": "qualified_removal",
        "checkpoints": rows, "unrelated_global_bits_preserved": True})

    m.setup()
    m.raw_input(1, fault_word=1)
    for _ in range(25):
        row = m.qualification_sample()
        assert row == {"state": 0, "counter": 1, "global_bit": 0, "events": []}
    m.raw_input()
    for sample in range(1, 202):
        row = m.qualification_sample()
        assert row["global_bit"] == int(sample == 201)
    result["qualification_sequences"].append({"sequence": "blocked_then_good_entry",
        "initial_blocked_samples": 25, "good_samples_to_entry": 201, "final": row})

    # With an active Smart timer, a temporary blocker is not sampled at all.
    # It therefore cannot be relied on to clear an inherited counter.
    for blocker in ("global", "gpio", "countdown"):
        m.setup(inherited=451, smart=False)
        m.put(TIMERS + 20, 2, "B")
        m.put(TIMERS + 28, m.get(TICK))
        if blocker == "global":
            m.run(0x08021044, 2)
        elif blocker == "gpio":
            m.put(GPIO + 8, 0)
        else:
            m.put(0x200020c4, 1, "B")
        skipped = m.policy_sample(advance=False)
        assert skipped["counter"] == 451 and not skipped["stop_requested"]
        m.put(PAYLOAD + 6, 1, "B")
        m.run(0x0800bd28, CTX)
        if blocker == "global":
            m.run(0x08021044, 1)
        elif blocker == "gpio":
            m.put(GPIO + 8, 4)
        else:
            m.put(0x200020c4, 0, "B")
        next_sample = m.policy_sample()
        assert next_sample == {"counter": 0, "stop_requested": True, "events": [[7, 0]]}
        assert m.get(OUTPUT_FLAGS) == 0x10
        result["counter_history"].append({"sequence": "blocker_only_while_timer_active",
            "blocker": blocker, "skipped": skipped, "next_eligible_sample": next_sample,
            "output_dispatch_not_executed": True})

    # A blocker that overlaps an eligible sample discards inherited progress.
    for blocker in ("global", "gpio", "countdown"):
        m.setup(inherited=451, smart=True)
        if blocker == "global":
            m.run(0x08021044, 2)
        elif blocker == "gpio":
            m.put(GPIO + 8, 0)
        else:
            m.put(0x200020c4, 1, "B")
        blocked = m.policy_sample()
        assert blocked == {"counter": 1, "stop_requested": False, "events": []}
        if blocker == "global":
            m.run(0x08021044, 1)
        elif blocker == "gpio":
            m.put(GPIO + 8, 4)
        else:
            m.put(0x200020c4, 0, "B")
        released = m.policy_sample()
        assert released == {"counter": 2, "stop_requested": False, "events": []}
        result["counter_history"].append({"sequence": "blocker_observed_at_eligible_sample",
            "blocker": blocker, "blocked": blocked, "released": released})

    # Scheduling order can matter at the synthetic qualification boundary.
    # GPIO is deliberately held high; real electrical timing is not modeled.
    for before_qualification in (True, False):
        m.setup(inherited=451, smart=True)
        m.raw_input()
        for _ in range(201):
            m.qualification_sample()
        if not before_qualification:
            qualification = m.qualification_sample()
            assert qualification["global_bit"] == 1
        policy = m.policy_sample()
        assert policy["stop_requested"] == before_qualification
        assert policy["counter"] == (0 if before_qualification else 1)
        result["counter_history"].append({"sequence": "entry_and_policy_order",
            "policy_before_qualifying_sample_202": before_qualification,
            "gpio_held_high_synthetically": True, "policy": policy})

    # Actual lifecycle reset blocks, without the surrounding output commands.
    for inherited in (1, 451, 65535):
        m.setup(inherited=inherited)
        m.put(OUTPUT_FLAGS, 0)
        m.run(0x0801f762, stop=0x0801fa6a, registers={
            UC_ARM_REG_R4: 0, UC_ARM_REG_R6: 0x2000001c,
            UC_ARM_REG_R10: OUTPUT_FLAGS})
        assert m.get(COUNTER, "H") == 0 and not m.events
        assert m.get(CONFIG["ac"]["cache"], "H") == 0
        result["output_lifecycle"].append({"path": "outer_output_off_gate",
            "counter_before": inherited, "counter_after": 0})
        m.put(COUNTER, inherited, "H")
        m.run(0x0801f806, stop=0x0801f80c, registers={
            UC_ARM_REG_R4: 0, UC_ARM_REG_R6: 0x2000001c,
            # The initialization path has r7=1; only the RAM reset tail runs.
            UC_ARM_REG_R7: 1})
        assert m.get(COUNTER, "H") == 0
        result["output_lifecycle"].append({"path": "initialization_tail_only",
            "counter_before": inherited, "counter_after": 0})

    result["cases"] = sum(len(value) for value in result.values() if isinstance(value, list))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE,
                        help="Public original 1.5.9 MainMcu image; SHA-256 checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
