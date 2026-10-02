#!/usr/bin/env python3
"""Offline complete sysPara persistence and saved-backup reference evidence.

Executes A1763 main 1.1.4.9 load/save/checksum/validator/default instructions
with an in-memory synthetic file. File I/O, memcmp, memcpy/memset, logging and
persistence scheduling are substitutes. No device, filesystem backend, external
file-export protocol or runtime API is exercised. A separate bounded scan
records direct getter branches and literal-reference candidates.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
import struct

import capstone
from capstone import arm as C
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_PROT_EXEC, UC_PROT_READ
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP)

from emulate_charging_followup import DefaultsMachine
from emulate_clock_semantics import STACK, STOP
from emulate_general_settings import SETTINGS
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
BASE = 0x08005000
SIZE = 0x19f
BACKUP = SETTINGS + 0x161
FILE_NAME = 0x20018000


def crc8(data: bytes) -> int:
    """Independent host expectation for firmware polynomial 0x07, initial zero."""
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (7 if value & 0x80 else 0)) & 255
    return value


class FileMachine(DefaultsMachine):
    def __init__(self):
        super().__init__()
        self.uc.mem_write(FILE_NAME, b"sysPara\0")
        self.uc.mem_write(0x200000e8, struct.pack("<I", FILE_NAME))
        self.uc.mem_write(SETTINGS, b"sysP\0")
        self.file = b""
        self.calls = []
        self.defaults = 0
        self.reads = set()
        self.writes = set()
        self.guard = False
        self.uc.mem_protect(0x08000000, 0x40000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.read_hook)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write_hook)

    def read(self, address, length):
        return bytes(self.uc.mem_read(address, length))

    def read_hook(self, uc, _access, address, size, _value, _data):
        if self.guard and address < BACKUP + 38 and address + size > BACKUP:
            self.reads.update(range(max(BACKUP, address), min(BACKUP + 38, address + size)))

    def write_hook(self, uc, _access, address, size, _value, _data):
        assert 0x20000000 <= address < address + size <= 0x20020000, (
            "Non-RAM instruction write", hex(address), hex(uc.reg_read(UC_ARM_REG_PC)))
        if self.guard and address < BACKUP + 38 and address + size > BACKUP:
            self.writes.update(range(max(BACKUP, address), min(BACKUP + 38, address + size)))

    def step(self, uc, address, size, data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        if address == 0x08029aa0:
            self.defaults += 1
            # Continue into the actual default initializer in the parent.
        if address == 0x0801e808:
            name = self.read(r2, 8).split(b"\0")[0]
            assert name == b"sysPara"
            self.calls.append({"kind": "open", "name": "sysPara", "flags": r3})
            self.back(0)
        elif address == 0x0801eea8:
            copied = self.file[:r3]
            uc.mem_write(r2, copied)
            self.calls.append({"kind": "read", "requested": r3, "returned": len(copied)})
            self.back(len(copied))
        elif address == 0x0801efa8:
            self.read_hook(uc, 0, r2, r3, 0, None)
            self.file = self.read(r2, r3)
            self.calls.append({"kind": "write", "bytes": r3})
            self.back(r3)
        elif address in (0x0801ef9c, 0x0801efa4, 0x0801e72c):
            self.calls.append({"kind": {0x0801ef9c: "rewind", 0x0801efa4: "sync",
                                       0x0801e72c: "close"}[address]})
            self.back(0)
        elif address == 0x08005400:
            self.read_hook(uc, 0, r0, r2, 0, None)
            self.read_hook(uc, 0, r1, r2, 0, None)
            a, b = self.read(r0, r2), self.read(r1, r2)
            self.back((a > b) - (a < b))
        elif address == 0x080052a8:
            self.read_hook(uc, 0, r1, r2, 0, None)
            uc.mem_write(r0, self.read(r1, r2))
            self.back(r0)
        elif any(lo <= address < hi for lo, hi in (
                (0x080288c8, 0x08028960), (0x0802c994, 0x0802ca36),
                (0x0800a01c, 0x0800a070))):
            return
        else:
            super().step(uc, address, size, data)

    def checked_file(self):
        value = bytearray(self.read(SETTINGS, SIZE))
        value[:4] = b"sysP"
        value[4] = crc8(value[5:])
        self.uc.reg_write(UC_ARM_REG_R1, SIZE - 5)
        actual_crc = self.run(0x0800a01c, SETTINGS + 5)
        assert actual_crc == value[4]
        return bytes(value)

    def run(self, address, argument):
        self.stopped = False
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=500000)
        assert self.stopped, f"Instruction budget at {address:08x}"
        return self.uc.reg_read(UC_ARM_REG_R0)

    def call_file_routine(self, address):
        self.reads.clear()
        self.writes.clear()
        self.calls.clear()
        self.guard = True
        try:
            return self.run(address, 0)
        finally:
            self.guard = False


def references(image):
    """All halfword-start candidates; code/data and indirect limits stay explicit."""
    decoder = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    decoder.detail = True
    words = {}
    for offset in range(len(image) - 3):
        value = struct.unpack_from("<I", image, offset)[0]
        if value == SETTINGS or BACKUP <= value < BACKUP + 38:
            words[BASE + offset] = value
    branches, literal_refs = [], []
    for offset in range(0, len(image) - 3, 2):
        ins = next(decoder.disasm(image[offset:offset + 4], BASE + offset, count=1), None)
        if ins is None:
            continue
        if (ins.mnemonic in ("bl", "blx", "b", "b.w") and ins.operands[0].type == C.ARM_OP_IMM
                and ins.operands[0].imm in (0x0801a608, 0x0801a61c)):
            branches.append({"address": f"{ins.address:08x}", "kind": ins.mnemonic,
                             "target": f"{ins.operands[0].imm:08x}"})
        if (ins.mnemonic.startswith("ldr") and len(ins.operands) > 1
                and ins.operands[1].type == C.ARM_OP_MEM
                and ins.operands[1].mem.base == C.ARM_REG_PC):
            literal = ((ins.address + 4) & ~3) + ins.operands[1].mem.disp
            if literal in words:
                literal_refs.append({"address": f"{ins.address:08x}", "literal": f"{literal:08x}",
                                     "value": f"{words[literal]:08x}", "instruction": ins.op_str})
    record_calls = [row["address"] for row in branches if row["target"] == "0801a608"]
    switch_calls = [row["address"] for row in branches if row["target"] == "0801a61c"]
    assert len(record_calls) == 19 and len(switch_calls) == 5
    assert all(row["kind"] == "bl" for row in branches)
    pointer_occurrences = {}
    for getter in (0x0801a608, 0x0801a61c):
        pointer_occurrences[f"{getter:08x}"] = [f"{BASE + offset:08x}"
            for offset in range(len(image) - 3)
            if image[offset:offset + 4] == struct.pack("<I", getter | 1)]
    assert not any(pointer_occurrences.values())
    return {"direct_getter_branches": branches, "literal_reference_candidates": literal_refs,
            "thumb_pointer_occurrences": pointer_occurrences,
            "indirect_computed_aliases_and_code_data_distinction_not_exhaustive": True}


def main():
    image = firmware_image()
    rows = []
    for seed, switches in itertools.product((0, 1), ((0, 0), (1, 0), (0, 1), (1, 1), (2, 255), (255, 2))):
        m = FileMachine()
        records = b"".join(struct.pack("<BII", 71 + seed + i,
            1800000000 + seed * 10000 + i * 2000, 1800001000 + seed * 10000 + i * 2000)
            for i in range(4))
        m.uc.mem_write(BACKUP, records + bytes(switches))
        expected = m.checked_file()
        m.file = expected
        # Force a checksum mismatch to reach the actual complete file write.
        m.uc.mem_write(SETTINGS + 4, bytes((expected[4] ^ 255,)))
        assert m.call_file_routine(0x0802c994) == 1
        assert m.file == expected and m.read(SETTINGS, SIZE) == expected
        assert len(m.reads) == 38 and not m.writes and not m.defaults
        rows.append({"group": "full_file_save", "seed": seed, "switches_raw": switches,
                     "filename": "sysPara", "file_bytes": len(m.file),
                     "file_sha256": hashlib.sha256(m.file).hexdigest(),
                     "backup_hex": m.file[0x161:0x187].hex(),
                     "all_backup_bytes_read": True, "backup_unchanged": True, "file_calls": m.calls.copy()})
        m.uc.mem_write(SETTINGS, bytes(SIZE))
        m.call_file_routine(0x080288c8)
        assert m.read(SETTINGS, SIZE) == expected and not m.defaults
        rows.append({"group": "full_file_load", "seed": seed, "switches_raw": switches,
                     "file_bytes": SIZE, "backup_hex": m.read(BACKUP, 38).hex(),
                     "whole_file_loaded_exactly": True, "defaults_called": 0,
                     "file_calls": m.calls.copy(), "external_export_protocol_proved": False})

    for defect in ("short", "magic", "checksum", "zero_charge_power"):
        m = FileMachine()
        m.uc.mem_write(BACKUP, bytes(range(38)))
        value = bytearray(m.checked_file())
        if defect == "short":
            value = value[:-1]
        elif defect == "magic":
            value[:4] = b"BAD!"
        elif defect == "checksum":
            value[4] ^= 1
        else:
            value[0xf:0x11] = bytes(2)
            value[4] = crc8(value[5:])
        m.file = bytes(value)
        before = m.read(BACKUP, 38)
        m.call_file_routine(0x080288c8)
        after = m.read(BACKUP, 38)
        assert m.defaults == 1 and after != before
        rows.append({"group": "invalid_file_load", "defect": defect, "defaults_called": 1,
                     "saved_backup_replaced": True, "backup_after_hex": after.hex(),
                     "file_calls": m.calls.copy(), "physical_storage_not_accessed": True})

    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
              "cases": len(rows), "hardware_access": False,
              "references": references(image), "results": rows}
    path = ROOT / "gen2-syspara-backup-results.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    path.chmod(0o600)
    sources = (Path(__file__).name, "emulate_charging_followup.py", "emulate_general_settings.py",
               "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows), "hardware_access": False,
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in sources}, "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "substitutes": ["in-memory synthetic file open/read/write/rewind/sync/close", "memcmp/libc",
                        "logging", "persistence scheduling", "product-default option"],
        "excluded": ["real files or flash", "external file-export protocol", "indirect-reference completeness",
                     "radio/app execution", "hardware", "actual clocks and outputs"]}
    manifest_path = ROOT / "gen2-syspara-backup-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "hardware_access": False, "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
