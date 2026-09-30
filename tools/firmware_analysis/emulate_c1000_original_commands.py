#!/usr/bin/env python3
"""Offline A1761 v1.5.9 startup/legacy-handler replay; no device transport."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
    UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
    UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12, UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[2]
IMAGE = ROOT / "firmware/c1000_original/1.5.9/MainMcu-decoded.bin"
IMAGE_SHA256 = "b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6"
RAM, RAM_SIZE, SP = 0x20000000, 0x20000, 0x2001f000
CTX, PAYLOAD, OUTPUT = 0x20018000, 0x20018100, 0x20018200
RETURN = 0x08005010
REGISTERS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
             UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
             UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
             UC_ARM_REG_R12)
SUBSTITUTES = {
    0x08007718: "command_acknowledgement",
    0x0801e7d4: "deferred_configuration_persistence",
    0x080251f0: "smart_mode_file_persistence",
}


class Machine:
    def __init__(self, image):
        if hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
            raise ValueError("Unexpected original C1000 controller image")
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB)
        self.uc.mem_map(0x08000000, 0x40000)
        self.uc.mem_write(0x08005000, image)
        self.uc.mem_protect(0x08000000, 0x40000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_map(RAM, RAM_SIZE)
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write)
        self.calls = []
        self.replies = []
        self.run(0x08005a88, stop=0x08005aa0, count=200000)
        self.initial_ram = bytes(self.uc.mem_read(RAM, RAM_SIZE))

    def write(self, uc, access, address, size, value, data):
        if not RAM <= address < address + size <= RAM + RAM_SIZE:
            raise AssertionError(f"Non-RAM write: {address:08x}")

    def back(self, result=1):
        self.uc.reg_write(UC_ARM_REG_R0, result)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def step(self, uc, address, size, data):
        if address == self.stop:
            self.stopped = True
            uc.emu_stop()
        elif address in SUBSTITUTES:
            self.calls.append(SUBSTITUTES[address])
            self.back()
        elif address == 0x080225c4:
            self.replies.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1),
                                                   uc.reg_read(UC_ARM_REG_R2))))
            self.calls.append("response_transport")
            self.back()
        elif not 0x08005000 <= address < 0x0802bc00:
            raise AssertionError(f"Unexpected executable address: {address:08x}")

    def reset(self):
        self.uc.mem_write(RAM, self.initial_ram)
        self.uc.mem_write(CTX + 0x18, struct.pack("<I", PAYLOAD))
        self.calls, self.replies = [], []

    def run(self, address, argument=0, *, stop=RETURN, count=20000, registers=None):
        self.stopped, self.stop = False, stop
        for register in REGISTERS:
            self.uc.reg_write(register, 0)
        self.uc.reg_write(UC_ARM_REG_SP, SP)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        for register, value in (registers or {}).items():
            self.uc.reg_write(register, value)
        self.uc.emu_start(address | 1, 0, count=count)
        if not self.stopped:
            raise AssertionError(f"Instruction limit at {self.uc.reg_read(UC_ARM_REG_PC):08x}")

    def low_ram(self):
        return bytes(self.uc.mem_read(RAM, 0x10000))

    def byte(self, address):
        return self.uc.mem_read(address, 1)[0]

    def table(self, address, count):
        rows = []
        for index in range(count):
            opcode, padding, handler = struct.unpack("<HHI", self.uc.mem_read(address + 8*index, 8))
            assert padding == 0 and handler & 1
            rows.append({"opcode": f"{opcode:04x}", "handler": f"{handler & ~1:08x}"})
        return rows


def changed_as(before, after, updates):
    expected = bytearray(before)
    for address, value in updates.items():
        expected[address-RAM:address-RAM+len(value)] = value
    assert after == expected


def run_suite(image):
    m = Machine(image)
    tables = {
        "function_0f_app": m.table(0x20000254, 32),
        "function_10_module": m.table(0x20000154, 32),
        "function_01_library": m.table(0x200009d8, 14),
    }
    app = {row["opcode"]: row["handler"] for row in tables["function_0f_app"]}
    assert app["0044"] == "0800b908" and app["005e"] == "0800bd00"
    assert "0090" not in app and "0103" not in app
    rows = []
    for raw in range(256):
        for seed in (0, 0xff, 0x5a):
            m.reset()
            m.uc.mem_write(PAYLOAD+6, bytes((raw,)))
            m.uc.mem_write(0x200020c4, bytes((seed,)))
            before = m.low_ram()
            m.run(0x0800bd00, CTX)
            expected = (seed & ~4) | (4 if raw else 0)
            changed_as(before, m.low_ram(), {0x20000d10: bytes((raw,)),
                                             0x200020c4: bytes((expected,))})
            assert m.calls == ["command_acknowledgement"]
            m.run(0x080173a8)
            assert m.uc.reg_read(UC_ARM_REG_R0) == bool(raw)
            rows.append(["fast", raw, seed, expected])
    for opcode, handler, address in (("0076", 0x0800bd48, 0x200004ab),
                                     ("0077", 0x0800bd28, 0x200004ac)):
        for raw in range(256):
            m.reset()
            m.uc.mem_write(0x200004ab, b"\x01\x01")
            m.uc.mem_write(PAYLOAD+6, bytes((raw,)))
            before = m.low_ram()
            m.run(handler, CTX)
            mode = 2 if raw == 1 else 1
            changed_as(before, m.low_ram(), {address: bytes((mode,))})
            assert m.calls == ["smart_mode_file_persistence", "command_acknowledgement"]
            m.run(0x080093f0, stop=0x08009424, registers={UC_ARM_REG_R4: OUTPUT})
            encoded = bytes(m.uc.mem_read(OUTPUT, 5))
            expected_modes = bytes((mode, 1)) if opcode == "0076" else bytes((1, mode))
            assert encoded == b"\xf8\x15\x04" + expected_modes
            rows.append([opcode, raw, mode, encoded.hex()])
    powers = (0, 1, 50, 99, 100, 200, 750, 1000, 1001, 1200, 1400, 65535)
    power_results = []
    for variant in (0, 1, 2):
        for power in powers:
            m.reset()
            m.uc.mem_write(0x20000454, bytes((variant,)))
            m.uc.mem_write(PAYLOAD+6, struct.pack("<H", power))
            before = m.low_ram()
            m.run(0x0800b908, CTX)
            changed_as(before, m.low_ram(), {0x20002040: struct.pack("<H", power)})
            assert m.calls == ["deferred_configuration_persistence", "command_acknowledgement"]
            m.run(0x08017500)
            assert m.uc.reg_read(UC_ARM_REG_R0) == power
            # Actual status-builder upper clamp, with its earlier stack store seeded.
            m.uc.mem_write(SP+0x100, struct.pack("<H", power))
            m.run(0x080092f6, stop=0x0800931c)
            reported = struct.unpack("<H", m.uc.mem_read(SP+0x100, 2))[0]
            assert reported == min(power, 1000 if variant == 0 else 750)
            row = {"variant_byte": variant, "raw_watts": power, "reported_D1_watts": reported}
            power_results.append(row)
            rows.append(["power", variant, power, reported])
    m.reset()
    before = m.low_ram()
    m.run(0x08017ec4, CTX)
    assert m.low_ram() == before and m.calls == ["response_transport"] and len(m.replies) == 1
    reply = m.replies[0]
    assert reply.startswith(bytes.fromhex("00a1020200a205") + b"2.0.1")
    # Do not duplicate an embedded build-author name into the result fixture.
    fields, pos = [], 1
    while pos < len(reply):
        tag, size = reply[pos:pos+2]
        value = reply[pos+2:pos+2+size]
        assert len(value) == size
        fields.append({"tag": f"{tag:02x}", "bytes": size,
                       **({"value": value.decode()} if tag in (0xa2, 0xa4) else {})})
        pos += 2+size
    assert pos == len(reply)
    rows.append(["library_0025", len(reply)])
    return {
        "model": "A1761 original C1000", "main_version": "1.5.9",
        "input_sha256": IMAGE_SHA256,
        "startup_data_sha256": hashlib.sha256(m.initial_ram[:0xa68]).hexdigest(),
        "execution": {"startup": "08005a88..08005aa0; actual scatter decompressor",
                      "substitutes": {f"{k:08x}": v for k, v in SUBSTITUTES.items()} |
                                     {"080225c4": "response transport capture"},
                      "peripheral_mapping": False},
        "tables": tables,
        "cases": {"fast_charge_raw_byte_and_unrelated_bits": 768,
                  "smart_raw_byte_and_actual_F8_serializer": 512,
                  "power_raw_store_and_actual_D1_clamp": 36,
                  "library_0025_reply": 1, "total": len(rows)},
        "all_cases_sha256": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest(),
        "smart_wire_mapping": {"0": "normal/status 1", "1": "smart/status 2",
                               "2..255": "normal/status 1; unsupported public command inputs"},
        "power_results": power_results, "library_0025_reply_fields": fields,
        "limits": ["Not the live station's installed 1.5.1 image",
                   "No whole-device execution, MQTT authentication, radio or physical power validation",
                   "Persistence, acknowledgements and transport substituted",
                   "D1 is an upper-clamped readback, not proof of a safe charging range",
                   "Missing opcodes apply only to the recovered function-0f table"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['cases']['total']} offline handler cases passed; Python {platform.python_version()}, Unicorn {unicorn.__version__}")


if __name__ == "__main__":
    main()
