#!/usr/bin/env python3
"""Replay function 0x0c routing in the public C1000 Gen 2 radio image.

Runs RISC-V receive validation, queue processing, payload classification,
function registration/dispatch, factory routing, framing and outer checksum.
OS allocation/queues/logging, session state and AES-GCM primitives are host
substitutes. Synthetic data only; no hardware, network or device access.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import unicorn
from unicorn import riscv_const as R


IMAGE_NAME = "c1000-radio-validated.bin"
IMAGE_SHA256 = "e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8"
SECRET = bytes(range(32))
AAD = bytes.fromhex("3322110077665544bbaa9988ffeeddcc")
STOP = 0x50000000
REGISTRY = 0x3FC8BEDC
INTERESTING = {
    0x42043A18: "mqtt_raw_callback", 0x4204D14E: "receive_validator",
    0x4204D30E: "queue_processor", 0x4204FFBE: "payload_parser",
    0x4204FEEE: "function_dispatch", 0x42043AEC: "factory_handler",
    0x42043AA8: "factory_send_wrapper", 0x420439CC: "mqtt_high_opcode_forward",
    0x4204FCAE: "send_builder", 0x4204F75C: "single_frame_builder",
    0x42051BFC: "outer_xor_checksum",
}


def packet(body: bytes, command: int = 0, function: int = 12,
           destination: int = 0) -> bytes:
    data = bytearray(b"\xff\x09" + struct.pack("<H", len(body) + 10)
                     + bytes((3, destination, function))
                     + struct.pack(">H", command) + body)
    checksum = 0
    for value in data:
        checksum ^= value
    return bytes(data + bytes((checksum,)))


def crypt(body: bytes, decrypt: bool = False) -> bytes:
    cipher = AESGCM(SECRET[:16])
    return (cipher.decrypt if decrypt else cipher.encrypt)(SECRET[16:28], body, AAD)


class Machine:
    def __init__(self, image: bytes, *, encrypted_ble_reply: bool = False):
        self.uc = unicorn.Uc(unicorn.UC_ARCH_RISCV, unicorn.UC_MODE_RISCV32)
        for address, size in ((0x3C130000, 0x40000), (0x3FC80000, 0x90000),
                              (0x40380000, 0x10000), (0x50000000, 0x1000),
                              (0x42000000, 0x200000), (0x40000000, 0x1000),
                              (0x20000000, 0x100000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(0x40000000, b"\x13\0\0\0" * 1024)
        self.uc.mem_write(STOP, b"\x13\0\0\0")
        offset = 24
        for _ in range(image[1]):
            address, size = struct.unpack_from("<II", image, offset)
            offset += 8
            self.uc.mem_write(address, image[offset:offset + size])
            offset += size
        self.heap = 0x20000000
        self.queue: list[int] = []
        self.egress: list[dict] = []
        self.builders: list[dict] = []
        self.visited: Counter[str] = Counter()
        self.encrypted_ble_reply = encrypted_ble_reply
        self.queue_finished = False
        self.stop_at = STOP
        self.instructions = 0
        self.uc.reg_write(R.UC_RISCV_REG_SP, 0x3FD0F000)
        self.uc.reg_write(R.UC_RISCV_REG_GP, 0x3FC83000)
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write_guard)
        self.run(0x420487E4, stop_at=0x4204884E)
        self.registrations = {
            f"{function:02x}": [f"{self.u32(REGISTRY + function * 16 + 4 + n * 4):08x}"
                               for n in range(self.read(REGISTRY + function * 16 + 1, 1)[0])]
            for function in range(24)
            if self.read(REGISTRY + function * 16 + 1, 1)[0]
        }
        assert self.registrations["0c"] == ["42043aec"]

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def alloc(self, data: bytes | int) -> int:
        if isinstance(data, int):
            data = bytes(data)
        result = self.heap
        self.heap += max(16, (len(data) + 15) & ~15)
        assert self.heap < 0x20100000
        self.uc.mem_write(result, data)
        return result

    def write_guard(self, uc, _access, address, size, _value, _data):
        # Only application registration, emulated allocations and stack.
        allowed = ((REGISTRY, REGISTRY + 24 * 16),
                   (0x3FC906D0, 0x3FC906DC),  # Fragment-parser cleanup counters.
                   (0x20000000, 0x20100000), (0x3FD0D000, 0x3FD10000))
        if not any(lo <= address < address + size <= hi for lo, hi in allowed):
            raise AssertionError(f"Unexpected write {address:08x} at "
                                 f"{uc.reg_read(R.UC_RISCV_REG_PC):08x}")

    def step(self, uc, address, _size, _data):
        self.instructions += 1
        if address == self.stop_at:
            uc.emu_stop()
            return
        if address in INTERESTING:
            self.visited[INTERESTING[address]] += 1
        args = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(8)]
        a0, a1, a2, a3, a4 = args[:5]
        if address in (0x4202D3B0, 0x420232B8, 0x42023636, 0x420519F0,
                       0x421271AC, 0x421270B8, 0x42051BA0, 0x42051F96,
                       0x42051EEE, 0x42054506):
            result = 0
        elif address == 0x42051F00:  # OS mutex take succeeds.
            result = 1
        elif address == 0x42051F12:
            result = self.alloc(a0)
        elif address == 0x40000354:
            uc.mem_write(a0, bytes((a1 & 255,)) * a2)
            result = a0
        elif address == 0x40000358:
            uc.mem_write(a0, self.read(a1, a2))
            result = a0
        elif address == 0x4205200C:  # OS queue push.
            self.queue.append(a1)
            result = 1
        elif address == 0x4205201E:  # Process one queued frame, then stop loop.
            if not self.queue:
                self.queue_finished = True
                uc.emu_stop()
                return
            uc.mem_write(a1, struct.pack("<I", self.queue.pop(0)))
            result = 1
        elif address == 0x42050D04:  # Synthetic per-port session key provider.
            uc.mem_write(a0, SECRET)
            result = 0
        elif address == 0x4205103C:
            result = 1  # GCM selected by synthetic established session.
        elif address == 0x42051DA0:
            result = int(a0 == 0 and self.encrypted_ble_reply)
        elif address == 0x420541CA:
            result = 512  # All selected frames fit one synthetic transport MTU.
        elif address == 0x42050E98:
            assert self.read(a0, 32) == SECRET
            decoded = crypt(self.read(a1, a2), decrypt=True)
            uc.mem_write(a3, decoded)
            uc.mem_write(a4, struct.pack("<I", len(decoded)))
            self.visited["host_gcm_decrypt"] += 1
            result = 0
        elif address == 0x42050D88:
            assert self.read(a0, 32) == SECRET
            encoded = crypt(self.read(a3, a4))
            uc.mem_write(a1, encoded)
            uc.mem_write(a2, struct.pack("<I", len(encoded)))
            self.visited["host_gcm_encrypt"] += 1
            result = 0
        elif address == 0x42053F70:  # Capture queued frame before physical TX.
            wire = self.read(a0, a1)
            assert packet(wire[9:-1], int.from_bytes(wire[7:9], "big"),
                          wire[6], wire[5]) == wire
            self.egress.append({"port": self.u32(a2 + 4), "packet": wire.hex(),
                                "timeout": int.from_bytes(self.read(a2 + 8, 2), "little")})
            result = 0
        else:
            if address == 0x4204FCAE:
                self.builders.append({"function": a0, "command": a1,
                                      "body": self.read(a2, a3).hex(),
                                      "port": self.u32(a4 + 4)})
            allowed = ((0x420487E4, 0x4204884E), (0x420439CC, 0x42043B84),
                       (0x4204D14E, 0x4204D514), (0x4204D824, 0x4204D826),
                       (0x4204F75C, 0x4204F846), (0x4204F9A6, 0x4204FA68),
                       (0x4204FCAE, 0x42050B24), (0x42051BFC, 0x42051C24))
            if not any(lo <= address < hi for lo, hi in allowed):
                raise AssertionError(f"Unexpected instruction {address:08x}; args={args}")
            return
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def run(self, address: int, *args: int, stop_at: int = STOP):
        self.stop_at = stop_at
        self.queue_finished = False
        for index, value in enumerate(args):
            self.uc.reg_write(R.UC_RISCV_REG_A0 + index, value)
        self.uc.reg_write(R.UC_RISCV_REG_RA, STOP)
        self.uc.emu_start(address, 0, count=100000)
        pc = self.uc.reg_read(R.UC_RISCV_REG_PC)
        assert pc == stop_at or self.queue_finished, f"Instruction budget at {pc:08x}"

    def receive(self, wire: bytes, *, port: int = 0, mqtt: bool = False):
        pointer = self.alloc(wire)
        if mqtt:
            self.run(0x42043A18, pointer, len(wire))
        else:
            self.run(0x4204D14E, port, pointer, len(wire))
        if self.queue:
            self.run(0x4204D30E)


def suite(image: bytes) -> dict:
    cases = []
    # The A2 data intentionally contains an opaque synthetic diagnostic frame.
    inner = bytes.fromhex("ee00080000f000daa5fc55")
    for source, port, encrypted, ble_reply in itertools.product(
            (0, 0x20, 0x21, 0x22), (0, 1, 2, 5), (False, True), (False, True)):
        body = bytes((0xA1, 1, source, 0xA2, len(inner), 0)) + inner
        wire = packet(crypt(body) if encrypted else body, 0x4000 if encrypted else 0)
        machine = Machine(image, encrypted_ble_reply=ble_reply)
        machine.receive(wire, port=port)
        expected_port = {0: 2, 2: 0}.get(port)
        assert len(machine.builders) == int(expected_port is not None)
        assert len(machine.egress) == int(expected_port is not None)
        if expected_port is not None:
            assert machine.builders[0] == {"function": 12, "command": 0,
                                            "body": body.hex(), "port": expected_port}
            actual = bytes.fromhex(machine.egress[0]["packet"])
            expected_encrypted = expected_port == 0 and ble_reply
            assert actual == packet(crypt(body) if expected_encrypted else body,
                                    0x4000 if expected_encrypted else 0, destination=1)
        assert machine.visited["host_gcm_decrypt"] == int(encrypted)
        cases.append({"case": "port_source_encryption", "source": f"{source:02x}",
                      "port": port, "incoming_encrypted": encrypted,
                      "ble_reply_encrypted": ble_reply, "destination_port": expected_port,
                      "body_preserved": True if expected_port is not None else None})

    body = bytes((0xA1, 1, 0x21, 0xA2, len(inner), 0)) + inner
    for command in (0x0001, 0x003F, 0x0040, 0x0800, 0x4001, 0x4800):
        machine = Machine(image)
        machine.receive(packet(crypt(body) if command & 0x4000 else body, command))
        assert not machine.egress and machine.visited["factory_handler"] == 1
        cases.append({"case": "nonzero_opcode_rejected", "command": f"{command:04x}"})

    for command in (0x0000, 0x0001, 0x003F, 0x0040, 0x4000, 0x4040):
        machine = Machine(image)
        wire_body = crypt(body) if command & 0x4000 else body
        machine.receive(packet(wire_body, command), mqtt=True)
        low = command & 0x0FFF <= 0x3F
        assert machine.visited["receive_validator"] == int(low)
        assert machine.visited["factory_handler"] == int(low)
        assert len(machine.egress) == int(not low)
        if not low:
            # High command callback bypasses both factory filter and decryption.
            assert machine.builders[0] == {"function": 12, "command": command & 0x0FFF,
                                            "body": wire_body.hex(), "port": 2}
            assert not machine.visited["host_gcm_decrypt"]
        cases.append({"case": "mqtt_raw_callback", "command": f"{command:04x}",
                      "factory_handler": low, "destination_port": None if low else 2})

    for mutation in ("header", "length", "checksum"):
        wire = bytearray(packet(body))
        wire[{"header": 0, "length": 2, "checksum": len(wire) - 1}[mutation]] ^= 1
        machine = Machine(image)
        machine.receive(bytes(wire))
        assert not machine.egress and not machine.visited["payload_parser"]
        cases.append({"case": "outer_validation", "corruption": mutation, "rejected": True})

    for function in (0, 2, 11, 13, 18, 23, 24, 255):
        machine = Machine(image)
        machine.receive(packet(body, function=function))
        assert not machine.egress and not machine.visited["factory_handler"]
        cases.append({"case": "unregistered_function", "function": f"{function:02x}"})

    for sample in (body[:-3] + b"\0" + body[-2:], b"\xa1\x01\x21\xa2\x00\x01",
                   crypt(body), b"\x01\x02\x03\x04", b""):
        machine = Machine(image)
        machine.receive(packet(sample))
        assert machine.builders[0]["body"] == sample.hex()
        assert machine.egress[0]["packet"] == packet(sample, destination=1).hex()
        assert not machine.visited["host_gcm_decrypt"]
        cases.append({"case": "opaque_clear_body", "body": sample.hex(),
                      "body_preserved": True, "inner_crc_checked": False})

    for destination in (0, 1, 2, 255):
        machine = Machine(image)
        machine.receive(packet(body, destination=destination))
        assert machine.egress[0]["packet"] == packet(body, destination=1).hex()
        cases.append({"case": "header_destination", "incoming_destination": destination,
                      "outgoing_destination": 1})

    return {"schema": 1, "firmware": {"product": "C1000 Gen 2 A1763",
            "radio_version": "0.3.3.0", "sha256": IMAGE_SHA256},
            "registrations_from_application_startup": machine.registrations,
            "cases_passed": len(cases), "cases": cases,
            "limitations": ["Original A1761 radio not replayed; equal version text is not equivalence.",
                            "OS queues/allocation/session state and AES-GCM are substituted.",
                            "Outgoing queue boundary captured; physical BLE/UART TX not executed.",
                            "No controller firmware or device executed; this is not a safe-probe approval."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    image = args.image.read_bytes()
    assert hashlib.sha256(image).hexdigest() == IMAGE_SHA256, "Unexpected radio image"
    result = suite(image)
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    if args.output:
        args.output.write_bytes(encoded)
    if args.manifest:
        manifest = {"tool": Path(__file__).name,
                    "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "firmware_sha256": IMAGE_SHA256,
                    "results_sha256": hashlib.sha256(encoded).hexdigest(),
                    "unicorn_version": unicorn.__version__, "cases_passed": result["cases_passed"],
                    "data": "Synthetic packets, no device credentials or captures"}
        args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases_passed": result["cases_passed"], "firmware_sha256": IMAGE_SHA256}))


if __name__ == "__main__":
    main()
