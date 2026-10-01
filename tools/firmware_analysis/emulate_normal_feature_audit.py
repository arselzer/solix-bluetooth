#!/usr/bin/env python3
"""Offline radio RSSI -> controller A3 audit; synthetic inputs, no hardware.

Executes C1000 Gen 2 main 1.1.4.9 ARM and radio 0.3.3.0 RISC-V instructions.
ESP AP-info, optional network-string/state providers, logging, libc, queue and
transport boundaries are substituted. Main TLV parsing, callback, cached RSSI
getter, A3 serializer and MTU handler execute. No actual RF, UART, BLE, scheduler,
subscription or sensor behavior is simulated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn import riscv_const as R
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_additional_features import TelemetryMachine
from emulate_clock_semantics import REQUEST
from emulate_radio_config_candidates import Machine as RadioBase, IMAGE_NAME, IMAGE_SHA256
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, firmware_image

os.umask(0o077)
PAYLOAD = 0x21000A00  # Does not overlap the parser's 100 six-byte descriptors.


class Main(TelemetryMachine):
    def __init__(self):
        super().__init__()
        self.queued = []

    def step(self, uc, address, size, data):
        if address == 0x08016A70:
            pointer = uc.reg_read(UC_ARM_REG_R0)
            self.queued.append(bytes(uc.mem_read(pointer, 20)))
            self.back(1)
        elif any(lo <= address < hi for lo, hi in (
            (0x0801AE44, 0x0801AF36), (0x0800D1D8, 0x0800D1F2),
            (0x08022576, 0x08022612), (0x0800BEC8, 0x0800BF1C),
            (0x08030D80, 0x08030DB4))):
            return
        else:
            super().step(uc, address, size, data)

    def parse(self, body: bytes):
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225A6, PAYLOAD)

    def wireless_response(self, body: bytes, accepted=True):
        assert body[0] == 0
        self.parse(body[1:])
        self.uc.reg_write(UC_ARM_REG_R1, REQUEST)
        self.run(0x0801AE44, int(accepted))

    def mtu_report(self, mtu: int):
        self.parse(b"\xa1\x02" + struct.pack("<H", mtu))
        self.run(0x0800BEC8, REQUEST)
        assert self.responses == [b"\x00"]


class Radio(RadioBase):
    def __init__(self, image: bytes, rssi: int, error: int = 0):
        super().__init__(image)
        self.rssi, self.error = rssi, error
        self.responses = []
        self.ap_info_calls = 0

    def step(self, uc, address, size, data):
        a0, a1, a2 = (uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(3))
        if address == 0x420EBE0A:
            self.ap_info_calls += 1
            # ESP AP-info is the observation boundary. The real wrapper reads
            # signed byte +0x2c only on its success branch.
            uc.mem_write(a0 + 0x2C, bytes((self.rssi & 255,)))
            result = self.error
        elif address in (0x421271AC, 0x421270B8, 0x4202BD9A, 0x4203145C):
            result = 0
        elif address == 0x42030EE2:
            # Optional string omitted; no real SSID or identifier is read.
            uc.mem_write(a1, bytes(4))
            result = 0
        elif address == 0x4204FA68:
            self.responses.append(bytes(uc.mem_read(a1, a2)))
            result = 0
        elif any(lo <= address < hi for lo, hi in (
            (0x42011522, 0x420115A4), (0x4203CB26, 0x4203CD6E),
            (0x4204DD4C, 0x4204DDF4), (0x4204E046, 0x4204E04A))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))


def fields(body: bytes) -> dict[int, bytes]:
    result, offset = {}, 1
    assert body[0] == 0
    while offset < len(body):
        tag, size = body[offset:offset+2]
        result[tag] = body[offset+2:offset+2+size]
        offset += size + 2
    assert offset == len(body)
    return result


def quality(value: int) -> int:
    # Instruction-level wrap precedes the upper clamp; not a usual RSSI clamp.
    return min(((value + 100) & 127) * 2, 100)


def run_suite(radio_image: bytes) -> dict:
    rows = {"radio_to_main": [], "direct_radio_rssi": [], "all_signed_cached_values": [],
            "incremental_retention": [], "missing_or_rejected_response": [],
            "mtu_capacity": [], "request_registration": []}
    m = Radio(radio_image, -70)
    table = dict(struct.unpack("<H2xI", m.uc.mem_read(0x3C147BE4+i*8, 8))
                 for i in range(42))
    assert table[3] == 0x4203CC10
    assert table[0x22] == 0x4203CB26
    assert table[0x4B] == 0x4203DA2C
    main_image = firmware_image()
    index = 0x080338D8 - 0x08005000
    assert main_image[index:index+8] == struct.pack("<II", 0x4E, 0x0800BEC9)
    m = Main()
    m.run(0x08030D80, 0x0801AE45)
    assert len(m.queued) == 1
    callback, builder, context, countdown, command, function = struct.unpack(
        "<IIIIHBx", m.queued[0])
    assert (callback, builder, context, countdown, command, function) == (
        0x0801AE45, 0x08012EBD, 0, 50, 3, 0x10)
    rows["request_registration"].append({
        "main_callback": "0801ae44", "main_builder": "08012ebc",
        "internal_function": 16, "internal_command": 3,
        "radio_handler": "4203cc10", "different_mqtt_state_command": 75,
        "mtu_report_internal_command": 78})

    for rssi, error in ((-100, 0), (-70, 0), (-30, 0), (0, 0), (-70, 1)):
        radio = Radio(radio_image, rssi, error)
        radio.run(0x4203CB26, radio.alloc(0x100))
        assert radio.ap_info_calls == 1 and len(radio.responses) == 1
        body = radio.responses[0]
        if error or rssi == 0:
            assert body == b"\x01"
        else:
            assert body == b"\x00\xa1\x04" + struct.pack("<i", rssi)
        rows["direct_radio_rssi"].append({"synthetic_rssi_dbm": rssi,
            "ap_info_error": error, "response_status": body[0],
            "signed_rssi_reply": rssi if body[0] == 0 else None,
            "external_transport_not_replayed": True})

    for rssi, error in ((-100, 0), (-99, 0), (-80, 0), (-70, 0), (-51, 0),
                        (-50, 0), (-30, 0), (0, 0), (-70, 1), (-70, 0x3001)):
        radio = Radio(radio_image, rssi, error)
        radio.run(0x4203CC10, radio.alloc(0x100))
        assert radio.ap_info_calls == 1 and len(radio.responses) == 1
        body = radio.responses[0]
        parsed = fields(body)
        expected_rssi = 0 if error else rssi
        assert parsed[0xA3] == bytes((expected_rssi & 255,))
        m = Main()
        # Readiness and reconnect bookkeeping remain at the cold synthetic
        # baseline. This avoids entering binding/reconnect side effects.
        m.wireless_response(body)
        a3 = m.serialize(0xA3)
        cached = int.from_bytes(m.uc.mem_read(0x20000786, 1), "little", signed=True)
        assert cached == expected_rssi and a3[8] == quality(expected_rssi)
        assert m.persistence_calls == 0
        rows["radio_to_main"].append({"synthetic_rssi_dbm": rssi,
            "ap_info_error": error, "cached_signed_byte": cached,
            "a3_quality_byte": a3[8], "response_status_still_zero": body[0] == 0,
            "settings_persistence_calls": m.persistence_calls})

    for value in range(-128, 128):
        m = Main()
        m.uc.mem_write(0x20000786, bytes((value & 255,)))
        a3 = m.serialize(0xA3)
        assert a3[8] == quality(value)
        rows["all_signed_cached_values"].append({
            "cached_signed_byte": value, "a3_quality_byte": a3[8]})

    for previous in (0, 42, 100, 255):
        m = Main()
        m.uc.mem_write(0x20000786, struct.pack("b", -70))
        prior = bytearray(14)
        prior[0], prior[8] = 4, previous
        a3 = m.serialize(0xA3, mode=3, prior=bytes(prior))
        assert a3[8] == previous
        rows["incremental_retention"].append({"prior_quality_byte": previous,
            "current_cached_rssi_dbm": -70, "a3_quality_byte": a3[8],
            "mode": 3})

    for accepted, body in ((False, b"\0\xa1\1\0\xa2\1\0\xa3\1\x9c"),
                           (True, b"\0\xa1\1\0\xa2\1\0")):
        m = Main()
        m.uc.mem_write(0x20000786, struct.pack("b", -70))
        m.wireless_response(body, accepted)
        assert m.serialize(0xA3)[8] == 60
        rows["missing_or_rejected_response"].append({"accepted": accepted,
            "rssi_tag_present": 0xA3 in fields(body),
            "cached_value_preserved": True, "a3_quality_byte": 60})

    for mtu in (0, 23, 46, 47, 64, 128, 247, 256, 517, 65535):
        m = Main()
        m.mtu_report(mtu)
        actual = int.from_bytes(m.uc.mem_read(0x200007B8, 2), "little")
        assert actual == (mtu - 46) & 0xFFFC
        a3 = m.serialize(0xA3)
        assert int.from_bytes(a3[9:11], "little") == actual
        rows["mtu_capacity"].append({"synthetic_reported_mtu": mtu,
            "a3_capacity_bytes": actual, "acknowledgement_status": 0,
            "underflow_domain": mtu < 46})
    return rows


def main():
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.firmware_dir:
        os.environ["SOLIX_FIRMWARE_DIR"] = str(args.firmware_dir)
    directory = Path(os.environ.get("SOLIX_FIRMWARE_DIR", Path(__file__).resolve().parents[2]
                                   / "firmware/c1000_gen2/1.1.4.9"))
    radio_image = (directory / IMAGE_NAME).read_bytes()
    assert hashlib.sha256(radio_image).hexdigest() == IMAGE_SHA256
    assert len(radio_image) == 1482800
    firmware_image()  # Main input size and SHA-256 enforcement in shared helper.
    results = run_suite(radio_image)
    counts = {name: len(rows) for name, rows in results.items()}
    assert sum(counts.values()) == 288
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "normal-feature-audit-results.json"
    result_path.write_text(json.dumps(results, indent=2) + "\n")
    result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_additional_features.py",
               "emulate_clock_semantics.py", "emulate_radio_config_candidates.py", "replay_io.py")
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": [{"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
                     {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256}],
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest()
                          for name in sources}, "substitution_limits": __doc__.strip()}
    manifest_path = args.output / "normal-feature-audit-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
