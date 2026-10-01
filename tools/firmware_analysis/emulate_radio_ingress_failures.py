#!/usr/bin/env python3
"""Offline A1763 radio ingress/error-path audit using synthetic packets.

Runs actual receive validation, queue consumption, payload classification,
error reply serialization and checksums. OS/session/logging/AES boundaries are
substituted; immediate replies stop at the port-send wrapper. Short-frame
cases execute only the receive validator, never the downstream parser. No
hardware, network, controller, production credentials or live commands.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import unicorn
from unicorn import riscv_const as R

from emulate_radio_factory_routes import (
    Machine as Base, IMAGE_NAME, IMAGE_SHA256, SECRET, crypt, packet,
)


def cbc(data: bytes, *, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(SECRET[:16]), modes.CBC(SECRET[16:]))
    if decrypt:
        context = cipher.decryptor()
        decoded = context.update(data) + context.finalize()
        unpad = padding.PKCS7(128).unpadder()
        return unpad.update(decoded) + unpad.finalize()
    pad = padding.PKCS7(128).padder()
    context = cipher.encryptor()
    return context.update(pad.update(data) + pad.finalize()) + context.finalize()


class Machine(Base):
    def __init__(self, image: bytes, *, cipher: int = 1, forced_error: int | None = None,
                 log_callback: bool = False):
        self.cipher = cipher
        self.forced_error = forced_error
        self.decrypt_calls = []
        self.reply_bodies = []
        self.replies = []
        self.dump_calls = 0
        self.debug_lines = []
        self.tracked_input = None
        self.past_input_reads = []
        super().__init__(image)
        self.uc.mem_write(0x50000100, b"\x13\0\0\0")
        self.uc.mem_write(0x3FC906DC, struct.pack("<I", 0x50000100 if log_callback else 0))
        self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.track_read)

    def track_read(self, uc, _access, address, size, _value, _data):
        if self.tracked_input is None:
            return
        start, length = self.tracked_input
        if start <= address < start + 16 and address + size > start + length:
            self.past_input_reads.append({
                "pc": f"{uc.reg_read(R.UC_RISCV_REG_PC):08x}",
                "offset": address - start, "size": size,
            })

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(5)]
        a0, a1, a2, a3, a4 = args
        if address == 0x4205103C:
            result = self.cipher
        elif address in (0x42050E98, 0x42050BF6):
            assert self.read(a0, 32) == SECRET
            if self.forced_error is not None:
                result = self.forced_error
            else:
                assert address == 0x42050E98, "CBC cryptography is not emulated"
                try:
                    decoded = crypt(self.read(a1, a2), decrypt=True)
                except InvalidTag:
                    result = 1  # Synthetic primitive failure; not an ESP error enum.
                else:
                    uc.mem_write(a3, decoded)
                    uc.mem_write(a4, struct.pack("<I", len(decoded)))
                    result = 0
            self.decrypt_calls.append({"cipher": "GCM" if address == 0x42050E98 else "CBC",
                                       "input_length": a2, "return": result})
        elif address == 0x42053EC0:
            # The actual immediate-response formatter has already run. The
            # separate RSSI routing replay follows this wrapper to port callbacks.
            wire = self.read(a1, a2)
            assert packet(wire[9:-1], int.from_bytes(wire[7:9], "big"),
                          wire[6], wire[5]) == wire
            body = wire[9:-1]
            if wire[7] & 0x40:
                body = (crypt(body, decrypt=True) if self.cipher == 1
                        else cbc(body, decrypt=True))
            self.replies.append({"port": a0, "command": wire[7:9].hex(),
                                 "body": body.hex(), "packet": wire.hex()})
            result = 0
        elif address == 0x42050BDC:
            # Host CBC reply encryption; selected primitive instructions are
            # outside this audit, just as GCM is in the shared harness.
            assert self.read(a0, 32) == SECRET
            encoded = cbc(self.read(a3, a4))
            uc.mem_write(a1, encoded)
            uc.mem_write(a2, struct.pack("<I", len(encoded)))
            result = 0
        elif address == 0x50000100:
            assert a0 == 4
            self.debug_lines.append(self.read(a1, 128).split(b"\0", 1)[0].decode("ascii"))
            result = 0
        else:
            if address == 0x42051BA0:
                self.dump_calls += 1  # Firmware's byte-dump log sink, not a key clear.
            if (0x4204FA68 <= address < 0x4204FCAE
                    or 0x4204F846 <= address < 0x4204F9A6
                    or 0x42051A5C <= address < 0x42051BA8
                    or address == 0x420541C8):
                if address == 0x4204FA68:
                    self.reply_bodies.append(self.read(a1, a2).hex())
                return
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))


def expect_failure(machine: Machine, status: int, port: int):
    assert machine.reply_bodies == [f"{status:02x}"]
    assert len(machine.replies) == 1
    assert machine.replies[0]["body"] == f"{status:02x}"
    assert machine.replies[0]["port"] == port
    assert machine.replies[0]["command"] == "4800"
    assert not machine.visited["function_dispatch"]
    assert not machine.visited["factory_handler"]
    assert not machine.builders and not machine.egress


def suite(image: bytes) -> dict:
    rows = {"gcm_failure": [], "primitive_error_returns": [],
            "marked_body_lengths": [], "valid_gcm": [],
            "short_outer_validator": [], "outer_corruption": [], "failure_byte_dump": []}
    body = b"\xa1\x01\x21"
    encoded = crypt(body)
    for port in (0, 5):
        for mutation in ("ciphertext", "tag", "truncated", "empty"):
            data = bytearray(encoded)
            if mutation in ("ciphertext", "tag"):
                data[0 if mutation == "ciphertext" else -1] ^= 1
            else:
                data = data[:5] if mutation == "truncated" else b""
            m = Machine(image)
            m.receive(packet(bytes(data), 0x4000), port=port, mqtt=port == 5)
            status = 8 if mutation == "empty" else 1
            expect_failure(m, status, port)
            assert len(m.decrypt_calls) == int(mutation != "empty")
            rows["gcm_failure"].append({"port": port, "mutation": mutation,
                "decrypt_calls": m.decrypt_calls, "reply_status": status,
                "handler_dispatched": False, "reply_cipher": "GCM"})

    for error in (1, 3, 0xFFFFFFFF):
        m = Machine(image, forced_error=error)
        m.receive(packet(encoded, 0x4000))
        expect_failure(m, 1, 0)
        assert len(m.decrypt_calls) == 1 and m.dump_calls == 2
        rows["primitive_error_returns"].append({"synthetic_return": error,
            "reply_status": 1, "byte_dump_calls": 2, "handler_dispatched": False})

    for cipher in (0, 1):
        for length in (0, 1, 15, 16, 17, 31, 32):
            m = Machine(image, cipher=cipher, forced_error=3)
            m.receive(packet(bytes(length), 0x4000))
            before_primitive = length == 0 or (cipher == 0 and length % 16 != 0)
            status = 8 if before_primitive else 1
            expect_failure(m, status, 0)
            assert len(m.decrypt_calls) == int(not before_primitive)
            rows["marked_body_lengths"].append({"synthetic_cipher_selector": cipher,
                "input_length": length, "decrypt_calls": m.decrypt_calls,
                "reply_status": status, "handler_dispatched": False})

    for sample in (b"", body, bytes.fromhex("ee00080000f000daa5fc55")):
        m = Machine(image)
        m.receive(packet(crypt(sample), 0x4000))
        assert len(m.decrypt_calls) == 1 and m.decrypt_calls[0]["return"] == 0
        assert m.visited["factory_handler"] == 1
        assert m.builders[0]["body"] == sample.hex()
        assert len(m.egress) == 1 and not m.replies
        rows["valid_gcm"].append({"plaintext_length": len(sample),
                                 "factory_body_preserved": True})

    for cipher in (0, 1):
        for enabled in (False, True):
            m = Machine(image, cipher=cipher, forced_error=1, log_callback=enabled)
            m.receive(packet(bytes(16), 0x4000))
            expect_failure(m, 1, 0)
            expected = [" ".join(f"{byte:02X}" for byte in part) + " \r\n"
                        for part in (SECRET[:16], SECRET[16:])]
            assert m.debug_lines == (expected if enabled else [])
            assert m.dump_calls == 2
            rows["failure_byte_dump"].append({"synthetic_cipher_selector": cipher,
                "log_callback_installed": enabled, "byte_dump_calls": 2,
                "formatted_lines": len(m.debug_lines),
                "both_synthetic_key_halves_formatted": enabled,
                "production_logging_configuration_unverified": True})

    for length in range(10):
        m = Machine(image)
        # The allocation deliberately supplies readable padding so the harness
        # can observe instruction reads beyond the caller's stated buffer.
        wire = bytearray(16)
        wire[:4] = b"\xff\x09" + struct.pack("<H", length)
        if length >= 4:
            checksum = 0
            for byte in wire[:length - 1]:
                checksum ^= byte
            wire[length - 1] = checksum
        pointer = m.alloc(bytes(wire))
        m.tracked_input = pointer, length
        m.run(0x4204D14E, 0, pointer, length)
        accepted = length >= 5
        assert bool(m.queue) == accepted
        assert not m.visited["payload_parser"]
        assert bool(m.past_input_reads) == (0 < length < 4)
        rows["short_outer_validator"].append({"stated_length": length,
            "queued": accepted, "past_input_reads": m.past_input_reads,
            "downstream_parser_executed": False})

    for mutation in ("sync", "length_low", "length_high", "checksum", "concatenated"):
        m = Machine(image)
        wire = bytearray(packet(body))
        if mutation == "concatenated":
            wire += packet(body)
        else:
            wire[{"sync": 0, "length_low": 2, "length_high": 3,
                  "checksum": len(wire) - 1}[mutation]] ^= 1
        pointer = m.alloc(bytes(wire))
        m.run(0x4204D14E, 0, pointer, len(wire))
        assert not m.queue and not m.visited["payload_parser"]
        rows["outer_corruption"].append({"mutation": mutation, "queued": False})
    return rows


def main():
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    image = args.image.read_bytes()
    assert len(image) == 1482800 and hashlib.sha256(image).hexdigest() == IMAGE_SHA256
    results = suite(image)
    counts = {name: len(rows) for name, rows in results.items()}
    encoded = (json.dumps(results, indent=2, sort_keys=True) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    args.output.chmod(0o600)
    sources = (Path(__file__), Path(__file__).with_name("emulate_radio_factory_routes.py"))
    manifest = {"hardware_access": False, "firmware_name": IMAGE_NAME,
        "firmware_sha256": IMAGE_SHA256, "unicorn_version": unicorn.__version__,
        "cases_passed": sum(counts.values()), "case_counts": counts,
        "result_sha256": hashlib.sha256(encoded).hexdigest(),
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sources}, "substitution_limits": __doc__.strip()}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    args.manifest.chmod(0o600)
    print(json.dumps({"cases_passed": sum(counts.values()), "case_counts": counts}))


if __name__ == "__main__":
    main()
