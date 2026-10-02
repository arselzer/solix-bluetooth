#!/usr/bin/env python3
"""Original A1761 1.5.9 input-event charging rules and second producer.

Actual initialization, immutable rule selection, predicates/actions and MCU
queue builders run against synthetic RAM. DSP, network and devices are absent.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R6

from emulate_c1000_original_commands import changed_as
from emulate_original_charge_source import SourceMachine
from emulate_original_fast_retention import BMS, INPUT_FLAGS
from emulate_original_power_paths import (
    AC_STATE, CEILING, DESCRIPTOR, FLAGS, IMAGE, IMAGE_SHA256,
    PAYLOAD, RULE_TABLES, VARIANT,
)

REGISTRY = 0x200020f0
TEMPERATURES = 0x200004fa
SECOND_STATE = 0x2000000c
SOURCE_MASK = 0x21


class GateMachine(SourceMachine):
    def reset(self):
        super().reset()
        self.selected_rows = []
        self.actions = []
        self.events = []
        # Execute only the real twelve rule-table registrations. No timers or
        # later initialization in the enclosing routine are executed.
        self.run(0x08006a4e, stop=0x08006ac2)
        assert struct.unpack("<12I", self.uc.mem_read(REGISTRY, 48)) == RULE_TABLES

    def step(self, uc, address, size, data):
        if address == 0x08015184:
            self.selected_rows.append(uc.reg_read(UC_ARM_REG_R6))
        if address in (0x080241d8, 0x08024494):
            pointer = uc.reg_read(UC_ARM_REG_R0)
            self.actions.append({"gate_bit": 5 if address == 0x080241d8 else 0,
                                 "argument": self.get(pointer, "H")})
        if address == 0x08010278:
            self.events.append({"kind": "event_queue", "id": uc.reg_read(UC_ARM_REG_R0),
                                "data": uc.reg_read(UC_ARM_REG_R1)})
            self.back(0)
        elif address == 0x08006b38:
            self.events.append({"kind": "alarm", "code": uc.reg_read(UC_ARM_REG_R0)})
            self.back(0)
        else:
            super().step(uc, address, size, data)

    def seed(self, *, source=0, present=0, outputs=0x12, temperatures=(25, 25),
             critical=False, variant=0):
        self.prepare(outputs | source | (0x100000 if critical else 0),
                     input_present=present)
        self.put(VARIANT, variant, "B")
        self.put(BMS+8, 38000)
        self.put(BMS+0x23, 50, "B")
        low, high = temperatures
        self.put(BMS+0x20, high, "b")
        self.put(BMS+0x21, low, "b")
        self.put(TEMPERATURES, high, "b")
        self.put(TEMPERATURES+1, low, "b")

    def rule(self, group):
        before = self.low_ram()
        flags = self.get(FLAGS)
        self.run(0x08015120, group)
        after = self.get(FLAGS)
        changed_as(before, self.low_ram(), {FLAGS: struct.pack("<I", after)})
        assert after & 0x12 == flags & 0x12
        assert not self.queued and not self.calls and not self.replies
        return {"selected_rows": self.selected_rows.copy(),
                "gate_actions": self.actions.copy(), "events": self.events.copy(),
                "source_bits_before": flags & SOURCE_MASK,
                "source_bits_after": after & SOURCE_MASK,
                "protected_output_bits_unchanged": True,
                "bc_code_after": self.readback()["bc_code"]}

    def producer(self, channel):
        before_flags = self.get(FLAGS)
        if channel == 2:
            enabled = bool(before_flags & 1)
            self.run(0x08020380, stop=0x080205f2 if enabled else 0x080206dc)
        else:
            enabled = bool(before_flags & 0x20)
            self.run(0x0801f570, stop=0x0801f6a2 if enabled else 0x0801f762)
        assert self.get(FLAGS) == before_flags and not self.events and not self.calls


def run_suite(image):
    if not __debug__:
        raise RuntimeError("Assertions are required; do not use Python -O")
    m = GateMachine(image)
    m.seed()
    result = {"image_sha256": IMAGE_SHA256,
              "scope": "Original main1.5.9 only; synthetic rule events/descriptors; no installed1.7.1 or electrical validation",
              "registration": [{"callbacks": [f"{x:08x}" for x in RULE_TABLES],
                  "startup_block": "08006a4e..08006ac2"}],
              "healthy_rules": [], "temperature_rules": [], "critical_rules": [],
              "bit0_setter": [], "channel2_producer": [],
              "shared_descriptor": [], "gate_handler_tables": []}
    for group, present, source in itertools.product(range(3, 7), range(4), (0, 0x21)):
        m.seed(source=source, present=present)
        row = m.rule(group)
        if group == 3:
            expected = (source & 0x20) | (0 if present & 1 else 1)
            selected = 2 if present & 1 else 1
        elif group == 4:
            expected, selected = source & 0x20, 1
        elif group == 5:
            expected, selected = 0x20, 1
        else:
            expected, selected = (1 if present & 2 else 0), (2 if present & 2 else 1)
        assert row["source_bits_after"] == expected and row["selected_rows"] == [selected]
        result["healthy_rules"].append({"group": group, "qualified_input_bits": present,
                                       **row})

    for group, temperatures in itertools.product((3, 5, 6),
            ((1,1), (2,2), (55,55), (56,56), (1,25))):
        m.seed(source=0x21, present=2, temperatures=temperatures)
        row = m.rule(group)
        normal = temperatures in ((2,2), (55,55))
        mixed = temperatures == (1,25)
        if mixed:
            assert row["selected_rows"] == [] and row["source_bits_after"] == 0x21
        else:
            expected = (0x21 if group == 3 else 0x20 if group == 5 else 1) if normal else (
                0x20 if group == 3 else 0)
            selected = ({3:1,5:1,6:2}[group] if normal else
                        {3:3,5:2,6:3}[group] if temperatures == (1,1) else
                        {3:4,5:3,6:4}[group])
            assert row["source_bits_after"] == expected and row["selected_rows"] == [selected]
        result["temperature_rules"].append({"group": group,
            "synthetic_low_high_c": list(temperatures), **row,
            "not_a_safe_temperature_or_physical_protection_claim": True})

    for group in range(3, 7):
        m.seed(source=0x21, present=3, critical=True)
        row = m.rule(group)
        assert row["selected_rows"] == [0]
        assert row["source_bits_after"] == (0x20 if group in (3,4) else 0)
        result["critical_rules"].append({"group": group,
                                         "synthetic_global_bit20": True, **row})

    for flags, argument in itertools.product((0,0x12,0x32,0x33), (0,1,2,0x2f)):
        m.seed(source=flags & SOURCE_MASK, outputs=flags & ~SOURCE_MASK)
        before = m.low_ram()
        m.put(PAYLOAD, argument, "H")
        m.run(0x08024494, PAYLOAD)
        after = flags if argument == 0x2f else (flags & ~1) | (argument & 1)
        changed_as(before, m.low_ram(), {FLAGS: struct.pack("<I", after)})
        assert m.get(FLAGS) == after
        result["bit0_setter"].append({"before_flags": flags,
            "argument": argument, "after_flags": after,
            "only_bit0_can_change": True})

    producer_cases = []
    for enabled, existing, dirty, variant in (
            *itertools.product((False,), (False,True), (False,), (0,1)),
            *itertools.product((True,), (True,), (False,True), (0,1)),
            *itertools.product((True,), (False,), (True,), (0,1))):
        producer_cases.append((enabled,existing,dirty,variant,300,392,1000))
    producer_cases.extend((True,True,True,variant,0,392,0) for variant in (0,1))
    for enabled, existing, dirty, variant, power, voltage, current in producer_cases:
        m.seed(source=int(enabled), variant=variant)
        m.uc.mem_write(DESCRIPTOR, struct.pack("<3HB",power,voltage,current,int(dirty)))
        m.put(SECOND_STATE+1, int(existing), "B")
        m.put(SECOND_STATE+2, int(existing), "B")
        m.put(0x200020c8+8*2, 1234, "H")
        m.producer(2)
        assert len(m.queued) == int((enabled and (dirty or not existing)) or
                                   (not enabled and existing))
        if not enabled and existing:
            assert m.queued == [{"register": 0x15, "payload": "5800"}]
        elif enabled and (dirty or not existing):
            assert m.queued[0]["register"] == 0x16
            words = list(struct.unpack("<6H", bytes.fromhex(m.queued[0]["payload"])))
            if existing:
                assert words == [380,3920, max(10,min(current//10,80 if variant else 160))*10,
                                 min(power,288 if variant else 580),4000,2800]
        if not enabled:
            assert m.get(0x200020c8+8*2, "H") == 0
        result["channel2_producer"].append({"enabled_bit0": enabled,
            "existing_session": existing, "descriptor_dirty": dirty,
            "synthetic_variant": variant, "requested_descriptor": [power,voltage,current],
            "queued": m.queued.copy(), "power_cache8_after": m.get(0x200020c8+8*2, "H"),
            "output_and_gate_flags_unchanged": True})

    for order in ((1,2), (2,1)):
        m.seed(source=0x21)
        m.uc.mem_write(DESCRIPTOR, struct.pack("<3HB",300,392,1000,1))
        m.put(AC_STATE+3,1,"B")
        m.put(SECOND_STATE+1,1,"B")
        counts=[]
        for channel in order:
            m.producer(channel)
            counts.append(len(m.queued))
        assert counts == [1,1] and m.get(DESCRIPTOR+6,"B") == 0
        assert m.queued[0]["register"] == (4 if order[0] == 1 else 0x16)
        result["shared_descriptor"].append({"synthetic_both_gates_set": True,
            "producer_order": list(order), "queue_counts": counts,
            "queued": m.queued.copy(), "shared_dirty_byte_consumed_by_first": True,
            "not_a_real_scheduler_or_dual_source_trial": True})

    m.seed()
    for address, count, name in ((0x20000254,32,"function0f_app"),
                                  (0x20000154,32,"function10_module"),
                                  (0x200009d8,14,"function01_library")):
        handlers = [row["handler"] for row in m.table(address,count)]
        assert "08024494" not in handlers and "080241d8" not in handlers
        result["gate_handler_tables"].append({"name":name,"entries":count,
            "gate_setters_absent_as_direct_handlers": True,
            "not_an_indirect_reachability_proof": True})
    result["cases"] = sum(len(value) for value in result.values() if isinstance(value,list))
    assert result["cases"] == 85
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image",type=Path,default=IMAGE)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--manifest",type=Path)
    args=parser.parse_args()
    result=run_suite(args.image.read_bytes())
    result["script_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    if args.manifest:
        files=[Path(__file__),*[Path(__file__).with_name(name) for name in (
            "emulate_c1000_original_commands.py","emulate_original_power_paths.py",
            "emulate_original_timers_modes.py","emulate_original_fast_retention.py",
            "emulate_original_charge_source.py","requirements.txt")]]
        args.manifest.write_text(json.dumps({"firmware_sha256":IMAGE_SHA256,
            "case_count":result["cases"],"python":platform.python_version(),
            "unicorn":unicorn.__version__,"sha256":{
                path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
            "result_sha256":hashlib.sha256(args.output.read_bytes()).hexdigest(),
            "substitutes":["event enqueue","alarm delivery","DSP allocation/enqueue"],
            "not_executed":["device","radio","MQTT","DSP","flash","hardware",
                            "whole event dispatcher","scheduler","installed main1.7.1"]},indent=2)+"\n")
    print(f"Passed {result['cases']} synthetic charging-gate cases")


if __name__ == "__main__":
    main()
