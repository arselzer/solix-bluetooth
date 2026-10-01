#!/usr/bin/env python3
"""Audit selected diagnostic getters in public A1763 main 1.1.4.9.

Actual Thumb parser, table dispatcher, timer stop, getters, serializer and CRC
execute. Startup RAM initialization and the empty radio command-table path also
execute. Ring I/O, memcpy/memset/strlen and logging are host substitutes.
Synthetic RAM and GPIO only. No device, network, storage backend or runtime API.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn import arm_const as A


SHA256 = "21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9"
BASE, STOP, STACK = 0x08005000, 0x08005000, 0x2001F000
SETTINGS, BACKUP, SCRATCH, TIMERS = 0x20001D48, 0x20001EA9, 0x20004A89, 0x20007344
TABLES = ((0, 0x08032B64, 51), (1, 0x08032CFC, 9))
GETTERS = {
    0x01: (0x080283BC, 16, "public model string"),
    0x02: (0x080283D0, 16, "public main version string"),
    0x03: (0x08028394, 16, "public build date string"),
    0x04: (0x080283A8, 16, "public build time string"),
    0x05: (0x080284A8, None, "variant-selected identity string"),
    0x07: (0x0802846A, 1, "cached BMS byte at 2000406b"),
    0xD0: (0x08030558, 2, "constant 5500 response"),
    0xD1: (0x08030574, 12, "cached block at 2000079f"),
    0xD8: (0x08018714, 12, "cached block at 20000787"),
    0xE0: (0x08028428, 4, "BMS word, zero byte, BMS byte"),
    0xE1: (0x080283E4, 4, "BMS word difference and zero word"),
    0xE2: (0x080142F4, 2, "constant 5500 response"),
    0xE3: (0x08014310, 12, "cached block at 20000793"),
    0xE5: (0x08029FEE, None, "stored identity string; minimum length 17"),
    0xE7: (0x0801378E, 8, "four calibration halfwords at 20000134..2000013b"),
    0xE8: (0x08017748, 20, "component version/configuration aggregate"),
    0xEB: (0x08015638, 2, "constant zero word"),
    0xEF: (0x0802D0BE, 1, "low byte of calibration halfword at 2000013c"),
    0xF0: (0x080136D8, 1, "GPIO input register 40011408 mask 1000"),
    0xF6: (0x08018362, 2, "cached BMS word at 2000403b masked 3000"),
    0xF7: (0x080195E4, 4, "cached word at 200042a0"),
    0xF9: (0x08019220, 4, "three bytes at 20000112 plus written NUL terminator"),
    0xFD: (0x0801923A, 1, "calibration/configuration byte at 20000111"),
}


def crc16(data: bytes) -> bytes:
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value.to_bytes(2, "big")


def inner(prop: int, data: bytes = b"", selector: int = 0) -> bytes:
    body = b"\0" + struct.pack("<H", len(data) + 8) + bytes((selector,)) + struct.pack("<H", prop) + data
    return b"\xee" + body + crc16(body) + b"\xfc\x55"


def tables(image: bytes) -> list[dict]:
    return [{"selector": selector, "table_address": f"{address:08x}",
             "entries": [{"property": f"{prop:04x}", "handler": f"{fn & ~1:08x}",
                          "getter_replayed": selector == 0 and prop in GETTERS}
                         for prop, fn in (struct.unpack_from("<II", image, address - BASE + i * 8)
                                          for i in range(count))]}
            for selector, address, count in TABLES]


class Machine:
    def __init__(self, image: bytes, seed: int, allocated_timer: bool):
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000, 0x40000)
        self.uc.mem_write(BASE, image)
        self.uc.mem_map(0x20000000, 0x20000)
        self.uc.mem_map(0x40011000, 0x1000)
        self.raw = b""
        self.replies = []
        self.serialized_data = []
        self.visited = set()
        self.reads = set()
        self.writes = set()
        self.guard = False
        self.allow_terminator = False
        self.stopped = False
        for address, size in ((SETTINGS, 0x190), (0x20000787, 36),
                              (0x20003AEC, 0x100), (0x20003F00, 0x200),
                              (0x2000429C, 8), (0x20000134, 10), (0x2000040C, 20)):
            self.uc.mem_write(address, bytes(((i * 13 + 7 + seed * 41) & 255 for i in range(size))))
        self.uc.mem_write(0x20000164, bytes.fromhex("3000050007000000"))
        self.uc.mem_write(0x20000111, bytes((2 + seed,)))
        self.uc.mem_write(0x20000112, b"XYZ!")
        self.uc.mem_write(0x20000116, b"SYNTHETIC00000001\0")
        self.uc.mem_write(0x200000E4, bytes((seed,)))
        self.uc.mem_write(0x200001D0, struct.pack("<II", 0x20018000, 0x20018020))
        self.uc.mem_write(0x20018000, b"SYNTHETIC-VARIANT-0\0")
        self.uc.mem_write(0x20018020, b"SYNTHETIC-VARIANT-1\0")
        self.uc.mem_write(0x40011408, struct.pack("<I", 0x1000 if seed else 0))
        self.uc.mem_write(0x20000148, bytes((1 if allocated_timer else 0,)))
        self.uc.mem_write(TIMERS + 20, b"\x02" + bytes(19))
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.read_hook)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write_hook)

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def read_hook(self, uc, _access, address, size, _value, _data):
        if self.guard and 0x20000000 <= address < 0x20010000:
            self.reads.update(range(address, address + size))

    def write_hook(self, uc, _access, address, size, _value, _data):
        if not self.guard:
            return
        allowed = [(SCRATCH, SCRATCH + 0x41F), (0x2000071C, 0x20000724),
                   (TIMERS + 20, TIMERS + 21), (0x2001E000, 0x2001F000)]
        if self.allow_terminator:
            allowed.append((0x20000115, 0x20000116))
        if not any(lo <= address < address + size <= hi for lo, hi in allowed):
            raise AssertionError(f"Unexpected write {address:08x} at {uc.reg_read(A.UC_ARM_REG_PC):08x}")
        self.writes.update(range(address, address + size))

    def back(self, value: int = 0):
        self.uc.reg_write(A.UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.uc.reg_write(A.UC_ARM_REG_PC, self.uc.reg_read(A.UC_ARM_REG_LR))

    def step(self, uc, address, _size, _data):
        r0, r1, r2, r3 = (uc.reg_read(register) for register in
                         (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3))
        self.visited.add(address)
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x080052A8:
            uc.mem_write(r0, self.read(r1, r2))
            # Instrument host memcpy sources as well as executed loads.
            self.read_hook(uc, 0, r1, r2, 0, None)
            self.write_hook(uc, 0, r0, r2, 0, None) if r2 else None
            self.back(r0)
        elif address == 0x080052DA:
            self.write_hook(uc, 0, r0, r1, 0, None) if r1 else None
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x08005340:
            count = 0
            while self.read(r0 + count, 1) != b"\0":
                count += 1
                assert count <= 255
            self.read_hook(uc, 0, r0, count + 1, 0, None)
            self.back(count)
        elif address == 0x0800D284:
            self.back()
        elif address == 0x080115E4:
            self.back(len(self.raw))
        elif address == 0x080115F0:
            value = self.raw[r0:r0 + r2]
            uc.mem_write(r1, value)
            self.back(len(value))
        elif address == 0x080115D4:
            value = self.raw[:r1]
            uc.mem_write(r0, value)
            self.raw = self.raw[len(value):]
            self.back(len(value))
        elif address == 0x08011628:
            self.replies.append(self.read(r0, r1))
            self.back(r1)
        else:
            if address == 0x0800DBDC:
                self.serialized_data.append(self.read(r3, r2))
            allowed = ((0x080091FC, 0x08009228), (0x0800DBDC, 0x0800DC4C),
                       (0x0800DE18, 0x0800DF16), (0x08010998, 0x080109C0),
                       (0x080136D8, 0x08013704), (0x0801378E, 0x080137F0),
                       (0x080142F4, 0x08014326), (0x08015638, 0x08015652),
                       (0x08015770, 0x080157D4), (0x08017748, 0x08017840),
                       (0x080182DC, 0x080182E0), (0x08018362, 0x08018546),
                       (0x0801870C, 0x08018730), (0x08019220, 0x080192C0),
                       (0x08019428, 0x0801942E), (0x080195D8, 0x08019668),
                       (0x0801A460, 0x0801A508), (0x0801A6BC, 0x0801A6C4),
                       (0x0801AD8C, 0x0801AD90), (0x0801B048, 0x0801B05C),
                       (0x0801B164, 0x0801B172), (0x08028394, 0x08028486),
                       (0x080284A8, 0x080284E0), (0x08029FEE, 0x0802A024),
                       (0x0802C464, 0x0802C472), (0x0802D0BE, 0x0802D0D8),
                       (0x0802EE30, 0x0802EE34), (0x0802EE66, 0x0802EE6C),
                       (0x0802F6AC, 0x0802F71A), (0x08030558, 0x0803058A))
            if not any(lo <= address < hi for lo, hi in allowed):
                raise AssertionError(f"Unexpected instruction {address:08x}")

    def query(self, raw: bytes, *, property_id: int = -1) -> dict:
        self.raw = raw
        self.allow_terminator = property_id == 0xF9
        before = self.read(SETTINGS, 0x190)
        outputs = self.read(0x20000164, 8)
        self.guard = True
        self.uc.reg_write(A.UC_ARM_REG_SP, STACK)
        self.uc.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(0x0802F6AD, 0, count=50000)
        assert self.stopped, "Instruction budget exceeded"
        assert self.read(SETTINGS, 0x190) == before
        assert self.read(0x20000164, 8) == outputs
        assert not self.reads.intersection(range(BACKUP, BACKUP + 38))
        for wire, data in zip(self.replies, self.serialized_data):
            assert wire == inner(property_id, data)
        return {"saved_settings_and_outputs_preserved": True,
                "backup_record_bytes_read": 0,
                "upgrade_timer_status": self.read(TIMERS + 20, 1)[0],
                "data": [data.hex() for data in self.serialized_data],
                "reply": [wire.hex() for wire in self.replies],
                "extra_written_byte": "20000115" if 0x20000115 in self.writes else None}


def expected_data(machine: Machine, prop: int, seed: int) -> bytes | None:
    """Independent byte expectations for the reviewed simple getter mappings."""
    strings = {1: b"A1763", 2: b"1.1.4.9", 3: b"Jul 29 2026", 4: b"19:41:28"}
    if prop in strings:
        return strings[prop].ljust(16, b"\0")
    if prop == 5:
        return f"SYNTHETIC-VARIANT-{seed}".encode()
    addresses = {7: (0x2000406B, 1), 0xD1: (0x2000079F, 12),
                 0xD8: (0x20000787, 12), 0xE3: (0x20000793, 12),
                 0xE5: (0x20000116, 17), 0xE7: (0x20000134, 8),
                 0xEF: (0x2000013C, 1), 0xF7: (0x200042A0, 4), 0xFD: (0x20000111, 1)}
    if prop in addresses:
        return machine.read(*addresses[prop])
    if prop in (0xD0, 0xE2):
        return b"\x55\0"
    if prop == 0xE0:
        return machine.read(0x2000041D, 2) + b"\0" + machine.read(0x2000041C, 1)
    if prop == 0xE1:
        a, b = struct.unpack("<HH", machine.read(0x2000400C, 4))
        return struct.pack("<HH", (a - b) & 0xFFFF, 0)
    if prop == 0xEB:
        return b"\0\0"
    if prop == 0xF0:
        return bytes((seed,))
    if prop == 0xF6:
        return struct.pack("<H", int.from_bytes(machine.read(0x2000403B, 2), "little") & 0x3000)
    if prop == 0xF9:
        return b"XYZ\0"
    assert prop == 0xE8
    return None  # Component names remain unresolved; assert two known fields below.


def radio_table_case(image: bytes, command: int, has_body: bool) -> dict:
    """Prove only the initial, zero-table condition, not an entire device boot."""
    uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    uc.mem_map(0x08000000, 0x40000)
    uc.mem_write(BASE, image)
    uc.mem_map(0x20000000, 0x20000)
    uc.reg_write(A.UC_ARM_REG_SP, STACK)
    uc.emu_start(0x08005A8D, 0x08005AA4, count=200000)
    assert uc.reg_read(A.UC_ARM_REG_PC) == 0x08005AA4
    assert bytes(uc.mem_read(0x20000760, 8)) == bytes(8)
    function, callback = struct.unpack_from("<II", image, 0x0803380C + 0x4C - BASE)
    assert (function, callback) == (0x0C, 0x08017705)
    visited, log_calls = set(), []
    stopped = False

    def step(_uc, address, _size, _data):
        nonlocal stopped
        visited.add(address)
        if address == STOP:
            stopped = True
            uc.emu_stop()
        elif address == 0x0800D284:
            log_calls.append(address)
            uc.reg_write(A.UC_ARM_REG_PC, uc.reg_read(A.UC_ARM_REG_LR))
        elif not any(lo <= address < hi for lo, hi in
                     ((0x08017D08, 0x08017D42), (0x08017704, 0x08017744),
                      (0x08020180, 0x080201B6))):
            raise AssertionError(f"Unexpected radio-table instruction {address:08x}")

    def call(address: int, r0: int, r1: int):
        nonlocal stopped
        stopped = False
        uc.reg_write(A.UC_ARM_REG_R0, r0)
        uc.reg_write(A.UC_ARM_REG_R1, r1)
        uc.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        uc.emu_start(address | 1, 0, count=5000)
        assert stopped

    uc.hook_add(unicorn.UC_HOOK_CODE, step)
    call(0x08017D08, function, callback)
    entry = bytes(uc.mem_read(0x20009908 + function * 16, 16))
    assert entry[1] == 1 and int.from_bytes(entry[4:8], "little") == callback
    body = inner(0xF0) if has_body else b""
    uc.mem_write(0x20018010, struct.pack("<H", len(body)))
    uc.mem_write(0x20018014, struct.pack("<I", 0x20018100))
    uc.mem_write(0x20018100, body or b"\0")
    before = bytes(uc.mem_read(0x20000000, 0x10000))
    call(callback, command, 0x20018000)
    assert bytes(uc.mem_read(0x20000000, 0x10000)) == before
    assert 0x08020180 in visited and log_calls
    assert not {0x0802F6AC, 0x0800DE18, 0x0802C464, 0x080136D8}.intersection(visited)
    return {"case": "radio_wrapper_with_startup_zero_command_table",
            "command": f"{command:04x}", "inner_body_present": has_body,
            "registered_function": "0c", "registered_callback": "08017704",
            "command_table": "0000000000000000", "diagnostic_parser_called": False,
            "low_ram_preserved": True, "logging_called": True,
            "limit": "RAM initialization plus isolated registration/wrapper; not the full hardware boot."}


def suite(image: bytes) -> dict:
    inventory = tables(image)
    entries = {int(row["property"], 16): int(row["handler"], 16) for row in inventory[0]["entries"]}
    results = []
    for prop, seed, timer in itertools.product(GETTERS, (0, 1), (False, True)):
        callback, size, description = GETTERS[prop]
        assert entries[prop] == callback
        machine = Machine(image, seed, timer)
        expected = expected_data(machine, prop, seed)
        result = machine.query(inner(prop), property_id=prop)
        assert callback in machine.visited
        assert len(result["reply"]) == 1
        if size is not None:
            assert len(bytes.fromhex(result["data"][0])) == size
        if expected is not None:
            assert bytes.fromhex(result["data"][0]) == expected
        else:
            data = bytes.fromhex(result["data"][0])
            assert data[2:4] == struct.pack("<H", 1149)
            assert data[16:18] == machine.read(SETTINGS + 0x0D, 2)
        assert result["upgrade_timer_status"] == (3 if timer else 2)
        assert bool(result["extra_written_byte"]) == (prop == 0xF9)
        results.append({"property": f"{prop:04x}", "handler": f"{callback:08x}",
                        "meaning": description, "seed": seed, "timer_allocated": timer, **result})
    for selector, prop in ((0, 0xFF), (1, 0xFF), (2, 0x01), (255, 0x01)):
        machine = Machine(image, 0, True)
        result = machine.query(inner(prop, selector=selector))
        assert not result["reply"] and result["upgrade_timer_status"] == 3
        results.append({"case": "unsupported_selector_or_property", "selector": selector,
                        "property": f"{prop:04x}", **result})
    for position in (0, 2, 7, 9, 10):
        machine = Machine(image, 0, True)
        malformed = bytearray(inner(2))
        malformed[position] ^= 1
        result = machine.query(bytes(malformed))
        assert not result["reply"] and result["upgrade_timer_status"] == 2
        results.append({"case": "invalid_inner_frame", "changed_offset": position, **result})
    for command, has_body in itertools.product((0, 0x4000, 0x800, 0xFFFF), (False, True)):
        results.append(radio_table_case(image, command, has_body))
    return {"firmware_sha256": SHA256, "model": "A1763 C1000 Gen 2 main 1.1.4.9",
            "cases_passed": len(results), "property_inventory": inventory, "cases": results,
            "limits": __doc__.strip(),
            "claim": "Selected getters do not export backup records. The radio command table is initially empty; BLE/native access to the inner parser is not established. This is not proof that every diagnostic entry is passive or that no other export exists."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions are required")
    image = args.image.read_bytes()
    assert len(image) == 198656 and hashlib.sha256(image).hexdigest() == SHA256
    result = suite(image)
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(encoded)
    args.manifest.write_text(json.dumps({"script": Path(__file__).name,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "firmware_sha256": SHA256, "results_sha256": hashlib.sha256(encoded).hexdigest(),
        "unicorn_version": unicorn.__version__, "cases_passed": result["cases_passed"],
        "fixture_source": "Synthetic RAM, GPIO and inner requests; public firmware constants"},
        indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases_passed": result["cases_passed"], "firmware_sha256": SHA256}))


if __name__ == "__main__":
    main()
