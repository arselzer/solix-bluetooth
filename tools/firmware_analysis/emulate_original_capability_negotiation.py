#!/usr/bin/env python3
"""Offline A1761 main 1.5.9 library negotiation: no device/network transport.

Executes the real TLV-slot parser, function-01 dispatcher, 0003 handler and
response serializer. The capacity provider and final response transport are
host substitutes; the test is not firmware feature enumeration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_c1000_original_commands import (
    CTX, IMAGE, IMAGE_SHA256, Machine, PAYLOAD, SP, changed_as,
)

PROVIDER = 0x08005030
PROVIDER_SLOT = 0x2000640c
SESSION_FLAGS = 0x200009d4
# Dispatcher saves 24 bytes; handler saves 20 and reserves 36. Its +20 u16
# provider output therefore resides at the caller's SP minus 60.
STACK_CAPACITY = SP - 60


def field(tag: int, value: bytes) -> bytes:
    return bytes((tag, len(value))) + value


class NegotiationMachine(Machine):
    def __init__(self, image: bytes):
        self.provider_capacity = 256
        self.provider_success = 1
        self.provider_calls = 0
        super().__init__(image)

    def step(self, uc, address, size, data):
        if address in (0x08007718, 0x0801e7d4, 0x080251f0):
            raise AssertionError(f"Unexpected ACK or persistence boundary: {address:08x}")
        if address == PROVIDER:
            pointer = uc.reg_read(UC_ARM_REG_R0)
            assert pointer == STACK_CAPACITY
            uc.mem_write(pointer, struct.pack("<H", self.provider_capacity))
            self.provider_calls += 1
            self.back(self.provider_success)
        else:
            super().step(uc, address, size, data)

    def setup(self, body: bytes, *, source: int, capacity: int = 256,
              provider_success: int = 1, provider_present: bool = True,
              trailing: bytes = b"", stack_seed: int = 0xbeef):
        self.reset()
        self.provider_calls = 0
        self.provider_capacity = capacity
        self.provider_success = provider_success
        self.uc.mem_write(CTX, bytes((source,)))
        self.uc.mem_write(SESSION_FLAGS, bytes.fromhex("a5a5a5a5"))
        self.uc.mem_write(PROVIDER_SLOT, struct.pack(
            "<I", PROVIDER | 1 if provider_present else 0))
        self.uc.mem_write(PAYLOAD, body + trailing)
        self.uc.mem_write(STACK_CAPACITY, struct.pack("<H", stack_seed))
        # Actual parser writes 20 eight-byte slots at the handler's context+1c.
        self.run(0x0801df4c, PAYLOAD, registers={
            UC_ARM_REG_R1: len(body), UC_ARM_REG_R2: CTX + 0x1c,
        })
        self.calls.clear()

    def dispatch(self):
        self.run(0x0801dfac, 3, registers={UC_ARM_REG_R1: CTX})


def reply(capacity: int) -> bytes:
    return b"\x00\xa1\x01\x00\xa2\x02" + struct.pack("<H", capacity)


def run_suite(image: bytes) -> dict:
    m = NegotiationMachine(image)
    table = m.table(0x200009d8, 14)
    assert {row["opcode"]: row["handler"] for row in table}["0003"] == "08017fa0"
    rows = []
    a3 = field(0xa3, b"\x42")
    for source in (0, 1):
        for capacity in (128, 256):
            for requested in (0, 1, 127, 128, 250, 251, 512, 65535):
                m.setup(a3 + field(0xa4, struct.pack("<H", requested)),
                        source=source, capacity=capacity)
                before = m.low_ram()
                m.dispatch()
                expected = min(requested, capacity)
                assert m.replies == [reply(expected)]
                assert m.provider_calls == 1
                changed_as(before, m.low_ram(), {SESSION_FLAGS + source: b"\x01"})
                rows.append({"case": "negotiated_capacity", "source": source,
                             "requested": requested, "provider": capacity,
                             "reply_hex": m.replies[0].hex(),
                             "changed_session_byte": 1})

    # The old handler's error calls do not return from the enclosing handler.
    malformed = (
        ("missing_A3", field(0xa4, struct.pack("<H", 123)), b"", 123),
        ("missing_A4", field(0xa3, struct.pack("<H", 77)), b"", 77),
        ("A4_length_one", a3 + field(0xa4, b"\x34"), b"\x12", 256),
        ("A4_length_zero", a3 + field(0xa4, b""), b"\x7b\x00", 123),
    )
    for name, body, trailing, expected in malformed:
        m.setup(body, source=0, trailing=trailing)
        before = m.low_ram()
        m.dispatch()
        error = [b"\x04"] if name.startswith("missing") else []
        assert m.replies == error + [reply(expected)]
        changed_as(before, m.low_ram(), {SESSION_FLAGS: b"\x01"})
        rows.append({"case": name, "reply_hex": [x.hex() for x in m.replies],
                     "changed_session_byte": 1,
                     "scope": "synthetic parser slots/backing bytes, not a supported request"})

    for requested in (127, 512):
        m.setup(a3 + field(0xa4, struct.pack("<H", requested)), source=1,
                capacity=256, provider_success=0)
        before = m.low_ram()
        m.dispatch()
        assert m.replies == [b"\x01", reply(min(requested, 256))]
        changed_as(before, m.low_ram(), {SESSION_FLAGS + 1: b"\x01"})
        rows.append({"case": "provider_failure", "requested": requested,
                     "reply_hex": [x.hex() for x in m.replies],
                     "changed_session_byte": 1})

    # Missing-provider state is explicitly synthetic: application registration
    # is not executed. The handler has no initialized fallback capacity.
    m.setup(a3 + field(0xa4, struct.pack("<H", 65535)), source=0,
            provider_present=False, stack_seed=123)
    before = m.low_ram()
    m.dispatch()
    assert m.provider_calls == 0 and m.replies == [reply(123)]
    changed_as(before, m.low_ram(), {SESSION_FLAGS: b"\x01"})
    rows.append({"case": "synthetic_missing_provider", "stack_seed": 123,
                 "reply_hex": [x.hex() for x in m.replies],
                 "changed_session_byte": 1,
                 "scope": "not an initialized live application"})

    return {
        "model": "A1761 original C1000", "main_version": "1.5.9",
        "input_sha256": IMAGE_SHA256,
        "execution": {
            "parser": "0801df4c", "lookup": "0801df26",
            "function_01_dispatcher": "0801dfac", "handler": "08017fa0",
            "response_serializers": ["08026294", "0801dee8", "08023a5e"],
            "substitutes": {f"{PROVIDER:08x}": "capacity provider; writes u16 and returns result",
                            "080225c4": "response body capture"},
            "peripheral_mapping": False,
        },
        "cases": {"valid_capacity": 32, "malformed_shape": 4,
                  "provider_failure": 2, "synthetic_missing_provider": 1,
                  "total": len(rows)},
        "rows": rows,
        "limits": [
            "Installed main1.7.1 image remains missing",
            "No hardware, native-radio forwarding, authentication or complete-session validation",
            "Negotiation marks a per-source library session byte; it is not read-only feature enumeration",
            "No charging settings, output gates or physical charging behavior tested",
            "Malformed inputs and missing-provider state are synthetic boundaries, not supported requests",
            "Unmapped-pointer malformed paths are not executed or proposed for live testing",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    inputs = [Path(__file__).resolve(), Path(__file__).with_name("emulate_c1000_original_commands.py")]
    manifest = {
        "schema_version": 1, "python_version": platform.python_version(),
        "unicorn_version": unicorn.__version__, "firmware_sha256": IMAGE_SHA256,
        "sources_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        "results_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"{result['cases']['total']} offline original negotiation cases passed")


if __name__ == "__main__":
    main()
