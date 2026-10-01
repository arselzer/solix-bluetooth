#!/usr/bin/env python3
"""Original A1761 1.5.9 charging-source and input-power status provenance.

Runs actual getters, information-cache stores and typed status serialization.
RAM and power samples are synthetic; no scheduler, DSP, transport or device.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R4

from emulate_c1000_original_commands import changed_as
from emulate_original_power_paths import (
    CEILING, CTX, FLAGS, IMAGE, IMAGE_SHA256, OUTPUT, PAYLOAD, PowerMachine,
)
from emulate_original_timers_modes import INFO

POWERS = 0x200020c8
INPUT_FLAGS = 0x20000690
FAST_FLAGS = 0x200020c4
FAST_REQUEST = 0x20000d10


class SourceMachine(PowerMachine):
    def prepare(self, flags, *, input_present=0, inputs=None, outputs=None):
        self.reset()
        self.put(FLAGS, flags)
        self.put(INPUT_FLAGS, input_present, "B")
        self.put(CEILING, 1000, "H")
        self.put(FAST_FLAGS, 3, "B")  # Retain unrelated AC/DC timer bits.
        self.uc.mem_write(INFO, bytes(96))
        self.uc.mem_write(POWERS, struct.pack("<20H", *(inputs or [0]*10),
                                            *(outputs or [0]*10)))

    def readback(self):
        before = self.low_ram()
        # Execute the complete source selection, including the bit-0 priority.
        self.run(0x08010bf8, stop=0x08010c32, registers={UC_ARM_REG_R4: INFO})
        # Actual port-7 input, port-8 input, all-input sum and all-output sum.
        for start, end in ((0x08010ba0, 0x08010ba8), (0x08010bdc, 0x08010be4),
                           (0x08010be4, 0x08010bea), (0x08010bea, 0x08010bf0)):
            self.run(start, stop=end, registers={UC_ARM_REG_R4: INFO})
        changed_as(before, self.low_ram(), {
            INFO+0x0a: bytes(self.uc.mem_read(INFO+0x0a, 2)),
            INFO+0x1c: bytes(self.uc.mem_read(INFO+0x1c, 6)),
            INFO+0x38: bytes(self.uc.mem_read(INFO+0x38, 1)),
        })
        before = self.low_ram()
        self.put(OUTPUT+512, 0, "H")
        self.run(0x08009164, OUTPUT, stop=0x0800924a,
                 registers={UC_ARM_REG_R1: OUTPUT+512})
        assert self.low_ram() == before
        raw = bytes(self.uc.mem_read(OUTPUT, 214))
        fields, offset = {}, 0
        while offset < len(raw):
            tag, length = raw[offset:offset+2]
            value = raw[offset+2:offset+2+length]
            assert len(value) == length and tag not in fields
            fields[tag] = value
            offset += length+2
        assert offset == len(raw)
        flags = self.get(FLAGS)
        inputs = struct.unpack("<10H", self.uc.mem_read(POWERS, 20))
        outputs = struct.unpack("<10H", self.uc.mem_read(POWERS+20, 20))
        expected = {0xa5: inputs[7], 0xae: inputs[8],
                    0xaf: sum(inputs) & 0xffff, 0xb0: sum(outputs) & 0xffff}
        for tag, value in expected.items():
            assert fields[tag] == b"\x02"+struct.pack("<H", value)
        source = 1 if flags & 1 else 2 if flags & 0x20 else 0
        assert fields[0xbc] == bytes((1, source))
        assert not self.replies and not self.queued
        return {"a5_raw": expected[0xa5], "ae_raw": expected[0xae],
                "af_raw": expected[0xaf], "b0_raw": expected[0xb0],
                "bc_code": source, "typed_bc": fields[0xbc].hex()}


def run_suite(image):
    if not __debug__:
        raise RuntimeError("Assertions are required; do not use Python -O")
    m = SourceMachine(image)
    result = {"image_sha256": IMAGE_SHA256,
              "scope": "Public main1.5.9; synthetic flags/power caches only; no main1.7.1, DSP or electrical validation",
              "source_matrix": [], "power_cache_members": [],
              "power_sum_boundaries": [], "settings_roundtrips": []}
    for source_bits, output_bits, present, input_power in itertools.product(
            (0, 1, 0x20, 0x21), (0, 2, 0x10, 0x12), (0, 1), (0, 250)):
        flags = 0x12340000 | source_bits | output_bits
        inputs, outputs = [0]*10, [0]*10
        inputs[7], outputs[7] = input_power, 115
        m.prepare(flags, input_present=present, inputs=inputs, outputs=outputs)
        raw_powers = bytes(m.uc.mem_read(POWERS, 40))
        baseline = (flags, m.get(INPUT_FLAGS, "B"), m.get(CEILING, "H"),
                    m.get(FAST_FLAGS, "B"))
        status = m.readback()
        assert (m.get(FLAGS), m.get(INPUT_FLAGS, "B"), m.get(CEILING, "H"),
                m.get(FAST_FLAGS, "B")) == baseline
        assert bytes(m.uc.mem_read(POWERS, 40)) == raw_powers and not m.calls
        result["source_matrix"].append({"source_bits": source_bits,
            "output_bits": output_bits, "qualified_input_bit": present,
            "input_cache7": input_power, **status,
            "protected_flags_preferences_and_power_caches_unchanged": True})

    for direction, index in itertools.product(("input", "output"), range(10)):
        inputs, outputs = [0]*10, [0]*10
        (inputs if direction == "input" else outputs)[index] = 1234
        m.prepare(0x30, inputs=inputs, outputs=outputs)
        status = m.readback()
        assert status["af_raw"] == (1234 if direction == "input" else 0)
        assert status["b0_raw"] == (1234 if direction == "output" else 0)
        result["power_cache_members"].append({"direction": direction,
            "index": index, "seeded_value": 1234, **status})

    for members in ([1,2,3,4,5,6,7,8,9,10], [65535]+[0]*9,
                    [65535,1]+[0]*8, [65535]*10):
        m.prepare(0x30, inputs=members, outputs=list(reversed(members)))
        status = m.readback()
        result["power_sum_boundaries"].append({"inputs": members,
            "full_sum": sum(members), "wire_sum": sum(members) & 0xffff,
            "no_saturation_before_uint16_store": True, **status})

    for source_bits, kind in itertools.product((0,1,0x20,0x21), ("fast", "ceiling")):
        m.prepare(0x12340012 | source_bits)
        initial = (m.get(FLAGS), m.get(CEILING, "H"), m.get(FAST_FLAGS, "B"),
                   m.get(FAST_REQUEST, "B"))
        baseline = m.readback()
        traces = []
        for value in ((1,0) if kind == "fast" else (100,1000)):
            before = m.low_ram()
            m.put(PAYLOAD+6, value, "B" if kind == "fast" else "H")
            m.calls.clear()
            m.run(0x0800bd00 if kind == "fast" else 0x0800b908, CTX)
            expected = ({FAST_REQUEST: bytes((value,)),
                         FAST_FLAGS: bytes((3 | (value << 2),))} if kind == "fast"
                        else {CEILING: struct.pack("<H", value)})
            changed_as(before, m.low_ram(), expected)
            assert m.calls == (["command_acknowledgement"] if kind == "fast" else
                               ["deferred_configuration_persistence", "command_acknowledgement"])
            status = m.readback()
            assert status == baseline
            traces.append({"requested": value, **status})
        assert (m.get(FLAGS), m.get(CEILING, "H"), m.get(FAST_FLAGS, "B"),
                m.get(FAST_REQUEST, "B")) == initial
        result["settings_roundtrips"].append({"kind": kind,
            "source_bits": source_bits, "traces": traces,
            "source_code_unchanged_and_protected_snapshot_restored": True})
    result["cases"] = sum(len(value) for value in result.values() if isinstance(value, list))
    assert result["cases"] == 96
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    if args.manifest:
        files = [Path(__file__), *[Path(__file__).with_name(name) for name in (
            "emulate_c1000_original_commands.py", "emulate_original_power_paths.py",
            "emulate_original_timers_modes.py", "requirements.txt")]]
        args.manifest.write_text(json.dumps({"firmware_sha256": IMAGE_SHA256,
            "case_count": result["cases"], "python": platform.python_version(),
            "unicorn": unicorn.__version__, "sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
            "result_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            "substitutes": ["command ACK", "deferred configuration persistence"],
            "not_executed": ["radio", "MQTT", "device", "DSP", "charging policy",
                             "physical power", "flash", "installed main1.7.1"]}, indent=2)+"\n")
    print(f"Passed {result['cases']} synthetic charging-source cases")


if __name__ == "__main__":
    main()
