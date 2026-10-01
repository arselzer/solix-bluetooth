#!/usr/bin/env python3
"""Replay original C1000 1.5.9 charging paths with synthetic RAM and queues.

No transport or physical DSP is present. Queue allocation/enqueue operations
are substituted; controller packet construction and gate branches execute.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_c1000_original_commands import CTX, IMAGE, IMAGE_SHA256, Machine, OUTPUT, PAYLOAD

FLAGS = 0x200004fc
DESCRIPTOR = 0x20000424
AC_STATE = 0x2000001c
VARIANT = 0x20000454
CEILING = 0x20002040
RULE_TABLES = (
    0x08028d3c, 0x08028d8c, 0x08028c4c, 0x08028e2c,
    0x08028e2c, 0x08028e2c, 0x08028e2c, 0x08028c9c,
    0x08028ddc, 0x08028cec, 0x08028ecc, 0x08028e7c,
)


class PowerMachine(Machine):
    def __init__(self, image: bytes):
        self.queued = []
        super().__init__(image)

    def reset(self):
        super().reset()
        self.queued = []

    def step(self, uc, address, size, data):
        if address == 0x0801c8ec:
            # Synthetic allocation; only one command is pending in each case.
            length = uc.reg_read(UC_ARM_REG_R0)
            assert 0 < length <= 32
            uc.mem_write(OUTPUT, bytes(32))
            self.back(OUTPUT)
        elif address == 0x0800ed5c:
            assert uc.reg_read(UC_ARM_REG_R0) == 0x20002170
            pointer = uc.reg_read(UC_ARM_REG_R1)
            register, length, payload = struct.unpack("<HHI", uc.mem_read(pointer + 8, 8))
            assert payload == OUTPUT and 0 < length <= 32
            self.queued.append({"register": register,
                                "payload": bytes(uc.mem_read(payload, length)).hex()})
            self.back(1)
        else:
            super().step(uc, address, size, data)

    def put(self, address, value, kind="I"):
        self.uc.mem_write(address, struct.pack("<" + kind, value))

    def get(self, address, kind="I"):
        return struct.unpack("<" + kind, self.uc.mem_read(address, struct.calcsize(kind)))[0]


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Assertions are required; do not run this replay with Python -O")
    m = PowerMachine(image)
    results = {"image_sha256": IMAGE_SHA256, "unicorn_version": unicorn.__version__,
               "scope": "Synthetic original A1761 main 1.5.9; no radio or DSP execution",
               "app_ceiling": [], "internal_gate": [], "descriptor_and_producer": [],
               "gate_branches": [], "rule_tables": []}
    seeds = (0, 0x10, 0x20, 0x30, 0x12340030)
    for flags, watts in itertools.product(seeds, (0, 1, 99, 100, 750, 1000, 1200, 65535)):
        m.reset()
        m.put(FLAGS, flags)
        m.put(PAYLOAD + 6, watts, "H")
        m.run(0x0800b908, CTX)
        assert m.get(CEILING, "H") == watts and m.get(FLAGS) == flags
        assert m.calls == ["deferred_configuration_persistence", "command_acknowledgement"]
        assert not m.queued
        results["app_ceiling"].append({"flags": flags, "watts": watts,
                                        "flags_unchanged": True, "queue_empty": True})

    for flags, value in itertools.product(seeds, (0, 1, 2, 0x2f, 0xffff)):
        m.reset()
        m.put(FLAGS, flags)
        m.put(PAYLOAD, value, "H")
        m.run(0x080241d8, PAYLOAD)
        expected = flags if value == 0x2f else (flags & ~0x20) | ((value & 1) << 5)
        assert m.get(FLAGS) == expected and not m.queued
        results["internal_gate"].append({"before": flags, "value": value, "after": expected})

    cases = ((0, 0, 0), (1, 299, 99), (88, 300, 100), (880, 396, 3599),
             (1600, 576, 3600), (65535, 65535, 65535))
    for variant, (power, voltage, current) in itertools.product((0, 1, 2), cases):
        m.reset()
        m.put(VARIANT, variant, "B")
        m.put(FLAGS, 0x30)
        m.run(0x080242b8, voltage,
              registers={UC_ARM_REG_R1: current, UC_ARM_REG_R2: power})
        expected = (power, min(max(voltage, 300), 576 if variant else 396), min(current, 3600))
        assert struct.unpack("<3H", m.uc.mem_read(DESCRIPTOR, 6)) == expected
        assert m.get(DESCRIPTOR + 6, "B") == 1 and m.get(FLAGS) == 0x30
        m.put(AC_STATE + 3, 1, "B")  # Existing charging session: take update branch.
        m.run(0x0801f570, stop=0x0801f6a2)
        assert len(m.queued) == 1 and m.queued[0]["register"] == 4
        frame = struct.unpack("<6H", bytes.fromhex(m.queued[0]["payload"]))
        # Register 4 serializer leaves word four at its initial value (zero).
        transmitted_current = max(10, expected[2] // 10)
        assert frame == (0, expected[1], transmitted_current, min(power, 1600), 0, 280)
        assert m.get(FLAGS) == 0x30 and m.get(DESCRIPTOR + 6, "B") == 0
        results["descriptor_and_producer"].append({
            "variant": variant, "requested": [power, voltage, current],
            "stored": expected, "register4_words": frame, "flags_unchanged": True})

    for charging, output in itertools.product((False, True), repeat=2):
        m.reset()
        flags = (int(charging) << 5) | (int(output) << 4)
        m.put(FLAGS, flags)
        m.uc.mem_write(AC_STATE + 3, b"\x01\x01")
        m.uc.mem_write(DESCRIPTOR, struct.pack("<3HB", 0, 396, 0, 1))
        m.run(0x0801f570, stop=0x0801f6a2 if charging else 0x0801f762)
        assert len(m.queued) == 1 and m.get(FLAGS) == flags
        if charging:
            assert m.queued[0]["register"] == 4
        else:
            assert m.queued[0] == {"register": 0, "payload": "5400"}
        results["gate_branches"].append({"charging_bit": charging, "output_bit": output,
                                          "queued": m.queued, "flags_unchanged": True})

    m.reset()
    app_handlers = {int(row["handler"], 16) for row in m.table(0x20000254, 32)}
    assert 0x080241d8 not in app_handlers  # Direct table membership only, not reachability proof.
    for group, table in enumerate(RULE_TABLES):
        words = struct.unpack("<20I", m.uc.mem_read(table, 80))
        rows, count, stored_group = struct.unpack("<IHBx", m.uc.mem_read(0x20000500 + group*8, 8))
        assert stored_group == group and 0 < count < 20
        results["rule_tables"].append({"group": group, "callbacks": f"{table:08x}",
            "records": f"{rows:08x}", "record_count": count,
            "charging_bit_action_slots": [i for i, value in enumerate(words[10:])
                                           if value == 0x080241d9]})
    results["cases"] = sum(len(results[key]) for key in (
        "app_ceiling", "internal_gate", "descriptor_and_producer", "gate_branches"))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE,
                        help="Decoded original C1000 1.5.9 main MCU image (SHA-256 checked)")
    parser.add_argument("--output", type=Path, required=True, help="Synthetic JSON results")
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
