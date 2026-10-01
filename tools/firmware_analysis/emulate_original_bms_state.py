#!/usr/bin/env python3
"""Replay original A1761 BMS current/state producers and main consumers offline.

Uses MainBMS, SubBMS and MainMcu from the public 1.5.9 bundle. Only selected
RAM-only instruction blocks execute; no hardware, serial transport or clock
is simulated. Producer sample counts are deliberately not converted to time.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R4,
    UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
    UC_ARM_REG_R10, UC_ARM_REG_SP,
)

from emulate_c1000_original_commands import IMAGE, IMAGE_SHA256, REGISTERS
from emulate_original_fast_retention import BMS, PAYLOAD, StatusMachine

RAM, RAM_SIZE, STACK = 0x20000000, 0x10000, 0x2000f000
STATE, SIGNED_CURRENT, DIGITAL_FLAGS = 0x200003bc, 0x2000005c, 0x2000007c
PACKET = 0x20008000
IMAGES = {
    "main": {
        "filename": "MainBMS-decoded.bin",
        "sha256": "dfb7106d8b189bf1ed19e7c2f6ae15555aeb54d84269292190727604168a1d92",
        "split": (0x08008f4a, 0x08008fea),
        "producer": (0x0800b70a, 0x0800b852),
        "charge": 0x2000046c, "discharge": 0x200008b8,
        "charge_counter": 0x200003c2, "discharge_counter": 0x200003c4,
        "no_charge_counter": 0x200003cc, "inactive_counter": 0x200003c6,
        "serializer": (0x0800833c, 0x0800835e),
        "mask_reset": (0x0800b5b4, 0x0800b852),
    },
    "expansion": {
        "filename": "SubBMS-decoded.bin",
        "sha256": "dc37fadf824193edcef2030d187d9f6320de8b7b8d1a57c2fd2c1483d8d2c627",
        "split": (0x0800673e, 0x08006890),
        "producer": (0x08008edc, 0x0800902a),
        "charge": 0x20000434, "discharge": 0x2000087c,
        "charge_counter": 0x200003c4, "discharge_counter": 0x200003c6,
        "no_charge_counter": 0x200003ce, "inactive_counter": 0x200003c8,
        "serializer": (0x08005b8a, 0x08005ba6),
        "mask_reset": (0x08008e9c, 0x0800902a),
    },
}


class BmsMachine:
    def __init__(self, image: bytes, kind: str):
        self.kind, self.config = kind, IMAGES[kind]
        if hashlib.sha256(image).hexdigest() != self.config["sha256"]:
            raise ValueError(f"Unexpected {kind} BMS image")
        self.image_end = 0x08002000 + len(image)
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB)
        self.uc.mem_map(0x08000000, 0x10000)
        self.uc.mem_write(0x08002000, image)
        self.uc.mem_protect(0x08000000, 0x10000,
                            unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_map(RAM, RAM_SIZE)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write_guard)
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.code_guard)
        self.reset()

    def write_guard(self, uc, access, address, size, value, data):
        if not RAM <= address < address + size <= RAM + RAM_SIZE:
            raise AssertionError(f"Unexpected non-RAM write {address:08x}")

    def code_guard(self, uc, address, size, data):
        if not 0x08002000 <= address < self.image_end:
            raise AssertionError(f"Unexpected execution {address:08x}")

    def put(self, address: int, value: int, kind: str = "I") -> None:
        self.uc.mem_write(address, struct.pack("<" + kind, value))

    def get(self, address: int, kind: str = "I") -> int:
        return struct.unpack("<" + kind,
                             self.uc.mem_read(address, struct.calcsize(kind)))[0]

    def reset(self, state: int = 0) -> None:
        self.uc.mem_write(RAM, bytes(RAM_SIZE))
        self.put(STATE, state, "H")
        # Both relevant digital-condition bits are asserted so the split's
        # extra below-600-current suppression branches do not apply. This is
        # a synthetic condition, not a claim about actual MOSFET/GPIO state.
        self.put(DIGITAL_FLAGS, 0x0a, "B")

    def run(self, start: int, stop: int, registers: dict | None = None) -> None:
        for register in REGISTERS:
            self.uc.reg_write(register, 0)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        for register, value in (registers or {}).items():
            self.uc.reg_write(register, value)
        self.uc.emu_start(start | 1, stop, count=20000)
        if self.uc.reg_read(UC_ARM_REG_PC) != stop:
            raise AssertionError("Producer failed to reach its bounded stop")

    def current(self, milliamps: int) -> tuple[int, int]:
        self.put(SIGNED_CURRENT, milliamps, "i")
        self.run(*self.config["split"], {UC_ARM_REG_R6: DIGITAL_FLAGS})
        return self.get(self.config["charge"]), self.get(self.config["discharge"])

    def sample(self) -> int:
        self.run(*self.config["producer"], {UC_ARM_REG_R0: self.get(STATE, "H")})
        return self.get(STATE, "H")

    def packet_fields(self) -> dict:
        registers = ({UC_ARM_REG_R4: 0x20000038, UC_ARM_REG_R6: PACKET}
                     if self.kind == "main" else
                     {UC_ARM_REG_R5: 0x20000038, UC_ARM_REG_R7: PACKET})
        self.run(*self.config["serializer"], registers)
        return {"state": self.get(PACKET + 0x34, "H"),
                "discharge_current_ma": self.get(PACKET + 0x40),
                "charge_current_ma": self.get(PACKET + 0x44)}


def main_controller_consumer(m: StatusMachine, fields: dict) -> dict:
    m.seed()
    m.put(PAYLOAD + 0x34, fields["state"], "H")
    m.put(PAYLOAD + 0x40, fields["discharge_current_ma"])
    m.put(PAYLOAD + 0x44, fields["charge_current_ma"])
    m.run(0x080134aa, stop=0x080134ce,
          registers={UC_ARM_REG_R4: PAYLOAD, UC_ARM_REG_R5: BMS})
    expected = fields["charge_current_ma" if fields["state"] == 2
                      else "discharge_current_ma"]
    assert m.get(BMS + 0x0c) == expected
    assert m.get(BMS + 0x25, "B") == fields["state"]
    return {"selected_pack_current_ma": expected,
            "stored_raw_state": m.get(BMS + 0x25, "B")}


def run_suite(directory: Path) -> dict:
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not use Python -O")
    controller = StatusMachine((directory / "MainMcu-decoded.bin").read_bytes())
    result = {"main_image_sha256": IMAGE_SHA256,
              "bms_image_sha256": {k: v["sha256"] for k, v in IMAGES.items()},
              "unicorn_version": unicorn.__version__,
              "scope": "Synthetic original 1.5.9 package BMS blocks; no 1.7.1, DSP, ADC or real-time validation",
              "current_entry": [], "zero_current_decay": [],
              "interrupted_entry": [], "mask_reset": [],
              "main_remaining_estimate": [], "special_state": []}
    for kind, config in IMAGES.items():
        m = BmsMachine((directory / config["filename"]).read_bytes(), kind)
        for current in (-1000, -201, -200, -199, -35, -34, 0, 34, 35, 199, 200, 201, 1000):
            m.reset()
            charge, discharge = m.current(current)
            assert charge == (current if current >= 35 else 0)
            assert discharge == (-current if current <= -35 else 0)
            expected = 2 if current >= 200 else 1 if current <= -200 else 0
            checkpoints = {}
            for sample in range(1, 103):
                state = m.sample()
                assert state == (expected if sample >= 101 else 0)
                if sample in (1, 100, 101, 102):
                    checkpoints[str(sample)] = state
            packet = m.packet_fields()
            assert packet == {"state": expected, "discharge_current_ma": discharge,
                              "charge_current_ma": charge}
            result["current_entry"].append({"bms": kind, "signed_current_ma": current,
                "states_by_producer_sample": checkpoints, "serialized": packet,
                "main_consumer": main_controller_consumer(controller, packet)})

        for initial in (0, 1, 2, 4):
            m.reset(initial)
            m.current(0)
            clear_sample = 101 if initial == 2 else 8001
            checkpoints = {}
            for sample in range(1, 8003):
                state = m.sample()
                assert state == (0 if sample >= clear_sample else initial)
                if sample in (100, 101, 8000, 8001, 8002):
                    checkpoints[str(sample)] = state
            result["zero_current_decay"].append({"bms": kind, "initial_state": initial,
                "states_by_producer_sample": checkpoints,
                "note": "Normal producer fragment only; other outer-loop policies are excluded"})

        for current, counter in ((200, "charge_counter"), (-200, "discharge_counter")):
            m.reset()
            m.current(current)
            for _ in range(60):
                assert m.sample() == 0
            assert m.get(config[counter], "H") == 60
            m.current(0)
            for _ in range(10):
                assert m.sample() == 0
            assert m.get(config[counter], "H") == 50
            m.current(current)
            for _ in range(50):
                assert m.sample() == 0
            assert m.sample() == (2 if current > 0 else 1)
            result["interrupted_entry"].append({"bms": kind, "signed_current_ma": current,
                "sequence": "60 qualifying, 10 zero-current, 51 qualifying samples",
                "counter_after_gap": 50, "final_state": m.get(STATE, "H"),
                "consecutive_samples_required": False})

        for initial, mask in itertools.product((1, 2), (0x1000, 0x2000, 0x4000, 0x8000)):
            m.reset(initial)
            m.current(1000 if initial == 2 else -1000)
            before_current = (m.get(config["charge"]), m.get(config["discharge"]))
            m.run(*config["mask_reset"], {UC_ARM_REG_R0: initial, UC_ARM_REG_R1: mask})
            assert m.get(STATE, "H") == 0
            assert (m.get(config["charge"]), m.get(config["discharge"])) == before_current
            result["mask_reset"].append({"bms": kind, "initial_state": initial,
                "status_mask": f"{mask:04x}", "state_after": 0,
                "nonzero_current_cache_unchanged": True})

        if kind == "main":
            m.reset()
            m.put(0x20001024, 1, "B")
            m.put(0x200009ca, 0x5a, "B")
            m.put(0x200008db, 1, "B")
            m.run(0x08006440, 0x0800645c)
            assert m.get(STATE, "H") == 4
            assert m.packet_fields()["state"] == 4
            result["special_state"].append({"bms": kind, "state": 4,
                "producer": "08006440..0800645c", "meaning": "unassigned"})

    # Capacity names are independently present as rm/fc in the main debug
    # format. Run the two real estimate branches with asymmetric capacities.
    for state in (0, 1, 2, 4):
        controller.seed()
        controller.put(BMS + 0x14, 1000)  # Remaining mAh.
        controller.put(BMS + 0x18, 4000)  # Full mAh.
        controller.put(PAYLOAD + 0x40, 1000)
        controller.put(PAYLOAD + 0x44, 1000)
        registers = {UC_ARM_REG_R4: PAYLOAD, UC_ARM_REG_R5: 0,
                     UC_ARM_REG_R7: 0xffff, UC_ARM_REG_R8: 0,
                     UC_ARM_REG_R10: BMS}
        start, stop = ((0x08013548, 0x080135a4) if state == 2
                       else (0x080135be, 0x080135e6))
        controller.run(start, stop=stop, registers=registers)
        expected = 30 if state == 2 else 10
        assert controller.get(BMS + 2, "H") == expected
        result["main_remaining_estimate"].append({"state_branch": state,
            "remaining_mah": 1000, "full_mah": 4000, "selected_current_ma": 1000,
            "estimate_tenths_hour": expected,
            "formula": "(full - remaining) * 10 / charge_current" if state == 2
                       else "remaining * 10 / discharge_current"})
    result["cases"] = sum(len(v) for v in result.values() if isinstance(v, list))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-directory", type=Path, default=IMAGE.parent,
                        help="Public decoded 1.5.9 images; all three SHA-256 values checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.image_directory)
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
