#!/usr/bin/env python3
"""Replay A1761 main 1.5.9 saved charge limits and configuration loading.

Controller parser/validator/default instructions execute against a synthetic
file and RAM. Persistence, event delivery and logging are captured boundaries.
No device, radio, network, DSP, flash or installed main 1.7.1 is accessed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from emulate_c1000_original_commands import (
    CTX, IMAGE, IMAGE_SHA256, Machine, PAYLOAD, changed_as,
)

CONFIG = 0x20002038
FLAGS = 0x200004fc
FAST_FLAGS = 0x200020c4
SMART = 0x200004ab
VARIANT = 0x20000454
REPORT_CACHE = 0x20000c6c
TEMPLATE = 0x08028c00
VALUES = (0, 1, 99, 100, 199, 200, 201, 749, 750, 751, 999, 1000, 1001, 1200, 65535)


class LoadMachine(Machine):
    def reset(self):
        super().reset()
        self.events = []
        self.file = None
        self.decisions = []

    def step(self, uc, address, size, data):
        boundaries = {
            0x08023474: "header_or_checksum_rejected",
            0x08023480: "settings_validation_reached",
            0x0802348e: "settings_or_marker_rejected",
            0x08023b6c: "default_initializer_reached",
        }
        if address in boundaries:
            self.decisions.append(boundaries[address])
        if address == 0x08010278:
            self.events.append({"event_id": uc.reg_read(UC_ARM_REG_R0),
                                "argument": uc.reg_read(UC_ARM_REG_R1)})
            self.back(1)
        elif address == 0x08025840:
            self.calls.append("configuration_file_persistence")
            self.back(1)
        elif address == 0x0800ccf0:
            self.calls.append("logger")
            self.back(1)
        elif address == 0x0801a998:
            assert self.file is not None
            self.calls.append("synthetic_file_open")
            self.back(0)
        elif address == 0x0801b038:
            assert uc.reg_read(UC_ARM_REG_R2) == CONFIG
            assert uc.reg_read(UC_ARM_REG_R3) == 44 and len(self.file) == 44
            uc.mem_write(CONFIG, self.file)
            self.calls.append("synthetic_file_read")
            self.back(44)
        elif address == 0x0801a8bc:
            self.calls.append("synthetic_file_close")
            self.back(0)
        else:
            super().step(uc, address, size, data)

    def put(self, address, value, kind="I"):
        self.uc.mem_write(address, struct.pack("<" + kind, value))

    def get(self, address, kind="I"):
        return struct.unpack("<" + kind, self.uc.mem_read(address, struct.calcsize(kind)))[0]

    def seed(self, variant=0, watts=1000):
        self.reset()
        self.put(VARIANT, variant, "B")
        self.uc.mem_write(CONFIG+8, bytes(self.uc.mem_read(TEMPLATE, 36)))
        self.put(CONFIG+8, watts, "H")
        self.put(CONFIG+10, 0, "H")  # Device Timeout Never.
        self.put(CONFIG+0x17, 3, "B")
        self.put(CONFIG+0x1d, 1, "B")
        self.put(CONFIG+0x28, 1111, "H")  # Synthetic retained word; no unit assignment.
        self.put(0x20000460+0x2e, 60, "H")
        self.put(0x20000460+0x30, 1, "H")
        self.put(FLAGS, 0x12340032)
        self.put(FAST_FLAGS, 0xa5, "B")
        self.uc.mem_write(SMART, b"\x01\x01")
        self.put(REPORT_CACHE+0xa1, 0x7b, "B")
        self.put(REPORT_CACHE+0xa7, 1, "B")

    def snapshot(self):
        return {"saved_watts": self.get(CONFIG+8, "H"),
                "device_timeout_minutes": self.get(CONFIG+10, "H"),
                "brightness": self.get(CONFIG+0x17, "B"),
                "fast_flag_byte": self.get(FAST_FLAGS, "B"),
                "smart_modes": list(self.uc.mem_read(SMART, 2)),
                "power_flags": f"{self.get(FLAGS):08x}"}

    def validate(self):
        before = self.low_ram()
        self.run(0x08025768)
        assert self.low_ram() == before and not self.calls and not self.events
        return self.uc.reg_read(UC_ARM_REG_R0)

    def load(self, *, corrupt_checksum=False, corrupt_header=False):
        # Use the firmware's actual file tag and checksum calculation, then
        # feed those synthetic bytes through the complete file-load function.
        self.uc.mem_write(CONFIG, bytes(self.uc.mem_read(0x080234d0, 4)))
        self.run(0x08009644, CONFIG+8, registers={UC_ARM_REG_R1: 36})
        checksum = self.uc.reg_read(UC_ARM_REG_R0)
        assert checksum <= 255
        self.put(CONFIG+4, checksum ^ int(corrupt_checksum), "B")
        if corrupt_header:
            self.put(CONFIG, self.get(CONFIG, "B") ^ 1, "B")
        self.file = bytes(self.uc.mem_read(CONFIG, 44))
        before = self.low_ram()
        original = self.snapshot()
        preserved = {offset: bytes(self.uc.mem_read(CONFIG+offset, length))
                     for offset, length in ((0x1d, 1), (0x1e, 1), (0x20, 4), (0x28, 2))}
        self.run(0x08023410)
        defaulted = "default_initializer_reached" in self.decisions
        updates = {}
        if defaulted:
            expected = bytearray(self.uc.mem_read(TEMPLATE, 36))
            struct.pack_into("<H", expected, 0, 1000 if self.get(VARIANT, "B") == 0 else 750)
            expected[0x18-8] = 60
            expected[0x1c-8] = 1
            for offset, value in preserved.items():
                expected[offset-8:offset-8+len(value)] = value
            updates = {CONFIG+8: bytes(expected), FAST_FLAGS: bytes((0xa1,)),
                       SMART: b"\x02\x02", REPORT_CACHE+0xa1: b"\x00"}
            assert self.events == [{"event_id": 0, "argument": 0}]
        else:
            assert not self.events
        changed_as(before, self.low_ram(), updates)
        assert self.get(FLAGS) == 0x12340032
        assert not self.replies
        return {"before": original, "after": self.snapshot(),
                "defaulted": defaulted, "decisions": self.decisions.copy(),
                "events": self.events.copy(), "captured_calls": self.calls.copy(),
                "all_low_ram_writes_match_expected": True,
                "synchronous_power_flags_unchanged": True}


def run_suite(image):
    if not __debug__:
        raise RuntimeError("Assertions are required; do not use Python -O")
    m = LoadMachine(image)
    result = {"firmware_sha256": IMAGE_SHA256,
              "scope": "Original A1761 main1.5.9; synthetic files/RAM, no installed1.7.1 validation",
              "validator": [], "valid_file_load": [], "bad_checksum": [],
              "bad_header": [], "handler_then_load": []}
    for variant in (0, 1, 2):
        maximum = 1000 if variant == 0 else 750
        for watts in VALUES:
            m.seed(variant, watts)
            invalid = m.validate()
            assert invalid == int(not 200 <= watts <= maximum)
            result["validator"].append({"synthetic_variant": variant,
                "saved_watts": watts, "invalid": bool(invalid), "no_ram_changes": True})
            row = m.load()
            assert row["defaulted"] == bool(invalid)
            assert row["decisions"] == (["settings_validation_reached",
                "settings_or_marker_rejected", "default_initializer_reached"] if invalid
                else ["settings_validation_reached"])
            if not invalid:
                assert row["before"] == row["after"]
            else:
                assert row["after"]["device_timeout_minutes"] == 720
                assert row["after"]["saved_watts"] == maximum
            result["valid_file_load"].append({"synthetic_variant": variant, **row})

        for watts in (100, 200, 750):
            m.seed(variant, watts)
            row = m.load(corrupt_checksum=True)
            assert row["defaulted"] and row["decisions"] == [
                "header_or_checksum_rejected", "default_initializer_reached"]
            result["bad_checksum"].append({"synthetic_variant": variant, **row})

        m.seed(variant, 200)
        row = m.load(corrupt_header=True)
        assert row["defaulted"] and row["decisions"] == [
            "header_or_checksum_rejected", "default_initializer_reached"]
        result["bad_header"].append({"synthetic_variant": variant, **row})

        for watts in (100, 200):
            m.seed(variant, maximum)
            m.put(PAYLOAD+6, watts, "H")
            before = m.low_ram()
            m.run(0x0800b908, CTX)
            changed_as(before, m.low_ram(), {CONFIG+8: struct.pack("<H", watts)})
            assert m.calls == ["deferred_configuration_persistence", "command_acknowledgement"]
            m.run(0x08017500)
            assert m.uc.reg_read(UC_ARM_REG_R0) == watts
            m.calls.clear()
            row = m.load()
            assert row["defaulted"] == (watts == 100)
            result["handler_then_load"].append({"synthetic_variant": variant,
                "app_handler_accepted_watts": watts, "raw_getter_matched_write": True, **row})
    result["cases"] = sum(len(value) for value in result.values() if isinstance(value, list))
    assert result["cases"] == 108
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    if args.manifest:
        directory = Path(__file__).resolve().parent
        files = [Path(__file__), directory/"emulate_c1000_original_commands.py",
                 directory/"requirements.txt"]
        args.manifest.write_text(json.dumps({"firmware_sha256": IMAGE_SHA256,
            "cases": result["cases"], "python": platform.python_version(),
            "unicorn": unicorn.__version__, "sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
            "result_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            "substitutes": ["synthetic file open/read/close", "persistence scheduling",
                            "configuration and Smart file persistence", "event delivery", "logger",
                            "ACK helper only in handler-to-load cases"],
            "not_executed": ["radio", "BLE", "MQTT", "physical inputs or outputs", "DSP",
                             "flash", "whole boot/event loop", "installed main1.7.1"]}, indent=2)+"\n")
    print(f"Passed {result['cases']} synthetic original-settings-load cases")


if __name__ == "__main__":
    main()
