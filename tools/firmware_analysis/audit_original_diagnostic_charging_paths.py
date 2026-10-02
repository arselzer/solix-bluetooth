#!/usr/bin/env python3
"""Offline A1761 1.5.9 diagnostic dispatch and selected radio staging audit.

The actual diagnostic parser/dispatcher selects table entries; their bodies
are substituted for that routing-only check. Selected staging handlers and
the actual module-command poller/builders then run separately with synthetic
RAM. No live transport, persistent storage, GPIO, DSP or physical output.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct

from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
)

from emulate_c1000_original_commands import (
    IMAGE, IMAGE_SHA256, Machine, PAYLOAD, changed_as,
)
from emulate_original_f0_tunnel import TunnelMachine, outer_packet

MODULE, NETWORK, TICKS = 0x20000c6c, 0x20000054, 0x20000704
TABLES = ((0, 0x08029b30, 40), (1, 0x08029c70, 8))
GATES = {0x080241d8: "AC charging gate", 0x08024494: "second charging gate"}
PROTECTED = ((0x20000419, 0x20), (0x20000498, 0x20), (0x200004fc, 4),
             (0x200005d4, 0x70), (0x20000690, 1), (0x20002038, 44),
             (0x200020c4, 1), (0x20000d10, 1),
             (0x2000001c, 0x20), (0x20000000, 0x10))


def protected(machine: Machine) -> list[bytes]:
    return [bytes(machine.uc.mem_read(address, count))
            for address, count in PROTECTED]


class RoutingMachine(TunnelMachine):
    def __init__(self, image: bytes, targets: set[int]):
        self.targets = targets
        self.audit_dispatch = False
        self.routed: list[tuple[int, int, int]] = []
        super().__init__(image)

    def step(self, uc, address, size, data):
        if self.audit_dispatch and address in self.targets:
            pointer = uc.reg_read(UC_ARM_REG_R0)
            frame = bytes(uc.mem_read(pointer, 7))
            self.routed.append((address, frame[4], struct.unpack_from("<H", frame, 5)[0]))
            self.back(0)
        else:
            super().step(uc, address, size, data)

    def dispatch(self, selector: int, opcode: int):
        self.setup()
        self.routed.clear()
        self.put(0x200004fc, 0x12340032)
        before = protected(self)
        raw = self.diagnostic(selector=selector, opcode=opcode)
        # Synthetic outer request, following the previously replayed tunnel.
        body = b"\xa1\x01\x22\xa2" + struct.pack("<H", len(raw)) + raw
        self.receive(outer_packet(body))
        self.audit_dispatch = True
        try:
            self.run(0x080276bc, count=100000)
        finally:
            self.audit_dispatch = False
        assert protected(self) == before
        assert not self.sent and not self.calls and not self.events and not self.replies
        assert self.gpio_reads == 0
        return self.routed


class StageMachine(Machine):
    def __init__(self, image: bytes):
        self.transports: list[dict] = []
        self.diagnostic_replies: list[dict] = []
        self.events: list[list[int]] = []
        super().__init__(image)

    def step(self, uc, address, size, data):
        if address == 0x08012b38:
            pointer, count = uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)
            self.transports.append({"function": f"{uc.reg_read(UC_ARM_REG_R0):02x}",
                                    "command": f"{uc.reg_read(UC_ARM_REG_R1):04x}",
                                    "payload": bytes(uc.mem_read(pointer, count)).hex()})
            self.back(0)
        elif address == 0x0800d6b0:
            pointer, count = uc.reg_read(UC_ARM_REG_R3), uc.reg_read(UC_ARM_REG_R2)
            self.diagnostic_replies.append({"selector": uc.reg_read(UC_ARM_REG_R0),
                                           "property": f"{uc.reg_read(UC_ARM_REG_R1):04x}",
                                           "payload": bytes(uc.mem_read(pointer, count)).hex()})
            self.back(0)
        elif address == 0x08010278:
            self.events.append([uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)])
            self.back(0)
        elif address in GATES:
            raise AssertionError(f"Unexpected charging-gate call {address:08x}")
        else:
            super().step(uc, address, size, data)

    def seed(self, opcode: int, data: bytes):
        self.reset()
        self.transports, self.diagnostic_replies, self.events = [], [], []
        self.uc.mem_write(MODULE, bytes(0xc8))
        self.uc.mem_write(0x200004fc, struct.pack("<I", 0x12340032))
        self.uc.mem_write(TICKS, struct.pack("<I", 123456))
        # Header without its CRC/trailer: selected handler entry only.
        self.uc.mem_write(PAYLOAD, b"\xee\0" + struct.pack("<H", len(data) + 8)
                          + b"\0" + struct.pack("<H", opcode) + data)


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    base = Machine(image)
    tables = [{"selector": selector, "address": f"{address:08x}",
               "entries": base.table(address, count)}
              for selector, address, count in TABLES]
    assert bytes(base.uc.mem_read(0x08027724, 8)) == struct.pack("<II", TABLES[1][1], TABLES[0][1])
    targets = {int(row["handler"], 16) for table in tables for row in table["entries"]}
    assert len(targets) == 48 and not targets.intersection(GATES)
    routing = RoutingMachine(image, targets)
    rows, counts = [], Counter()

    def record(group, data):
        rows.append([group, data])
        counts[group] += 1

    for table in tables:
        selector = table["selector"]
        for entry in table["entries"]:
            opcode, handler = int(entry["opcode"], 16), int(entry["handler"], 16)
            assert routing.dispatch(selector, opcode) == [(handler, selector, opcode)]
            record("actual_table_dispatch", {"selector": selector, **entry})
            assert routing.dispatch(1 - selector, opcode) == []
            record("other_selector_rejected", {"selector": 1 - selector, "opcode": entry["opcode"]})
    for selector, opcode in itertools.product((0, 1), (0, 0xffff)):
        assert routing.dispatch(selector, opcode) == []
        record("unknown_property_rejected", {"selector": selector, "opcode": f"{opcode:04x}"})
    for selector in (2, 0x20, 0x21, 0x22, 0xff):
        assert routing.dispatch(selector, 0xf0) == []
        record("other_selector_rejected", {"selector": selector, "opcode": "00f0"})
    record("no_direct_gate_entries", {f"{address:08x}": name for address, name in GATES.items()})

    # Execute selected staged handlers. No guessed body is sent to hardware.
    # Index values are exact old-MCU cache members, not user-facing mode names.
    stages = (
        (0xd2, 0x080248e6, 9, "0020", True),
        (0xd5, 0x0802516c, 12, "0023", True),
        (0xf4, 0x0800feb4, 16, "0038", True),
        (0xd4, 0x08017974, 11, "0022", False),
        (0xec, 0x08026f24, 8, "0030", False),
    )
    stage = StageMachine(image)
    staging_rows = []
    for opcode, handler, state, command, parameter in stages:
        for value in ((0, 1, 255) if parameter else (0,)):
            stage.seed(opcode, bytes((value,)))
            before, safety = stage.low_ram(), protected(stage)
            stage.run(handler, PAYLOAD)
            changes = {MODULE + 0x9d: bytes((state,))}
            if parameter:
                changes[MODULE + 0x16] = bytes((value,))
            changed_as(before, stage.low_ram(), changes)
            assert protected(stage) == safety
            expected_reply = ([{"selector": 0, "property": "00ec", "payload": "00"}]
                              if opcode == 0xec else [])
            assert stage.diagnostic_replies == expected_reply
            assert not stage.transports and not stage.events and not stage.calls
            record("actual_staging_handler", {"property": f"{opcode:04x}", "value": value,
                                              "state": state, "no_synchronous_power_change": True})
            before = stage.low_ram()
            stage.run(0x08011374, count=100000)
            updates = {MODULE + 0x9d: b"\x12", NETWORK + 4: bytes((state,)),
                       NETWORK + 0x64: struct.pack("<I", 124456)}
            if opcode == 0xd2:
                updates[NETWORK + 0xf] = bytes((int(bool(value)),))
            changed_as(before, stage.low_ram(), updates)
            assert protected(stage) == safety and not stage.events and not stage.calls
            expected_payload = (bytes((0xa1, 1, value)).hex() if parameter
                                else ("a10101a20100" if opcode == 0xec else ""))
            expected_transport = {"function": "10", "command": command,
                                  "payload": expected_payload}
            assert stage.transports == [expected_transport]
            result = {"diagnostic_property": f"{opcode:04x}", "value": value,
                      "staged_state": state, "module_request": expected_transport,
                      "next_state": 18, "no_synchronous_power_change": True}
            staging_rows.append(result)
            record("actual_module_poller_and_builder", result)

    # D6 needs a separate timing boundary: the first poll must leave its staged
    # request unchanged. The later three-second/event/radio path is NOT run.
    for value in (0, 1, 255):
        stage.seed(0xd6, bytes((value,)))
        before, safety = stage.low_ram(), protected(stage)
        stage.run(0x0802436a, PAYLOAD)
        changed_as(before, stage.low_ram(), {MODULE + 0x9d: b"\x0d",
                                          MODULE + 0x16: bytes((value,)),
                                          MODULE + 4: struct.pack("<I", 123456)})
        before = stage.low_ram()
        stage.run(0x08011374)
        changed_as(before, stage.low_ram(), {})
        assert protected(stage) == safety
        assert not stage.transports and not stage.events and not stage.calls
        record("delayed_d6_first_poll_only", {"value": value, "state": 13,
                                             "later_event_and_radio_effects_not_executed": True})

    assert sum(counts.values()) == 131
    return {"model": "A1761 original C1000", "main_version": "1.5.9",
            "input_sha256": IMAGE_SHA256, "tables": tables,
            "dispatcher": "080276bc; selector0 count40, selector1 count8",
            "cases": dict(counts) | {"total": len(rows)},
            "cases_sha256": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest(),
            "selected_module_stages": staging_rows,
            "substitutes": {"48 selected diagnostic handler bodies": "routing-only sentinel capture",
                            "08012b38": "capture MCU-to-radio transport, no send",
                            "0800d6b0": "capture selected diagnostic reply",
                            "08010278": "event capture; selected cases assert none occurred"},
            "limits": ["Available main1.5.9 differs from installed main1.7.1; original radio image unavailable.",
                       "Table absence excludes direct registration only, not all indirect paths.",
                       "Only five selected radio stages and D6's initial timing wait run their actual handler bodies.",
                       "No general factory enumeration/control is recommended; previous1.7.1 F0 attempt lost control.",
                       "Synchronous protected RAM is checked; radio ACK/retry, later rules/DSP/relay effects are absent.",
                       "No charging-converter stop or bypass-disable control is established."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    sources = [Path(__file__), Path(__file__).with_name("emulate_original_f0_tunnel.py"),
               Path(__file__).with_name("emulate_c1000_smart_policy.py"),
               Path(__file__).with_name("emulate_c1000_original_commands.py")]
    manifest = {"firmware_sha256": IMAGE_SHA256,
                "sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
                "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "cases": result["cases"]["total"]}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{result['cases']['total']} offline original diagnostic audit cases passed")


if __name__ == "__main__":
    main()
