#!/usr/bin/env python3
"""Replay the radio module-update status getter and bounded producer decisions.

Public A1763 radio 0.3.3.0 only. Actual RISC-V getter, raw TLV serializer,
OTA-type getter, start/stop callbacks and selected failure-decision blocks run
against synthetic RAM. Transfer start/stop, logging, ROM memcpy and response
transport are host substitutes. No update engine, hardware, network, reset,
flash, credentials or external protocol request runs.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn import riscv_const as R


SHA256 = "e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8"
STOP, STACK = 0x50000000, 0x3FD0F000
STATUS, ACTIVE, OTA_TYPE, MCU_SLOT = 0x3FC90678, 0x3FC905C4, 0x3FC8AA64, 0x3FC8B240


class Machine:
    def __init__(self, image: bytes):
        self.uc = unicorn.Uc(unicorn.UC_ARCH_RISCV, unicorn.UC_MODE_RISCV32)
        for address, size in ((0x3C130000, 0x40000), (0x3FC80000, 0x90000),
                              (0x40380000, 0x10000), (0x42000000, 0x200000),
                              (0x40000000, 0x1000), (STOP, 0x1000), (0x20000000, 0x1000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(0x40000000, b"\x13\0\0\0" * 1024)
        offset = 24
        for _ in range(image[1]):
            address, size = struct.unpack_from("<II", image, offset)
            offset += 8
            self.uc.mem_write(address, image[offset:offset + size])
            offset += size
        self.uc.mem_write(STOP, b"\x13\0\0\0")
        self.uc.reg_write(R.UC_RISCV_REG_SP, STACK)
        self.uc.reg_write(R.UC_RISCV_REG_GP, 0x3FC83000)
        self.producing = False
        self.stops = {STOP}
        self.stopped = False
        self.responses = []
        self.transport_error = 0
        self.boundaries = []
        self.global_writes = []
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write_guard)

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def word(self, address: int) -> int:
        return int.from_bytes(self.read(address, 4), "little")

    def seed(self, address: int, value: int, size: int = 4):
        self.uc.mem_write(address, value.to_bytes(size, "little"))

    def write_guard(self, uc, _access, address, size, value, _data):
        if STACK - 0x1000 <= address < address + size <= STACK:
            return
        allowed = ((STATUS, STATUS + 4), (ACTIVE, ACTIVE + 1)) if self.producing else ()
        assert any(lo <= address < address + size <= hi for lo, hi in allowed), (
            f"Unexpected write {address:08x} at {uc.reg_read(R.UC_RISCV_REG_PC):08x}")
        self.global_writes.append({"pc": f"{uc.reg_read(R.UC_RISCV_REG_PC):08x}",
                                   "address": f"{address:08x}", "size": size, "value": value})

    def step(self, uc, address, _size, _data):
        a0, a1, a2 = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(3)]
        if address in self.stops:
            self.stopped = True
            uc.emu_stop()
            return
        if address in (0x421271AC, 0x421270B8):
            result = 0
        elif address == 0x40000358:
            self.write_guard(uc, 0, a0, a2, 0, None)
            uc.mem_write(a0, self.read(a1, a2))
            result = a0
        elif address == 0x4204FA68:
            self.responses.append(self.read(a1, a2))
            result = self.transport_error
        elif address in (0x4204529A, 0x42047C5A):
            assert self.producing
            self.boundaries.append({"address": f"{address:08x}",
                                    "operation": "transfer_start" if address == 0x4204529A else "transfer_stop",
                                    "arguments": [a0, a1, a2] if address == 0x4204529A else []})
            result = 0
        elif any(lo <= address < hi for lo, hi in (
                (0x4203CAB8, 0x4203CB26), (0x4204DD4C, 0x4204DDF4),
                (0x4202937C, 0x42029386), (0x42047478, 0x42047510),
                (0x42047FB6, 0x42048054), (0x420400AC, 0x420400E0),
                (0x42042526, 0x4204255E))):
            return
        else:
            raise AssertionError(f"Unexpected instruction {address:08x}")
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def run(self, address: int, stops=None):
        self.stops = {STOP} if stops is None else set(stops)
        self.stopped = False
        self.uc.reg_write(R.UC_RISCV_REG_RA, STOP)
        self.uc.emu_start(address, 0, count=10000)
        assert self.stopped

    def query(self) -> str:
        self.producing = False
        before = self.read(0x3FC80000, 0x20000)
        self.uc.reg_write(R.UC_RISCV_REG_A0, 0x20000000)
        self.run(0x4203CAB8)
        assert self.read(0x3FC80000, 0x20000) == before
        expected = b"\0\xa1\x01" + bytes((self.word(STATUS) & 0xFF,))
        assert self.responses == [expected]
        return expected.hex()


def producer_case(image: bytes, name: str, ota_type: int, initial: int, *, slot=0, result=1) -> dict:
    m = Machine(image)
    m.seed(OTA_TYPE, ota_type)
    m.seed(STATUS, initial)
    m.seed(ACTIVE, 0x5A, 1)
    m.seed(MCU_SLOT, slot)
    m.producing = True
    expected = initial
    active = 0x5A
    expected_boundaries = []
    if name == "start_callback":
        if ota_type == 1:
            expected = 1
            expected_boundaries = [("transfer_start", [1, 1, 0])]
        elif ota_type in (2, 4) and not slot:
            expected_boundaries = [("transfer_start", [0, 1, 0])]
        active = 1
        m.run(0x42047478)
    elif name == "stop_callback":
        if ota_type == 1:
            expected = 2
        elif ota_type in (2, 4):
            expected_boundaries = [("transfer_stop", [])]
        active = 0
        m.run(0x42047FB6)
    elif name == "failure_decision_400ac":
        expected = 3 if ota_type == 1 else initial
        m.run(0x420400AC, stops={0x420400E0})
    else:
        assert name == "conditional_failure_decision_42526"
        m.uc.reg_write(R.UC_RISCV_REG_S0, result)
        expected = 3 if result == 1 and ota_type == 1 else initial
        m.run(0x42042526, stops={0x4204255E, 0x4204256C})
    assert m.word(STATUS) == expected
    assert m.read(ACTIVE, 1) == bytes((active,))
    assert [(x["operation"], x["arguments"]) for x in m.boundaries] == expected_boundaries
    reply = m.query()
    return {"case": name, "ota_type": ota_type, "initial_module_status": initial,
            "mcu_slot_present": bool(slot), "failure_argument": result,
            "final_module_status": expected, "separate_activity_byte": active,
            "substituted_update_boundaries": m.boundaries, "actual_global_writes": m.global_writes,
            "getter_reply": reply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions are required")
    image = args.image.read_bytes()
    assert hashlib.sha256(image).hexdigest() == SHA256
    m = Machine(image)
    table = dict(struct.unpack("<II", m.read(0x3C147BE4 + i * 8, 8)) for i in range(42))
    assert table[0x64] == 0x4203CAB8
    cases = []
    for value, error in itertools.product((0, 1, 2, 3, 4, 255, 0x100, 0x12345678, 0xFFFFFFFF), (0, 1)):
        m = Machine(image)
        m.seed(STATUS, value)
        m.transport_error = error
        cases.append({"case": "getter_0064", "module_status_word": value,
                      "transport_result": error, "getter_reply": m.query(),
                      "global_state_preserved": True, "response_attempts": len(m.responses)})
    types, states = (0, 1, 2, 3, 4, 255), (0, 3, 0x12345678)
    for ota_type, initial, slot in itertools.product(types, states, (0, 1)):
        cases.append(producer_case(image, "start_callback", ota_type, initial, slot=slot))
    for name, ota_type, initial in itertools.product(("stop_callback", "failure_decision_400ac"), types, states):
        cases.append(producer_case(image, name, ota_type, initial))
    for ota_type, initial, result in itertools.product(types, states, (0, 1, 2)):
        cases.append(producer_case(image, "conditional_failure_decision_42526", ota_type, initial, result=result))
    result = {"firmware_sha256": SHA256, "model": "A1763 C1000 Gen 2 radio 0.3.3.0",
              "cases_passed": len(cases), "cases": cases, "limits": __doc__.strip(),
              "claim": "0064 reads a cached Wi-Fi-module status byte, not a universal update-busy or installed-firmware-success indicator. External request reachability is unproved."}
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(encoded)
    args.manifest.write_text(json.dumps({"script": Path(__file__).name,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "firmware_sha256": SHA256, "results_sha256": hashlib.sha256(encoded).hexdigest(),
        "unicorn_version": unicorn.__version__, "cases_passed": len(cases),
        "fixture_source": "Public firmware and synthetic RAM only; update boundary calls suppressed"}, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases_passed": len(cases), "firmware_sha256": SHA256}))


if __name__ == "__main__":
    main()
