"""Offline A1763 factory USB stream, real FIFOs/parser and packetization.

Only synthetic USB completions and reviewed model/version getters are supplied.
Real USB peripheral, interrupts, enumeration and physical sockets are excluded.
The inherited getter harness substitutes memory primitives and logging; USB
prepare-receive/send boundaries and parser timer installation are recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

import unicorn
from unicorn import arm_const as A

from emulate_gen2_diagnostic_getters import BASE, SHA256, SETTINGS, STACK, STOP, Machine, inner

RX, TX = 0x20006B70, 0x20006B80
BUSY, USB_BUFFER = 0x20000968, 0x20007A5C
DEVICE, CLASS, CONTROL = 0x20018000, 0x20018200, 0x20018400


class UsbMachine(Machine):
    def __init__(self, image: bytes, *, timer: bool = False, send_result: int = 0):
        self.rearmed = []
        self.usb_sent = []
        self.timer_registrations = []
        self.send_result = send_result
        super().__init__(image, 0, timer)
        self.uc.mem_write(DEVICE + 0xB4, struct.pack("<I", CLASS))
        self.uc.mem_write(DEVICE + 0xAC, struct.pack("<I", CONTROL))
        self.uc.mem_write(CONTROL, b"\xff")
        self.call(0x0800F55C)
        assert struct.unpack("<4I", self.read(RX, 16)) == (0x464, 0x20006B90, 0x20006B90, 0x20006B90)
        assert struct.unpack("<4I", self.read(TX, 16)) == (0x164, 0x20006FF4, 0x20006FF4, 0x20006FF4)
        self.saved = self.read(SETTINGS, 0x190)
        self.outputs = self.read(0x20000164, 8)
        self.guard = True

    def write_hook(self, uc, access, address, size, value, data):
        if self.guard and any(lo <= address < address + size <= hi for lo, hi in (
            (RX, 0x20007158), (USB_BUFFER, USB_BUFFER + 64), (BUSY, BUSY + 1),
            (DEVICE, CONTROL + 1), (0x200001CF, 0x200001D0),
        )):
            self.writes.update(range(address, address + size))
            return
        super().write_hook(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3))
        if address == 0x0802F8DE:
            assert (r0, r1, r2, r3) == (DEVICE, 3, CLASS + 3, 64)
            self.rearmed.append({"endpoint": 3, "length": 64})
            self.back(0)
        elif address == 0x0802F988:
            assert r0 == DEVICE and r1 in (1, 0x81) and r3 <= 64
            if r1 == 1:
                assert r2 == r3 == 0  # Actual DataIn zero-length branch masks direction.
            body = self.read(r2, r3) if r3 else b""
            self.usb_sent.append(body)
            # Synthetic completion bookkeeping: max packet and transfer size.
            ep = DEVICE + 0x48 + 12
            uc.mem_write(ep, struct.pack("<H", 64))
            uc.mem_write(ep + 10, struct.pack("<H", r3))
            self.back(self.send_result)
        elif address == 0x080107F4:
            assert r0 == 10 and r1 == 0x0802F6AD and r2 == 0 and r3 == 0x200001CF
            self.timer_registrations.append({"period_argument": r0, "callback": f"{r1:08x}"})
            uc.mem_write(r3, b"\x02")
            self.back(0)
        elif address == 0x0801095C:
            assert r0 == 2
            self.back(0)
        elif any(lo <= address < hi for lo, hi in (
            (0x0800F364, 0x0800F454), (0x0800F55C, 0x0800F57E),
            (0x080115D4, 0x08011638), (0x08011640, 0x08011660),
            (0x08014B38, 0x08014B6A), (0x08014B70, 0x08014BC0),
            (0x08014BC8, 0x08014C02), (0x08014D24, 0x08014D4E),
        )):
            return
        else:
            super().step(uc, address, size, data)

    def call(self, address: int, *arguments: int):
        self.stopped = False
        for register, value in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3), arguments):
            self.uc.reg_write(register, value)
        self.uc.reg_write(A.UC_ARM_REG_SP, STACK)
        self.uc.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=200000)
        assert self.stopped, "Instruction budget exceeded"
        if self.guard:
            assert self.read(SETTINGS, 0x190) == self.saved
            assert self.read(0x20000164, 8) == self.outputs
        return self.uc.reg_read(A.UC_ARM_REG_R0)

    def receive(self, body: bytes, endpoint: int = 3):
        assert len(body) <= 64
        self.uc.mem_write(CLASS + 3, body)
        self.uc.mem_write(DEVICE + 0x22 + 12 * endpoint, struct.pack("<H", len(body)))
        self.call(0x08014BC8, DEVICE, endpoint)

    def parse(self):
        self.call(0x0802F6AC)

    def count(self, ring: int):
        return self.call(0x0800F3A0, ring)

    def drain(self):
        self.call(0x08014D24, DEVICE)
        for _ in range(32):
            if not self.read(BUSY, 1)[0]:
                return b"".join(self.usb_sent)
            self.call(0x08014B70, DEVICE, 1)
        raise AssertionError("USB completion budget exceeded")


def suite(image: bytes):
    rows = []
    startup = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    startup.mem_map(0x08000000, 0x40000)
    startup.mem_write(BASE, image)
    startup.mem_map(0x20000000, 0x20000)
    startup.reg_write(A.UC_ARM_REG_SP, STACK)
    startup.emu_start(0x08005A8D, 0x08005AA4, count=200000)
    assert startup.reg_read(A.UC_ARM_REG_PC) == 0x08005AA4
    callbacks = struct.unpack("<7I", startup.mem_read(0x200009B0, 28))
    assert callbacks == (0x08014C31, 0x08014C03, 0x08014CAD, 0, 0x08014B39, 0x08014B71, 0x08014BC9)
    descriptor = bytes(startup.mem_read(0x200009CC, 18))
    assert descriptor.hex() == "1201000202000040e9288a01000101020301"
    rows.append({"case": "initialized_usb_descriptors", "class_callbacks": [f"{value:08x}" for value in callbacks],
                 "device_descriptor_hex": descriptor.hex(), "vendor_id": "28e9", "product_id": "018a",
                 "usb_class": 2, "physical_enumeration_tested": False})
    for property_id, expected in ((1, b"A1763".ljust(16, b"\0")), (2, b"1.1.4.9".ljust(16, b"\0"))):
        for fragment in (1, 7, 64):
            for timer in (False, True):
                machine = UsbMachine(image, timer=timer)
                machine.call(0x08011640)
                assert machine.timer_registrations == [{"period_argument": 10, "callback": "0802f6ad"}]
                request = inner(property_id)
                for pos in range(0, len(request), fragment):
                    machine.receive(request[pos:pos + fragment])
                    machine.parse()
                assert machine.count(RX) == 0
                reply = machine.drain()
                assert reply == inner(property_id, expected)
                assert machine.count(TX) == 0
                rows.append({"case": "fragmented_getter", "property": property_id, "fragment_size": fragment,
                             "upgrade_timer_allocated": timer, "reply_hex": reply.hex(),
                             "out_endpoint": 3, "in_endpoint": 129,
                             "rearms": len(machine.rearmed), "settings_and_outputs_preserved": True})
    for endpoint in (1, 2):
        machine = UsbMachine(image)
        machine.receive(inner(1), endpoint)
        machine.parse()
        assert machine.count(RX) == machine.count(TX) == 0
        assert not machine.rearmed and not machine.usb_sent
        rows.append({"case": "other_out_endpoint", "endpoint": endpoint, "diagnostic_bytes_received": 0})
    machine = UsbMachine(image)
    machine.receive(b"junk" + inner(1))
    machine.parse()
    assert machine.drain() == inner(1, b"A1763".ljust(16, b"\0"))
    rows.append({"case": "leading_junk", "discarded_prefix_bytes": 4, "getter_reply_correct": True})
    machine = UsbMachine(image)
    machine.receive(inner(1) + inner(2))
    machine.parse()
    assert machine.count(RX) == 0
    assert machine.drain() == inner(1, b"A1763".ljust(16, b"\0"))
    rows.append({"case": "concatenated_requests", "received_frames": 2, "reply_frames": 1,
                 "remaining_rx_bytes": 0, "second_request_discarded": True})
    machine = UsbMachine(image)
    for prop, expected in ((1, b"A1763"), (2, b"1.1.4.9")):
        machine.receive(inner(prop))
        machine.parse()
    assert machine.drain() == inner(1, b"A1763".ljust(16, b"\0")) + inner(2, b"1.1.4.9".ljust(16, b"\0"))
    rows.append({"case": "serialized_requests", "reply_frames": 2, "reply_stream_bytes": 54})
    for send_result in (0, 1):
        machine = UsbMachine(image, send_result=send_result)
        expected = inner(1, b"A1763".ljust(16, b"\0"))
        for _ in range(15):
            machine.receive(inner(1))
            machine.parse()
        assert machine.count(TX) == 355
        reply = machine.drain()
        assert reply == (expected * 15)[:355]
        assert [len(body) for body in machine.usb_sent] == [64, 0, 64, 0, 64, 0, 64, 0, 64, 0, 35]
        rows.append({"case": "tx_overflow", "usb_send_boundary_result": send_result,
                     "generated_reply_bytes": 405, "retained_reply_bytes": 355,
                     "complete_frames_retained": 13, "partial_frame_bytes": 4,
                     "usb_packet_lengths": [len(body) for body in machine.usb_sent],
                     "serializer_checks_fifo_return": False, "settings_and_outputs_preserved": True})
    machine = UsbMachine(image, send_result=1)
    machine.receive(inner(1))
    machine.parse()
    machine.call(0x08014D24, DEVICE)
    assert machine.read(BUSY, 1) == b"\x01" and machine.count(TX) == 0
    machine.receive(inner(2))
    machine.parse()
    for _ in range(10):
        machine.call(0x08014D24, DEVICE)
    assert machine.read(BUSY, 1) == b"\x01" and machine.count(TX) == 27
    assert len(machine.usb_sent) == 1
    rows.append({"case": "nonzero_send_return_without_completion", "usb_send_result": 1,
                 "subsequent_pump_calls": 10, "send_boundary_calls": 1,
                 "busy_flag_retained": True, "next_reply_bytes_waiting": 27,
                 "physical_send_success_not_inferred": True})
    machine = UsbMachine(image)
    machine.receive(inner(1)[:9])
    for _ in range(4):
        machine.parse()
        assert machine.count(RX) == 9
    machine.parse()
    assert machine.count(RX) == machine.count(TX) == 0
    rows.append({"case": "incomplete_input_stops_growing", "bytes": 9, "polls_until_discard": 5,
                 "getter_called": False, "wall_clock_time_not_measured": True})
    machine = UsbMachine(image)
    for _ in range(18):
        machine.receive(b"X" * 64)
    assert machine.count(RX) == 1123 and len(machine.rearmed) == 18
    rows.append({"case": "rx_overflow", "received_bytes": 1152, "retained_bytes": 1123,
                 "receive_rearmed_count": 18, "rx_enqueue_return_ignored": True,
                 "parser_executed": False})
    return {"model": "A1763 main 1.1.4.9", "firmware_sha256": SHA256, "cases": len(rows),
            "hardware_access": False, "results": rows,
            "limits": __doc__.strip()}


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
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    names = (Path(__file__).name, "emulate_gen2_diagnostic_getters.py")
    args.manifest.write_text(json.dumps({"cases": result["cases"], "firmware_sha256": SHA256,
        "result_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "unicorn_version": unicorn.__version__, "hardware_access": False,
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in names}}, indent=2) + "\n")
    args.output.chmod(0o600)
    args.manifest.chmod(0o600)
    print(json.dumps({"cases": result["cases"], "hardware_access": False}))


if __name__ == "__main__":
    main()
