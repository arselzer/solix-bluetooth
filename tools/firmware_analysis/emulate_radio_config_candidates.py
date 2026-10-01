#!/usr/bin/env python3
"""Replay two radio candidates that do not export controller backup plans.

Runs exact C1000 Gen 2 radio 0.3.3.0 RISC-V instructions. JSON primitives,
allocation, file reads/writes, libc, identity providers and logging are host
substitutes. All data is synthetic. No app binary, network or device access.
This is a bounded function audit, not proof that no other export exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_RISCV, UC_MODE_RISCV32, UC_HOOK_CODE
from unicorn import riscv_const as R


IMAGE_NAME = "c1000-radio-validated.bin"
IMAGE_SHA256 = "e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8"
SETTINGS = 0x3FC89EB4
MAGIC = 0x20221224


class Machine:
    def __init__(self, image: bytes, fail_json_call: int = 0):
        self.uc = Uc(UC_ARCH_RISCV, UC_MODE_RISCV32)
        for address, size in ((0x3C130000, 0x40000), (0x3FC80000, 0x90000),
                              (0x40380000, 0x10000), (0x50000000, 0x1000),
                              (0x42000000, 0x200000), (0x40000000, 0x1000),
                              (0x20000000, 0x100000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(0x40000000, b"\x13\0\0\0" * 1024)
        offset = 24
        for _ in range(image[1]):
            address, size = struct.unpack_from("<II", image, offset)
            offset += 8
            self.uc.mem_write(address, image[offset:offset+size])
            offset += size
        self.heap = 0x20000000
        self.objects: dict[int, object] = {}
        self.json_calls = 0
        self.fail_json_call = fail_json_call
        self.file_calls: list[dict] = []
        self.normal_ok = True
        self.backup_ok = True
        self.normal = self.record()
        self.backup = self.record()
        self.uc.reg_write(R.UC_RISCV_REG_SP, 0x3FD0F000)
        self.uc.reg_write(R.UC_RISCV_REG_GP, 0x3FC83000)
        self.providers = {address: self.alloc(value.ljust(length, b"\0"))
                          for address, value, length in (
                              (0x4201232E, b"anker_power", 32),
                              (0x42012416, b"A1763", 16),
                              (0x42012450, b"SYNTHETIC00000001", 32),
                              (0x420122F4, b"synthetic-account", 128))}
        self.uc.hook_add(UC_HOOK_CODE, self.step)

    @staticmethod
    def record() -> bytes:
        data = bytearray(0x154)
        struct.pack_into("<I", data, 0, MAGIC)
        for offset, value in ((4, b"anker_power"), (0x24, b"A1763"),
                              (0x34, b"SYNTHETIC00000001"),
                              (0x54, b"synthetic-account")):
            data[offset:offset+len(value)] = value
        return bytes(data)

    def alloc(self, data: bytes | int) -> int:
        if isinstance(data, int):
            data = bytes(data)
        address = self.heap
        self.heap += max(16, (len(data)+15) & ~15)
        self.uc.mem_write(address, data)
        return address

    def string(self, address: int) -> str:
        data = bytearray()
        for offset in range(4096):
            value = self.uc.mem_read(address+offset, 1)[0]
            if not value:
                return data.decode("ascii")
            data.append(value)
        raise AssertionError("Unterminated synthetic string")

    def node(self, value: object) -> int:
        self.json_calls += 1
        if self.json_calls == self.fail_json_call:
            return 0
        address = self.alloc(16)
        self.objects[address] = value
        return address

    def step(self, uc, address, _size, _data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address == 0x4204D08A:
            result = self.node([])
        elif address == 0x4204D0A4:
            result = self.node({})
        elif address == 0x4204CFAC:
            result = self.node(self.string(a0))
        elif address == 0x4204CED4:
            self.objects[a0].append(self.objects[a1])
            result = 1
        elif address == 0x4204CED8:
            self.objects[a0][self.string(a1)] = self.objects[a2]
            result = 1
        elif address == 0x4204CFE4:
            self.objects[a0][self.string(a1)] = self.string(a2)
            result = 1
        elif address == 0x4204CEA8:
            self.json_calls += 1
            result = 0 if self.json_calls == self.fail_json_call else self.alloc(
                json.dumps(self.objects[a0], sort_keys=True).encode()+b"\0")
        elif address in (0x4204C93C, 0x4202D3B0, 0x420232B8, 0x42023636):
            result = 0
        elif address in (0x42014EEC, 0x420148F6, 0x42014EE6):
            assert self.string(a0) == "mqtt_client_file"
            assert a1 == SETTINGS and a2 == 0x154
            kind = {0x42014EEC: "normal_read", 0x420148F6: "backup_read",
                    0x42014EE6: "normal_write"}[address]
            self.file_calls.append({"operation": kind, "file": self.string(a0), "size": a2})
            if kind == "normal_write":
                result = 0
            else:
                ok = self.normal_ok if kind == "normal_read" else self.backup_ok
                data = self.normal if kind == "normal_read" else self.backup
                if ok:
                    uc.mem_write(a1, data)
                result = 0 if ok else 1
        elif address == 0x40000354:
            uc.mem_write(a0, bytes((a1 & 255,))*a2)
            result = a0
        elif address == 0x40000358:
            uc.mem_write(a0, bytes(uc.mem_read(a1, a2)))
            result = a0
        elif address == 0x40000374:
            result = len(self.string(a0))
        elif address in self.providers:
            result = self.providers[address]
        elif any(lo <= address < hi for lo, hi in (
                (0x420224D8, 0x42022736), (0x420265FA, 0x42026732),
                (0x42026878, 0x42026A50))):
            return
        else:
            raise AssertionError(f"Unexpected code at {address:08x}")
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def run(self, address: int, *args: int) -> int:
        for i, value in enumerate(args):
            self.uc.reg_write(R.UC_RISCV_REG_A0+i, value)
        self.uc.reg_write(R.UC_RISCV_REG_RA, 0x50000000)
        self.uc.emu_start(address, 0x50000000, count=100000)
        assert self.uc.reg_read(R.UC_RISCV_REG_PC) == 0x50000000
        return self.uc.reg_read(R.UC_RISCV_REG_A0)


def run_suite(image: bytes) -> dict:
    rows = []
    for length in (1, 40):
        m = Machine(image)
        output = m.alloc(4)
        result = m.run(0x420224D8, output, m.alloc(b"x"*length+b"\0"))
        assert result == 0
        pointer = struct.unpack("<I", m.uc.mem_read(output, 4))[0]
        body = json.loads(m.string(pointer))
        assert body == {"account": "x"*length, "base_get_device_param": [
            {"destination": "user", "param_names": ["20001"]}]}
        assert not m.file_calls
        rows.append({"case": "point_switch_request", "synthetic_account_length": length,
                     "return": result, "parameter_names": ["20001"],
                     "destination": "user", "file_calls": []})
    for invalid in ("null_output", "occupied_output", "null_account"):
        m = Machine(image)
        output = 0 if invalid == "null_output" else m.alloc(
            struct.pack("<I", 1 if invalid == "occupied_output" else 0))
        account = 0 if invalid == "null_account" else m.alloc(b"synthetic\0")
        result = m.run(0x420224D8, output, account)
        assert result == 5 and m.json_calls == 0
        rows.append({"case": invalid, "return": result, "json_allocations": 0})
    for failure in range(1, 7):
        m = Machine(image, fail_json_call=failure)
        output = m.alloc(4)
        result = m.run(0x420224D8, output, m.alloc(b"synthetic\0"))
        assert result == 2 and m.json_calls == failure
        assert bytes(m.uc.mem_read(output, 4)) == bytes(4)
        rows.append({"case": "json_boundary_failure", "failure_call": failure,
                     "return": result, "output_null": True})
    for normal_ok, backup_ok, magic_ok in ((True, True, True), (False, True, True),
                                           (False, False, True), (False, True, False)):
        m = Machine(image)
        m.normal_ok, m.backup_ok = normal_ok, backup_ok
        if not magic_ok:
            m.backup = bytes(4)+m.backup[4:]
        result = m.run(0x42026918)
        assert result == 0
        assert bytes(m.uc.mem_read(SETTINGS, 0x154)) == m.record()
        operations = [x["operation"] for x in m.file_calls]
        assert operations == (["normal_read"] if normal_ok else
            ["normal_read", "backup_read", "normal_write"] +
            ([] if backup_ok and magic_ok else ["normal_write"]))
        rows.append({"case": "mqtt_file_recovery", "normal_read_ok": normal_ok,
                     "backup_read_ok": backup_ok, "backup_magic_ok": magic_ok,
                     "return": result, "file_calls": m.file_calls,
                     "synthetic_connection_record_restored": True})
    return {"model": "C1000 Gen 2 radio 0.3.3.0", "image_sha256": IMAGE_SHA256,
            "cases": len(rows), "results": rows, "limits": __doc__.strip(),
            "actual_functions": ["420224d8", "42026918", "420265fa", "42026878"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    directory = args.firmware_dir or Path(os.environ.get("SOLIX_FIRMWARE_DIR",
        str(Path(__file__).resolve().parents[2]/"firmware/c1000_gen2/1.1.4.9")))
    image = (directory/IMAGE_NAME).read_bytes()
    if hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise ValueError("Firmware hash mismatch; these addresses require the documented radio image")
    results = run_suite(image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True)+"\n")
    print(f"Passed {results['cases']} synthetic radio candidate cases")


if __name__ == "__main__":
    main()
