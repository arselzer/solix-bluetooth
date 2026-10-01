#!/usr/bin/env python3
"""Audit the protobuf settings candidate named SETTING_MSG_DESC.

Executes C1000 Gen 2 1.1.4.9's actual callback, session decoder, protobuf
encoder, getters and history clearing routine against synthetic RAM.
Persistence scheduling and logging are substituted; no network, station,
flash or power-policy loop runs. The isolated callback has no enclosing
report delivery or acknowledgement. This is not a complete firmware audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct


def run_suite():
    from unicorn import UC_HOOK_MEM_READ
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R2
    from emulate_energy_counters import Machine, BUFFER, DESC
    from replay_io import FIRMWARE_SHA256, firmware_image

    firmware_image()
    history, records, saved = 0x20002028, 0x20002078, 0x20001D48
    name = 0x21000A00
    pointer = name+32

    class SettingsMachine(Machine):
        def __init__(self):
            super().__init__()
            self.persistence_requests = 0
            self.backup_reads = 0
            self.submessage_results = []
            self.uc.mem_write(name, b"SETTING_MSG_DESC\0")
            self.uc.mem_write(pointer, struct.pack("<I", name))
            self.uc.hook_add(UC_HOOK_MEM_READ, self.read)

        def read(self, uc, access, address, size, value, data):
            if address < 0x20001EA9+38 and address+size > 0x20001EA9:
                self.backup_reads += 1

        def step(self, uc, address, size, data):
            if address in (0x080170D4, 0x0801717E):
                self.submessage_results.append(uc.reg_read(UC_ARM_REG_R0))
            if address == 0x08016BB0:
                return  # Execute the callback that the energy harness substitutes.
            if address == 0x08015C4C:
                self.persistence_requests += 1
                self.back()
            elif address == 0x0800D284:
                self.back()
            else:
                super().step(uc, address, size, data)

        def encode(self, capacity):
            self.uc.reg_write(UC_ARM_REG_R2, capacity)
            self.run(0x08022D4C, DESC, second=BUFFER)
            self.uc.reg_write(UC_ARM_REG_R2, pointer)
            self.run(0x08016BB0, DESC)
            count = int.from_bytes(self.uc.mem_read(DESC+12, 4), "little")
            return self.uc.reg_read(UC_ARM_REG_R0), bytes(self.uc.mem_read(BUFFER, count))

    results = []
    for case in ("valid_change", "no_changes", "truncated_session"):
        for capacity in (0, 2, 64, 1024):
            m = SettingsMachine()
            # The full saved configuration is distinct from session tracking.
            configuration = bytes((i*17+9) % 256 for i in range(0x190))
            m.uc.mem_write(saved, configuration)
            m.uc.mem_write(history+8, struct.pack("<18I", *range(101, 119)))
            m.uc.mem_write(0x20003434, struct.pack("<4I", 100, 200, 300, 400))
            # Ten synthetic 25-byte records. Only the first is marked valid.
            m.uc.mem_write(records, bytes((0xA5,))*250)
            for i in range(10):
                m.uc.mem_write(records+25*i, b"\0")
            body = bytes((1, 15 if case != "no_changes" else 0, 7, 0, 2, 3, 4, 5))
            m.uc.mem_write(records, b"\xFF")
            m.uc.mem_write(records+4, body.ljust(20, b"\0"))
            m.uc.mem_write(records+24, bytes((2 if case == "truncated_session" else 8,)))
            before = bytes(m.uc.mem_read(records, 250))
            returned, encoded = m.encode(capacity)
            after = bytes(m.uc.mem_read(records, 250))
            cleared = all(after[25*i] == 0 and after[25*i+24] == 0
                          and after[25*i+4:25*i+24] == bytes(20) for i in range(10))
            assert bytes(m.uc.mem_read(saved, 0x190)) == configuration
            assert m.backup_reads == 0
            assert m.calls == []  # No inherited energy/sensor substitutes reached.
            assert cleared == bool(m.persistence_requests)
            assert cleared or after == before
            results.append({"session": case, "output_capacity": capacity,
                            "callback_return": returned, "encoded_bytes": len(encoded),
                            "encoded_hex": encoded.hex(), "tracking_records_cleared": cleared,
                            "persistence_requests": m.persistence_requests,
                            "submessage_encoder_returns": m.submessage_results,
                            "complete_saved_configuration_preserved": True,
                            "reads_from_38_byte_backup_block": m.backup_reads})
    # This callback clears tracking even if later encoding exceeds capacity.
    assert all(row["tracking_records_cleared"] ==
               (row["output_capacity"] >= (64 if row["session"] == "valid_change" else 2))
               for row in results)
    assert any(row["tracking_records_cleared"] and
               0 in row["submessage_encoder_returns"] for row in results)
    return {"model": "C1000 Gen 2 main 1.1.4.9", "image_sha256": FIRMWARE_SHA256,
            "cases": len(results), "results": results, "limits": __doc__.strip(),
            "actual_functions": ["08016bb0", "0801701c", "080097ae", "0800ff40",
                                 "0801a010", "08022be0", "08022af0", "08022d4c"],
            "substitutions": {"08015c4c": "record persistence scheduling",
                              "0800d284": "logging"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.firmware_dir:
        os.environ["SOLIX_FIRMWARE_DIR"] = str(args.firmware_dir)
    os.environ["SOLIX_ANALYSIS_OUTPUT"] = str(args.output.resolve().parent)
    result = run_suite()
    result["tool_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(f"Passed {result['cases']} synthetic settings-report cases")


if __name__ == "__main__":
    main()
