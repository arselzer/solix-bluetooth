#!/usr/bin/env python3
"""Offline ownership audit of A1763 main 1.1.4.9's radio diagnostic table.

Executes actual RAM initialization, timer initialization, ordinary app-table
installation and the complete wireless initialization call tree. Only logging
is substituted. A separate bounded static candidate search follows constants;
it is not a whole-program absence proof. No hardware or network is accessed.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import itertools
import json
from pathlib import Path
import struct

import capstone
from capstone import arm as C
import unicorn
from unicorn import arm_const as U


BASE, STOP, STACK = 0x08005000, 0x08005000, 0x2001F000
TARGET, SETTINGS = 0x20000760, 0x20001D48
SHA256 = "21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9"
DECODER = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
DECODER.detail = True


def decode(image: bytes, address: int):
    if not BASE <= address < BASE + len(image):
        return None
    return next(DECODER.disasm(image[address - BASE:address - BASE + 4], address, count=1), None)


class Replay:
    def __init__(self, image: bytes):
        self.image = image
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000, 0x40000)
        self.uc.mem_write(BASE, image)
        self.uc.mem_map(0x20000000, 0x20000)
        self.uc.mem_write(0x20000000, b"\xa5" * 0x20000)
        self.uc.reg_write(U.UC_ARM_REG_SP, STACK)
        self.phase = "ram_initialization"
        self.writes = []
        self.calls = set()
        self.visited = set()
        self.stopped = False
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write)
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.emu_start(0x08005A8D, 0x08005AA4, count=200000)
        assert self.uc.reg_read(U.UC_ARM_REG_PC) == 0x08005AA4
        assert self.read(TARGET, 8) == bytes(8)
        self.initial_target_writes = [row for row in self.writes if row["target_overlap"]]
        assert self.initial_target_writes
        self.ram_aliases = [f"{address:08x}" for address in range(0x20000000, 0x20012CD8, 4)
                            if TARGET <= int.from_bytes(self.read(address, 4), "little") < TARGET + 8]
        self.writes.clear()
        self.visited.clear()
        self.calls.clear()
        self.phase = "software_initialization"

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def write(self, uc, _access, address, size, value, _data):
        assert 0x20000000 <= address < address + size <= 0x20020000, "Non-RAM write"
        if address < 0x20000780 and address + size > 0x20000720:
            self.writes.append({"pc": f"{uc.reg_read(U.UC_ARM_REG_PC):08x}",
                                "address": f"{address:08x}", "size": size,
                                "value": value,
                                "target_overlap": address < TARGET + 8 and address + size > TARGET})

    def step(self, uc, address, _size, _data):
        self.visited.add(address)
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
        elif address in (0x0800D284, 0x08026FD4):
            uc.reg_write(U.UC_ARM_REG_PC, uc.reg_read(U.UC_ARM_REG_LR))
        elif self.phase != "ram_initialization":
            instruction = decode(self.image, address)
            if instruction and instruction.mnemonic in ("bl", "blx", "b.w"):
                operand = instruction.operands[0]
                if operand.type == C.ARM_OP_IMM:
                    self.calls.add((address, operand.imm))

    def call(self, address: int):
        self.stopped = False
        self.uc.reg_write(U.UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=300000)
        assert self.stopped, f"Instruction budget at {address:08x}"

    def run(self, bind: int, config_flag: int, canary: bool, app_first: bool) -> dict:
        expected = bytes.fromhex("a0a1a2a3a4a5a6a7") if canary else bytes(8)
        self.uc.mem_write(TARGET, expected)
        self.uc.mem_write(SETTINGS + 0x2A, bytes((bind,)))
        self.uc.mem_write(SETTINGS + 0x187, bytes((config_flag,)))
        self.uc.mem_write(0x20000164, struct.pack("<I", 0xFFFFFFFF))
        self.call(0x08010808)
        order = (0x08013C84, 0x080309C8) if app_first else (0x080309C8, 0x08013C84)
        for address in order:
            self.call(address)
        assert self.read(TARGET, 8) == expected
        assert not any(row["target_overlap"] for row in self.writes)
        assert self.read(0x20000758, 6) == struct.pack("<IH", 0x08032E60, 17)
        assert self.read(0x200007AF, 1) == bytes((bind,))
        assert int.from_bytes(self.read(0x20000164, 4), "little") == (
            0xEFFFFFFF if config_flag else 0xFFFFFFFF)
        callbacks = {0x10: 0x08022615, 0x0F: 0x080080A9, 0x0C: 0x08017705}
        for function, callback in callbacks.items():
            block = self.read(0x20009908 + function * 16, 16)
            assert block[1] == 1
            assert int.from_bytes(block[4:8], "little") == callback
        for address in (0x08031144, 0x08013B3C, 0x080275B8, 0x08027040,
                        0x080275F8, 0x08027AD0, 0x08017D08, 0x080291B0):
            assert address in self.visited
        return {"bind": bind, "configuration_flag_187": config_flag,
                "descriptor_seed": "canary" if canary else "zero",
                "app_init_first": app_first, "descriptor_unchanged": True,
                "descriptor_writes_after_ram_init": 0,
                "ordinary_app_table": {"pointer": "08032e60", "count": 17},
                "registered_callbacks": {f"{key:02x}": f"{value & ~1:08x}" for key, value in callbacks.items()},
                "nearby_writes": self.writes,
                "executed_instruction_addresses": len(self.visited),
                "executed_direct_calls": [{"from": f"{a:08x}", "to": f"{b:08x}"}
                                          for a, b in sorted(self.calls)]}


def static_candidates(image: bytes) -> dict:
    """Follow known constants from aligned literal-load/MOVW candidates.

    Calls are not recursively analyzed: caller-saved registers become unknown.
    RAM loads, stack spills, unknown indexes and unsupported transforms do not
    become guessed constants. Each seed has bounded branch exploration.
    """
    cache = {}

    def instruction(address):
        if address not in cache:
            cache[address] = decode(image, address)
        return cache[address]

    def read(address, size):
        if BASE <= address and address + size <= BASE + len(image):
            return int.from_bytes(image[address - BASE:address - BASE + size], "little")
        return None

    seeds = []
    direct = []
    for address in range(BASE, BASE + len(image) - 3, 2):
        ins = instruction(address)
        if not ins:
            continue
        op = ins.operands
        if ins.mnemonic in ("ldr", "ldr.w") and len(op) == 2 and op[1].type == C.ARM_OP_MEM:
            memory = op[1].mem
            if memory.base == C.ARM_REG_PC and not memory.index:
                value = read(((address + 4) & ~3) + memory.disp, 4)
                if value is not None and 0x20000000 <= value < 0x20020000:
                    seeds.append((address + ins.size, {op[0].reg: value}, address))
                    if TARGET - 8 <= value < TARGET + 8:
                        direct.append({"pc": f"{address:08x}", "literal_value": f"{value:08x}"})
        elif ins.mnemonic == "movw":
            seeds.append((address + ins.size, {op[0].reg: op[1].imm}, address))

    found, unresolved, limits, movt = set(), set(), set(), set()
    for start, initial, seed in seeds:
        queue = deque([(start, initial, 0)])
        visited = set()
        while queue and len(visited) < 300:
            address, state, depth = queue.popleft()
            marker = (address, tuple(sorted(state.items())))
            if marker in visited:
                continue
            visited.add(marker)
            if depth >= 96:
                limits.add(seed)
                continue
            ins = instruction(address)
            if not ins:
                continue
            name, op = ins.mnemonic.split(".")[0], ins.operands
            old = dict(state)

            def value(operand):
                if operand.type == C.ARM_OP_IMM:
                    result = operand.imm
                elif operand.type == C.ARM_OP_REG:
                    result = ((address + 4) & ~3) if operand.reg == C.ARM_REG_PC else old.get(operand.reg)
                else:
                    return None
                if result is None:
                    return None
                if operand.shift.type == C.ARM_SFT_LSL:
                    result <<= operand.shift.value
                elif operand.shift.type == C.ARM_SFT_LSR:
                    result >>= operand.shift.value
                elif operand.shift.type:
                    return None
                return result & 0xFFFFFFFF

            for register in ins.regs_access()[1]:
                state.pop(register, None)
            memories = [operand for operand in op if operand.type == C.ARM_OP_MEM]
            for memory_operand in memories:
                memory = memory_operand.mem
                base = ((address + 4) & ~3) if memory.base == C.ARM_REG_PC else old.get(memory.base)
                index = old.get(memory.index) if memory.index else 0
                if memory_operand.shift.type == C.ARM_SFT_LSL and index is not None:
                    index <<= memory_operand.shift.value
                elif memory_operand.shift.type:
                    index = None
                effective = (base + index + memory.disp) & 0xFFFFFFFF if base is not None and index is not None else None
                width = 1 if name.endswith("b") else 2 if name.endswith("h") else 8 if name.endswith("d") else 4
                if effective is not None and effective < TARGET + 8 and effective + width > TARGET - 8:
                    found.add((address, effective, width, "write" if name.startswith("str") else "read", ins.mnemonic + " " + ins.op_str))
                if base is not None and 0x20000700 <= base < 0x20000800 and effective is None:
                    unresolved.add((address, base, ins.mnemonic + " " + ins.op_str))
                if name.startswith("ldr") and op[0].type == C.ARM_OP_REG and effective is not None:
                    constant = read(effective, width)
                    if constant is not None:
                        state[op[0].reg] = constant
                if ins.writeback:
                    state.pop(memory.base, None)
            if name in ("mov", "movs", "movw") and len(op) == 2:
                constant = value(op[1])
                if constant is not None:
                    state[op[0].reg] = constant
            elif name == "movt" and op[0].reg in old:
                state[op[0].reg] = (old[op[0].reg] & 0xFFFF) | (op[1].imm << 16)
                if 0x20000700 <= state[op[0].reg] < 0x20000800:
                    movt.add((address, state[op[0].reg]))
            elif name == "adr":
                state[op[0].reg] = ((address + 4) & ~3) + op[1].imm
            elif name in ("add", "adds", "addw", "sub", "subs", "subw", "orr", "orrs", "and", "ands"):
                left = old.get(op[0].reg) if len(op) == 2 else value(op[1])
                right = value(op[-1])
                if left is not None and right is not None:
                    result = left + right if name.startswith("add") else left - right if name.startswith("sub") else left | right if name.startswith("orr") else left & right
                    state[op[0].reg] = result & 0xFFFFFFFF
            elif name in ("lsl", "lsls", "lsr", "lsrs"):
                left = old.get(op[0].reg) if len(op) == 2 else value(op[1])
                right = value(op[-1])
                if left is not None and right is not None:
                    state[op[0].reg] = ((left << right) if name.startswith("lsl") else (left >> right)) & 0xFFFFFFFF
            if name in ("bl", "blx"):
                for register in (C.ARM_REG_R0, C.ARM_REG_R1, C.ARM_REG_R2, C.ARM_REG_R3, C.ARM_REG_R12):
                    state.pop(register, None)
            elif name == "bx" or (name == "pop" and any(x.type == C.ARM_OP_REG and x.reg == C.ARM_REG_PC for x in op)):
                continue
            elif ins.group(capstone.CS_GRP_JUMP):
                destinations = [x.imm for x in op if x.type == C.ARM_OP_IMM]
                if destinations:
                    queue.append((destinations[-1], dict(state), depth + 1))
                if name == "b" and ins.cc == C.ARM_CC_AL:
                    continue
                if not destinations:
                    continue
            queue.append((address + ins.size, state, depth + 1))
        if queue:
            limits.add(seed)
    return {"aligned_candidate_seeds": len(seeds), "per_seed_instruction_depth": 96,
            "per_seed_state_limit": 300, "seeds_reaching_a_bound": len(limits),
            "nearby_direct_literal_loads": direct,
            "resolved_nearby_accesses": [{"pc": f"{a:08x}", "address": f"{b:08x}", "width": c,
                                          "access": d, "instruction": e} for a, b, c, d, e in sorted(found)],
            "unresolved_nearby_indexed_accesses": [{"pc": f"{a:08x}", "base": f"{b:08x}", "instruction": c}
                                                   for a, b, c in sorted(unresolved)],
            "nearby_movw_movt_results": [{"pc": f"{a:08x}", "value": f"{b:08x}"} for a, b in sorted(movt)],
            "limits": "Aligned candidates may include data or instruction interiors. Branch exploration is bounded; calls, RAM pointer loads, stack aliases, IT-state and unsupported transforms are not modeled completely. No whole-program absence claim."}


def indexed_read_case(image: bytes, variant: int) -> dict:
    """Execute the bounded loop behind the scan's one real indexed-read lead."""
    machine = Replay(image)
    uc = machine.uc
    uc.mem_write(0x200000E4, bytes((variant,)))
    for index in range(5):
        uc.mem_write(0x200007BC + 20 * index, bytes((index,)))
    before = machine.read(TARGET, 8)
    reads = []

    def read_hook(_uc, _access, address, size, _value, _data):
        if uc.reg_read(U.UC_ARM_REG_PC) == 0x08019B5E:
            reads.append(address)
            assert size == 1

    uc.hook_add(unicorn.UC_HOOK_MEM_READ, read_hook)
    uc.reg_write(U.UC_ARM_REG_R4, 0)  # Set by 08019b38 before this loop.
    uc.emu_start(0x08019B55, 0x08019B6E, count=1000)
    assert uc.reg_read(U.UC_ARM_REG_PC) == 0x08019B6E
    expected = [0x200007BC + 20 * index for index in range(min(variant + 1, 5))]
    assert reads == expected
    assert not any(row["target_overlap"] for row in machine.writes)
    assert machine.read(TARGET, 8) == before
    return {"case": "nearby_indexed_variant_read", "variant": variant,
            "read_addresses": [f"{a:08x}" for a in reads],
            "descriptor_unchanged": True, "stopped_at": "08019b6e"}


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
    cases = []
    representative = None
    for bind, flag, canary, first in itertools.product((0, 1), (0, 1), (False, True), (False, True)):
        machine = Replay(image)
        cases.append(machine.run(bind, flag, canary, first))
        representative = machine
    candidates = static_candidates(image)
    assert {(row["pc"], row["address"], row["access"])
            for row in candidates["resolved_nearby_accesses"]} == {
        ("080080e4", "2000075c", "read"), ("080080e6", "20000758", "read"),
        ("0801771a", "20000764", "read"), ("0801771c", "20000760", "read"),
        ("080291b2", "20000758", "write"), ("080291b4", "2000075c", "write"),
    }
    assert {row["pc"] for row in candidates["unresolved_nearby_indexed_accesses"]} == {"08019b5e", "08026ed4"}
    string_address, public_string = 0x08026ECC, b"LCD_DATA_UPDATE_EVENT\0"
    assert image[string_address - BASE:string_address - BASE + len(public_string)] == public_string
    string_reference = decode(image, 0x08026E7A)
    assert string_reference.mnemonic == "adr"
    assert ((string_reference.address + 4) & ~3) + string_reference.operands[1].imm == string_address
    for variant in range(6):
        cases.append(indexed_read_case(image, variant))
    candidates["reviewed_indexed_candidates"] = [
        {"pc": "08019b5e", "classification": "Actual read-only variant lookup; six replay cases establish five possible addresses from 200007bc through 2000080c, stride20."},
        {"pc": "08026ed4", "classification": "False code candidate inside public LCD_DATA_UPDATE_EVENT string at08026ecc, referenced by ADR08026e7a."},
    ]
    descriptors = [dict(zip(("source", "destination", "size", "initializer"),
                           (f"{x:08x}" for x in struct.unpack_from("<IIII", image, address - BASE))))
                   for address in (0x080353B4, 0x080353C4)]
    result = {"firmware_sha256": SHA256, "model": "A1763 C1000 Gen 2 main 1.1.4.9",
              "cases_passed": len(cases), "startup_descriptors": descriptors,
              "startup_target_writes": representative.initial_target_writes,
              "startup_ram_word_aliases_to_descriptor": representative.ram_aliases,
              "cases": cases, "static_candidates": candidates,
              "claim": "Reviewed software initializers preserve the diagnostic command descriptor, while installing the adjacent ordinary table and protocol callbacks. No installer established by this bounded audit."}
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(encoded)
    args.manifest.write_text(json.dumps({"script": Path(__file__).name,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "firmware_sha256": SHA256, "results_sha256": hashlib.sha256(encoded).hexdigest(),
        "unicorn_version": unicorn.__version__, "capstone_version": capstone.__version__,
        "cases_passed": len(cases), "fixture_source": "Public firmware and synthetic RAM only"},
        indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases_passed": len(cases), "static_candidate_seeds": candidates["aligned_candidate_seeds"]}))


if __name__ == "__main__":
    main()
