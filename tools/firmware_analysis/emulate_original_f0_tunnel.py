#!/usr/bin/env python3
"""Offline original C1000 1.5.9 diagnostic F0 request and response traversal.

Executes the public MCU receiver, dispatcher, diagnostic parser/getter, ring
buffers, serializers, software timers and output scheduler. Synthetic GPIO,
RAM and allocation; queue handoff, logging and final port send are captured.
No device, network, persistent storage or physical output access.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R7,
)

from emulate_c1000_original_commands import CTX, IMAGE, IMAGE_SHA256, PAYLOAD, RAM
from emulate_c1000_smart_policy import PolicyMachine, TICK, TIMERS

RX, TX = 0x20004654, 0x20004664
RX_DATA, TX_DATA = 0x20019000, 0x20019400
HEAP = 0x20019800
RESET_TIMER = 0x200004f0
POLL_TIMER = 0x20000077
DISPATCH_HEAD = 0x2000699c
LOGGER, PORT0, PORT1 = 0x08005020, 0x08005024, 0x08005028
ENCRYPTION_FLAGS = 0x200009d4
INTERESTING = {
    0x08022684: "outer_receiver", 0x08022284: "outer_parser",
    0x08022584: "function_dispatch", 0x080160e8: "function_0c_dispatch",
    0x0800aaa4: "diagnostic_tunnel", 0x080276bc: "diagnostic_dispatch",
    0x0800d888: "diagnostic_parser", 0x0801255e: "F0_getter",
    0x08007fb8: "GPIO_reader", 0x0800d6b0: "diagnostic_serializer",
    0x08012de4: "reply_poller", 0x08016134: "tunnel_serializer",
    0x08012b38: "outer_serializer", 0x08022634: "dispatch_enqueue_wrapper",
    0x08015020: "dispatch_enqueue", 0x08023fc0: "dispatch_scheduler",
    0x080220f4: "port_send", 0x080253b4: "stop_upgrade_reset_timer",
}


def outer_packet(body: bytes, *, function: int = 0x0c, command: int = 0,
                 destination: int = 1) -> bytes:
    packet = bytearray(b"\xff\x09" + struct.pack("<H", len(body) + 10)
                       + bytes((3, destination, function)) + struct.pack(">H", command) + body)
    checksum = 0
    for value in packet:
        checksum ^= value
    return bytes(packet + bytes((checksum,)))


class TunnelMachine(PolicyMachine):
    def __init__(self, image: bytes):
        self.guard = False
        self.visited: set[str] = set()
        self.queued: list[bytes] = []
        self.sent: list[tuple[int, bytes]] = []
        self.heap_next = HEAP
        self.freed: list[int] = []
        self.gpio_reads = 0
        super().__init__(image)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.read)

    def read(self, uc, access, address, size, value, data):
        if self.guard and address == 0x40011408:
            self.gpio_reads += 1

    def write(self, uc, access, address, size, value, data):
        if self.guard:
            # Complete allowlist of firmware writes for the selected getter.
            # Excludes settings, output flags, Smart counters, DSP/BMS, GPIO,
            # flash and all hardware control registers.
            allowed = (
                (0x20000057, 0x20000058),   # Normal command activity marker.
                (0x20000075, 0x20000078),   # Reply poller bookkeeping.
                (0x200006b0, 0x200006b8),   # Diagnostic RX parser history.
                (0x20000a53, 0x20000a54),   # Outgoing dispatch sequence.
                (0x20000d34, 0x20000d66),   # Diagnostic reply scratch.
                (0x200025a4, 0x200029c3),   # Diagnostic parser/serializer buffer.
                (RX, TX + 16),             # Ring read/write pointers.
                (TIMERS + 20, TIMERS + 60),  # Query poller and upgrade reset timer.
                (0x2000658c, 0x2000698c),   # Outer parser payload scratch.
                (DISPATCH_HEAD, DISPATCH_HEAD + 10),
                (CTX, CTX + 0x100),        # Synthetic outer request context.
                (RX_DATA, TX_DATA + 0x400),
                (HEAP, HEAP + 0x400),      # Synthetic packet/dispatch allocation.
                (0x2001e000, 0x2001f000),   # Execution stack.
            )
            if not any(lo <= address < address + size <= hi for lo, hi in allowed):
                raise AssertionError(f"Unexpected getter write {address:08x} "
                                     f"at {uc.reg_read(UC_ARM_REG_PC):08x}")
        # For this suite even the parent's permitted SysTick writes are forbidden.
        if not RAM <= address < address + size <= RAM + 0x20000:
            raise AssertionError(f"Peripheral/flash write {address:08x}")
        super().write(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        if self.guard and address in INTERESTING:
            self.visited.add(INTERESTING[address])
        if address in (0x08007718, 0x0801e7d4, 0x080251f0, 0x080225c4,
                       0x08010278, 0x0800d2c4):
            raise AssertionError(f"Unexpected ACK/persistence/event/reset {address:08x}")
        if address == LOGGER:
            self.back(0)
        elif address == 0x08012b00:
            count = uc.reg_read(UC_ARM_REG_R0)
            pointer = self.heap_next
            self.heap_next += (count + 3) & ~3
            assert self.heap_next < HEAP + 0x400
            self.back(pointer)
        elif address == 0x08012ae8:
            self.freed.append(uc.reg_read(UC_ARM_REG_R0))
            self.back(0)
        elif address == 0x0801c3b4:
            pointer = uc.reg_read(UC_ARM_REG_R1)
            assert uc.reg_read(UC_ARM_REG_R0) == 0x0802b15c
            self.queued.append(bytes(uc.mem_read(pointer, 0x204)))
            self.back(0)
        elif address in (PORT0, PORT1):
            pointer, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1))
            self.sent.append((int(address == PORT1), bytes(uc.mem_read(pointer, count))))
            self.back(count)
        else:
            super().step(uc, address, size, data)

    def setup(self, *, gpio: int = 0, route_active: int = 0, route_flags: int = 0,
              encrypted: bool = False, reset_timer_active: bool = False):
        self.guard = False
        self.reset()
        self.visited.clear()
        self.queued.clear()
        self.sent.clear()
        self.freed.clear()
        self.heap_next = HEAP
        self.gpio_reads = 0
        self.put(TIMERS, 1, "B")  # Reserve timer zero, as startup does.
        self.put(TICK, 123456)
        for ring, buffer in ((RX, RX_DATA), (TX, TX_DATA)):
            self.run(0x0800ec58, ring, registers={UC_ARM_REG_R1: buffer,
                                               UC_ARM_REG_R2: 0x400})
        self.run(0x08022d6c)  # Actual registration of functions10,0f,0c.
        assert self.get(0x2000647c + 0xc * 16 + 4) == 0x080160e9
        assert self.get(0x200000dc) == 0x0800aaa5
        assert bytes(self.uc.mem_read(0x08029be0, 8)) == bytes.fromhex("f00000005f250108")
        self.run(0x08023f9c)  # Actual dispatch queue initialization.
        self.put(0x200009c4, LOGGER | 1)
        for port, callback in ((0, PORT0), (1, PORT1)):
            self.run(0x08022b98, port, registers={UC_ARM_REG_R1: callback | 1})
        self.put(0x40011408, gpio)
        self.put(0x20000d13, route_active, "B")
        self.put(0x20000d30, route_flags, "B")
        self.put(ENCRYPTION_FLAGS, int(encrypted), "B")
        self.put(RESET_TIMER, 2 if reset_timer_active else 0, "B")
        if reset_timer_active:
            self.put(TIMERS + 40, 2, "B")
            self.put(TIMERS + 44, 1800000)
            self.put(TIMERS + 52, 0x08012f75)
        self.guard = True

    def diagnostic(self, *, selector: int = 0, opcode: int = 0xf0,
                   data: bytes = b"") -> bytes:
        # Build the checksum using the actual firmware CRC helper.
        frame = bytearray(b"\xee\x00" + struct.pack("<H", len(data) + 8)
                          + bytes((selector,)) + struct.pack("<H", opcode) + data)
        self.uc.mem_write(PAYLOAD, bytes(frame[1:]))
        self.run(0x08008d4c, PAYLOAD, registers={UC_ARM_REG_R1: len(frame) - 1})
        encoded = struct.pack("<H", self.uc.reg_read(UC_ARM_REG_R0))
        # Independent CRC-16/MODBUS check; this framing writes the conventional
        # numeric CRC most significant byte first. The MCU lookup helper
        # returns a byte-swapped value, then stores it little-endian.
        crc = 0xffff
        for byte in frame[1:]:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0xa001 if crc & 1 else crc >> 1
        assert encoded == struct.pack(">H", crc)
        return bytes(frame + encoded + b"\xfc\x55")

    def receive(self, packet: bytes, *, incoming_port: int = 0):
        self.uc.mem_write(PAYLOAD, packet)
        self.run(0x08022684, incoming_port,
                 registers={UC_ARM_REG_R1: PAYLOAD, UC_ARM_REG_R2: len(packet)},
                 count=100000)
        if not self.queued:
            return
        queued = self.queued[-1]
        assert queued[0] == incoming_port
        length = struct.unpack_from("<H", queued, 2)[0]
        assert queued[4:4 + length] == packet
        # The real task-queue handoff is substituted, then its decoded packet
        # enters the actual next-stage parser with a synthetic library context.
        self.put(CTX, incoming_port, "B")
        self.put(CTX + 12, PAYLOAD)
        self.run(0x08022284, CTX, registers={UC_ARM_REG_R1: length}, count=100000)

    def finish(self):
        self.run(0x080276bc, count=100000)
        timer_id = self.get(POLL_TIMER, "B")
        assert timer_id == 1
        for _ in range(2):
            self.run(0x08012de4, timer_id, count=100000)
        self.run(0x08023fc0, count=100000)
        assert self.get(DISPATCH_HEAD + 8, "H") == 0


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    m = TunnelMachine(image)
    results = {"valid_requests": [], "invalid_inner_requests": [],
               "invalid_outer_requests": [], "incoming_port_routing": [],
               "timer_registration": [], "outer_destinations": [],
               "command_flag_mask": [], "oversized_inner_length": []}
    selected = bytes.fromhex("ee00080000f000daa5fc55")
    routes = ((0, 0), (1, 2), (2, 3))
    for source, gpio, route, encrypted, active in itertools.product(
            (0x20, 0x21, 0x22), (0, 4), routes, (False, True), (False, True)):
        m.setup(gpio=gpio, route_active=route[0], route_flags=route[1],
                encrypted=encrypted, reset_timer_active=active)
        raw = m.diagnostic()
        assert raw == selected
        body = bytes((0xa1, 1, source, 0xa2)) + struct.pack("<H", len(raw)) + raw
        packet = outer_packet(body)
        m.receive(packet)
        assert not m.sent and m.gpio_reads == 0
        assert m.get(TX + 4) == m.get(TX + 8)
        m.finish()
        expected_raw = m.diagnostic(data=bytes((int(bool(gpio & 4)),)))
        expected_body = b"\xa1\x01\x00\xa2\x0c\x00" + expected_raw
        expected_packet = outer_packet(expected_body, command=0x4000 if encrypted else 0)
        assert m.sent == [(0, expected_packet)]
        assert m.gpio_reads == 1
        assert set(INTERESTING.values()) <= m.visited
        assert len(m.freed) == 2
        assert not m.calls and not m.events and not m.replies
        assert m.get(TIMERS + 40, "B") == (3 if active else 0)
        results["valid_requests"].append({
            "source": f"{source:02x}", "gpio_bit2": bool(gpio & 4),
            "normal_ack_route_active": route[0], "normal_ack_route_flags": route[1],
            "port0_encryption_flag": encrypted, "upgrade_reset_timer_active": active,
            "upgrade_reset_timer_stopped": active, "request": packet.hex(),
            "reply": expected_packet.hex(), "reply_port": 0,
            "gpio_reads": 1, "setter_ack_persistence_reset_events": 0})

    for selector in (1, 0x20, 0x21, 0x22):
        m.setup(reset_timer_active=True)
        raw = m.diagnostic(selector=selector)
        m.receive(outer_packet(b"\xa1\x01\x22\xa2\x0b\x00" + raw))
        m.finish()
        assert not m.sent and m.gpio_reads == 0
        assert m.get(TIMERS + 40, "B") == 3
        results["invalid_inner_requests"].append({"kind": "wrong_selector",
            "selector": selector, "reply_count": 0, "upgrade_reset_timer_stopped": True})
    for kind in ("bad_crc", "bad_tail", "bad_start", "incomplete"):
        m.setup(reset_timer_active=True)
        raw = bytearray(selected)
        if kind == "bad_crc":
            raw[7] ^= 1
        elif kind == "bad_tail":
            raw[-1] ^= 1
        elif kind == "bad_start":
            raw[0] = 0
        else:
            raw = raw[:-1]
        m.receive(outer_packet(b"\xa1\x01\x22\xa2" + struct.pack("<H", len(raw)) + raw))
        m.finish()
        assert not m.sent and m.gpio_reads == 0
        assert m.get(TIMERS + 40, "B") == 2
        results["invalid_inner_requests"].append({"kind": kind,
            "reply_count": 0, "upgrade_reset_timer_stopped": False})

    for kind in ("bad_header", "bad_length", "bad_checksum"):
        m.setup(reset_timer_active=True)
        packet = bytearray(outer_packet(b"\xa1\x01\x22\xa2\x0b\x00" + selected))
        if kind == "bad_header":
            packet[0] ^= 1
        elif kind == "bad_length":
            packet[2] ^= 1
        else:
            packet[-1] ^= 1
        m.receive(bytes(packet))
        assert not m.queued and not m.sent and m.gpio_reads == 0
        assert m.get(TIMERS + 40, "B") == 2
        results["invalid_outer_requests"].append({"kind": kind, "queued": False})

    for port in (0, 1):
        m.setup(gpio=0)
        m.receive(outer_packet(b"\xa1\x01\x20\xa2\x0b\x00" + selected), incoming_port=port)
        m.finish()
        assert len(m.sent) == 1 and m.sent[0][0] == 0
        results["incoming_port_routing"].append({"incoming_port": port,
            "outgoing_port": 0, "note": "Async diagnostic reply does not retain caller port/source"})

    for destination in (0, 1, 2):
        m.setup()
        packet = outer_packet(b"\xa1\x01\x21\xa2\x0b\x00" + selected,
                              destination=destination)
        m.receive(packet)
        m.finish()
        assert len(m.sent) == 1 and m.gpio_reads == 1
        results["outer_destinations"].append({"destination": destination,
            "request": packet.hex(), "reply_count": 1,
            "scope": "MCU accepts this header; radio forwarding is not simulated"})

    for command in (0x0000, 0x4000):
        m.setup()
        m.receive(outer_packet(b"\xa1\x01\x21\xa2\x0b\x00" + selected,
                              command=command, destination=0))
        m.finish()
        assert len(m.sent) == 1 and m.gpio_reads == 1
        results["command_flag_mask"].append({"command": f"{command:04x}",
            "clear_body": True, "reply_count": 1,
            "scope": "MCU masks command high nibble; no radio decryption simulated"})

    # Never transmit these. The tunnel trusts its length and its trailing
    # uint8 loop cannot reach256. Bound execution instead of simulating a
    # watchdog reset, which could have physical consequences on a device.
    for length in (255, 256, 300):
        m.setup()
        body = b"\xa1\x01\x21\xa2" + struct.pack("<H", length) + bytes(length)
        limited = False
        try:
            m.receive(outer_packet(body))
        except AssertionError as error:
            if not str(error).startswith("Instruction limit at "):
                raise
            pc = m.uc.reg_read(UC_ARM_REG_PC)
            assert 0x0800aadc <= pc <= 0x0800aae2
            limited = True
        assert limited == (length > 255)
        assert not m.sent and m.gpio_reads == 0 and not m.calls and not m.events
        results["oversized_inner_length"].append({"declared_and_present_length": length,
            "outer_parser_instruction_limit": 100000, "nonterminating_loop": limited,
            "loop": "0800aadc..0800aae2 uint8 increment versus uint16 length",
            "watchdog_or_reset_simulated": False})

    # Execute just the upgrade-mode reset-timer registration block. Do not
    # enter upgrade mode, run preceding setting writes, or invoke its callback.
    m.setup()
    m.guard = False
    m.run(0x08006474, stop=0x08006488, registers={
        # This reviewed block uses r7 as the timer ID output pointer.
        UC_ARM_REG_R7: RESET_TIMER})
    timer_id = m.get(RESET_TIMER, "B")
    assert timer_id == 1 and m.get(TIMERS + 24) == 1800000
    assert m.get(TIMERS + 32) == 0x08012f75
    results["timer_registration"].append({"period_ticks": 1800000,
        "callback": "08012f74", "callback_target": "0800d2c4 AIRCR system reset",
        "allocation_block": "08006474..08006484", "timer_id_pointer": "200004f0",
        "enclosing_branch_log": "upgrade mode event at080063ca",
        "callback_executed": False})
    return {"image_sha256": IMAGE_SHA256, "unicorn_version": unicorn.__version__,
        "scope": __doc__.strip(), "cases": sum(map(len, results.values())),
        "diagnostic_query": selected.hex(), "executed_stages": INTERESTING,
        "substitutes": {"08012b00": "synthetic bounded allocation", "08012ae8": "free capture",
            "0801c3b4": "task queue handoff capture", "08005020": "synthetic logger callback",
            "08005024/08005028": "synthetic final port callbacks"},
        "results": results,
        "limits": ["Public original main 1.5.9 only; installed 1.7.1 not emulated",
            "Radio BLE/MQTT forwarding and application command allowlists are unverified",
            "GPIO electrical meaning is not established",
            "Valid diagnostic frames stop an existing upgrade-mode reset timer",
            "Only F0 is selected; the general diagnostic tunnel also contains setters",
            "A malformed declared length above255 traps the MCU tunnel in a loop; never send it",
            "Scheduler callbacks are invoked explicitly; real interrupt timing is not modeled"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, default=IMAGE,
                        help="Public original1.5.9 MainMcu image; SHA-256 checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.firmware.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
