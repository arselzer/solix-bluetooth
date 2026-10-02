#!/usr/bin/env python3
"""Bounded offline A1763 radio BLE advertising recovery route replay.

Actual native admission, radio dispatch, opcode03/24 handlers, initialized BLE
enable helper, bitmap setters and advertising timer instructions execute.
NimBLE/OS, configured advertisement info, optional text/MAC providers and the
physical UART/TX boundary are substitutes. Synthetic fixtures only; no devices,
Wi-Fi, TLS, filesystem persistence or real credentials are accessed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import struct

import cryptography
import unicorn
from unicorn import riscv_const as R

from emulate_radio_rssi_routes import NativeMachine
from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256, crypt, packet

os.umask(0o077)
BLE_STATE = 0x3FC90534
BLE_BITMAP = 0x3FC90535
FAST_TIMER = 0x3FC829E8
CLOSE_TIMER = 0x3FC829E0
APP_BLE = 0x3FC90646
APP_WIFI = 0x3FC90647
PROTECTED = ((0x3FC86FA8, 0x368), (0x3FC86C40, 0x368),
             (0x3FC89EB4, 0x154), (0x3FC8B5BC, 0x310))


class Machine(NativeMachine):
    def __init__(self, image, *, ble_state=1, active=False):
        self.effects = []
        self.handlers = []
        self.enable_values = []
        self.forward_attempts = []
        self.active = active
        super().__init__(image)
        table = dict(struct.unpack("<II", self.read(0x3C147BE4+i*8, 8))
                     for i in range(42))
        assert table[3] == 0x4203CC10 and table[0x24] == 0x4203D056
        assert table[0x23] == 0x420432AA and table[0x25] == 0x42043316
        self.uc.mem_write(0x3FC903FE, b"\1")  # SDK BLE already initialized.
        self.uc.mem_write(0x3FC903FF, b"\1")  # Synthetic mutex exists.
        self.uc.mem_write(BLE_STATE, bytes((ble_state,)))
        self.uc.mem_write(BLE_BITMAP, b"\xff")
        self.uc.mem_write(APP_BLE, b"\0")
        self.uc.mem_write(APP_WIFI, b"\1")
        self.uc.mem_write(0x3FC8A188, bytes((1, 1, 1, 1)))

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address+size <= hi for lo, hi in (
                (0x3FC829D8, 0x3FC829F0),  # BLE timing counters, not identity.
                (0x3FC903FC, 0x3FC90405),  # SDK BLE volatile lifecycle flags.
                (BLE_STATE, BLE_BITMAP+1))):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1 = args[:2]
        if address in (0x4203CC10, 0x4203D056):
            self.handlers.append(f"{address:08x}")
            return
        if address == 0x4203D082:
            self.enable_values.append(uc.reg_read(R.UC_RISCV_REG_S1))
            return
        if address in (0x420432AA, 0x42043316):
            raise AssertionError("Disruptive wireless setter reached")
        if address == 0x420439CC:
            self.forward_attempts.append({"function": a0, "command": a1,
                "body_hex": self.read(args[2], args[3]).hex()})
            result = 0
        elif address == 0x42028D02:
            self.effects.append({"effect": "ble_disable_helper_boundary"})
            result = 0  # Only prove entry for forbidden zero/malformed fixtures.
        elif address == 0x42037294:  # Physical advertisement build/start boundary.
            self.effects.append({"effect": "advertisement_start_boundary"})
            result = 0
        elif address == 0x4205EC0A:  # Physical NimBLE advertising-active provider.
            self.effects.append({"effect": "advertisement_active_provider",
                                 "synthetic_value": self.active})
            result = int(self.active)
        elif address == 0x4205ECAC:  # Physical disconnect request; do not run stack.
            self.effects.append({"effect": "disconnect_boundary", "reason": a1})
            result = 1  # Synthetic failure returns without waiting for callbacks.
        elif address in (0x42036996, 0x4203697E):
            self.effects.append({"effect": "pal_lock" if address == 0x42036996
                                 else "pal_unlock"})
            result = 0
        elif address == 0x42037762:  # Configured advertisement-data setter.
            self.effects.append({"effect": "advertisement_info_boundary"})
            result = 0
        elif address == 0x42037A28:  # Physical advertising interval setter.
            self.effects.append({"effect": "advertisement_interval_boundary",
                "interval": int.from_bytes(self.read(a0, 2), "little")})
            result = 0
        elif address in (0x4202D6F8, 0x4202D71C):
            result = 0  # Synthetic SDK mutex take/release.
        elif address in (0x4202DCC8, 0x4202DD3C):
            self.effects.append({"effect": "os_timer_start" if address == 0x4202DCC8
                                 else "os_timer_stop", "timer": f"{a0:08x}"})
            result = 0
        elif address == 0x42030EE2:
            uc.mem_write(a0, bytes.fromhex("020000000001"))
            uc.mem_write(a1, struct.pack("<I", 6))
            result = 0
        elif address in (0x4202BD9A, 0x4203145C):
            result = 0  # Optional Ethernet state/SSID provider absent.
        elif any(lo <= address < hi for lo, hi in (
            (0x4203CC12, 0x4203CD6E), (0x4203D058, 0x4203D0CA),
            (0x42029120, 0x420291C4), (0x42028998, 0x42028A32),
            (0x42028BBA, 0x42028C14), (0x42028C14, 0x42028CE6),
            (0x420372A4, 0x420373BE), (0x420376DE, 0x42037762),
            (0x4204F95C, 0x4204F9A6), (0x4204E046, 0x4204E0A0),
            (0x420379EA, 0x420379F4))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def protected(self):
        return [self.read(address, size) for address, size in PROTECTED]

    def send_native(self, command, body=b"", *, encrypted=False, destination=0):
        self.incoming_encrypted = encrypted
        self.destination = destination
        before = self.protected()
        wire = packet(crypt(body) if encrypted else body,
                      command | (0x4000 if encrypted else 0), function=0x10,
                      destination=destination)
        self.receive_json(self.envelope(wire))
        assert self.protected() == before
        assert not self.builders and not self.egress  # No MCU command output.

    def response(self, command, expected_body=None):
        assert len(self.reply_frames) == len(self.publications) == 1
        wire = self.reply_frames[0]["wire"]
        assert self.reply_frames[0]["port"] == 5
        response_command = command | (0x4800 if self.incoming_encrypted else 0x0800)
        assert int.from_bytes(wire[7:9], "big") == response_command
        assert wire[6] == 0x10
        assert wire[4:7] == bytes((3, self.destination, 0x10))
        body = crypt(wire[9:-1], decrypt=True) if self.incoming_encrypted else wire[9:-1]
        if expected_body is not None:
            assert body == expected_body
        publication = self.publications[0]
        assert publication["topic"] == "dt/anker_power/A1763/SYNTHETIC00000001/param_info"
        assert publication["envelope"]["head"]["cmd"] == 16
        assert publication["envelope"]["head"]["cmd_status"] == 1
        assert base64.b64decode(json.loads(publication["envelope"]["payload"])["data"]) == wire
        return {"response_command": f"{response_command:04x}",
                "request_pattern": bytes((3, self.destination, 0x10)).hex(),
                "response_pattern": wire[4:7].hex(),
                "response_body_hex": body.hex(), "reply_port": 5,
                "mqtt_command": 16, "topic_suffix": "param_info"}


def suite(image):
    cases = []
    for destination in (0, 1, 2, 3):
        machine = Machine(image)
        machine.send_native(0x24, b"\xa1\1\1", destination=destination)
        assert machine.handlers == ["4203d056"] and not machine.forward_attempts
        cases.append({"case": "native_radio_pattern", "destination": destination,
                      **machine.response(0x24, b"\0")})
    for encrypted, state, active in itertools.product((False, True), (0, 1, 2, 3), (False, True)):
        machine = Machine(image, ble_state=state, active=active)
        machine.send_native(0x24, b"\xa1\1\1", encrypted=encrypted)
        assert machine.handlers == ["4203d056"]
        assert not machine.forward_attempts
        assert machine.read(APP_BLE, 2) == b"\0\1"  # MCU-facing status unchanged.
        assert machine.read(BLE_BITMAP, 1) == b"\xff"
        assert machine.read(0x3FC903FD, 1) == b"\1"
        assert machine.read(FAST_TIMER, 6) == bytes.fromhex("01000000b400")
        start_count = sum(e["effect"] == "advertisement_start_boundary" for e in machine.effects)
        assert start_count == int(state == 1 or (state == 2 and not active))
        cases.append({"case": "native_ble_enable", "gcm_input": encrypted,
            "synthetic_pal_state": state, "synthetic_advertising_active": active,
            "final_pal_state": machine.read(BLE_STATE, 1)[0],
            "advertisement_start_boundaries": start_count,
            "effects": machine.effects, "fast_timer_limit": 180,
            "application_wireless_flags_unchanged": True,
            "protected_identity_regions_unchanged": True, "controller_forwards": 0,
            **machine.response(0x24, b"\0")})
    for encrypted, body in itertools.product((False, True), (b"", b"\xa2\1\1")):
        machine = Machine(image)
        machine.send_native(0x24, body, encrypted=encrypted)
        assert not machine.effects and not machine.forward_attempts
        cases.append({"case": "missing_enable_tag", "gcm_input": encrypted,
            "request_body_hex": body.hex(), "effects": [], **machine.response(0x24, b"\1")})
    for value in (0, 2, 255):
        machine = Machine(image)
        machine.send_native(0x24, b"\xa1\1"+bytes((value,)))
        disable = value == 0
        assert any(e["effect"] == "ble_disable_helper_boundary" for e in machine.effects) == disable
        cases.append({"case": "enable_value_semantics", "value": value,
            "branch": "disable_helper_boundary" if disable else "enable_nonzero",
            "disable_helper_substituted": disable, "effects": machine.effects,
            **machine.response(0x24, b"\0")})
    machine = Machine(image)
    machine.send_native(0x24, b"\xa1\0")
    assert machine.enable_values and machine.enable_values != [0]
    cases.append({"case": "zero_length_enable_tlv", "unsafe_missing_length_guard": True,
        "read_value": machine.enable_values[0], "effects": machine.effects,
        **machine.response(0x24, b"\0")})
    for identity_field in ("account_id", "device_sn"):
        machine = Machine(image)
        before = machine.protected()
        envelope = machine.envelope(packet(b"\xa1\1\1", 0x24, function=0x10))
        body = json.loads(envelope["payload"])
        body[identity_field] = "different-synthetic-identity"
        envelope["payload"] = json.dumps(body)
        machine.receive_json(envelope)
        assert not machine.handlers and not machine.effects and not machine.reply_frames
        assert machine.protected() == before
        cases.append({"case": "identity_admission_rejection", "field": identity_field,
            "handler_calls": 0, "effects": [], "protected_identity_regions_unchanged": True})
    machine = Machine(image)
    machine.send_native(0x24, b"\xa1\1\1")
    machine.uc.mem_write(FAST_TIMER+2, struct.pack("<H", 99))
    machine.reply_frames.clear()
    machine.publications.clear()
    machine.send_native(0x24, b"\xa1\1\1")
    assert machine.read(FAST_TIMER, 6) == bytes.fromhex("01000000b400")
    assert sum(e["effect"] == "os_timer_start" for e in machine.effects) == 2
    cases.append({"case": "repeated_enable_resets_fast_timer", "reset_counter": 0,
        "fast_timer_limit": 180, "application_ble_flag": machine.read(APP_BLE, 1)[0],
        "protected_identity_regions_unchanged": True, **machine.response(0x24, b"\0")})
    for state, ble, wifi in itertools.product((1, 2, 3), (0, 1), (0, 1)):
        machine = Machine(image, ble_state=state)
        machine.uc.mem_write(APP_BLE, bytes((ble, wifi)))
        before = machine.read(0x3FC80000, 0x20000)
        machine.send_native(3)
        assert machine.handlers == ["4203cc10"] and not machine.effects
        response = machine.response(3)
        assert bytes.fromhex(response["response_body_hex"]).startswith(
            b"\0\xa1\1"+bytes((ble,))+b"\xa2\1"+bytes((wifi,)))
        after = machine.read(0x3FC80000, 0x20000)
        changed = [0x3FC80000+i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        allowed = set(range(0x3FC905EC, 0x3FC905F4)) | set(range(0x3FC906D0, 0x3FC906DC))
        allowed |= set(range(0x3FC8A0F4, 0x3FC8A0F8)) | set(range(0x3FC90390, 0x3FC90394))
        allowed |= set(range(0x3FC89E8C, 0x3FC89E96)) | set(range(0x3FC903E8, 0x3FC903F0))
        assert set(changed) <= allowed
        cases.append({"case": "native_wireless_query", "synthetic_pal_state": state,
            "application_ble_flag": ble, "application_wifi_flag": wifi,
            "does_not_report_physical_advertising": True, "effects": [], **response})
    for command in (0x4A, 0x4B, 0x64):
        machine = Machine(image)
        machine.send_native(command)
        assert not machine.handlers and not machine.reply_frames
        assert machine.forward_attempts == [{"function": 0x10, "command": command, "body_hex": ""}]
        cases.append({"case": "high_opcode_namespace_boundary", "command": f"{command:04x}",
                      "controller_forward_boundaries": 1, "local_handler_calls": 0})
    for count in (179, 180, 181, 182):
        machine = Machine(image, ble_state=2)
        machine.uc.mem_write(FAST_TIMER, bytes((1, 0))+struct.pack("<HH", count, 180))
        machine.run(0x42028C14)
        timer = machine.read(FAST_TIMER, 6)
        assert timer == (bytes((1, 0))+struct.pack("<HH", count+1, 180) if count <= 180 else bytes(6))
        cases.append({"case": "fast_advertisement_timer", "starting_counter": count,
                      "ending_timer_hex": timer.hex(), "effects": machine.effects,
                      "physical_advertising_disabled": False})
    return {"model": "A1763 C1000 Gen 2", "main_version": "1.1.4.9",
        "radio_version": "0.3.3.0", "image_sha256": IMAGE_SHA256,
        "case_count": len(cases), "cases": cases,
        "native_request_contract": {"function": "10", "request_pattern": "030010",
            "enable_command": "0024", "enable_body_hex": "a10101",
            "enable_wire_hex": "ff090d000300100024a101016d",
            "query_command": "0003", "query_body_hex": "",
            "head_cmd": 17, "head_cmd_status": 2, "head_sign_code": 1,
            "payload": "JSON string containing synthetic account_id/device_sn and base64 data"},
        "native_response_contract": {"response_pattern": "030010", "enable_command": "0824",
            "enable_body_hex": "00", "enable_wire_hex": "ff090b00030010082400c2",
            "head_cmd": 16, "head_cmd_status": 1, "head_sign_code": 0,
            "head_seed": "null", "topic_suffix": "param_info"},
        "limits": ["Actual bounded radio instructions; synthetic initialized BLE/native state.",
            "Physical NimBLE, OS timer/mutex, advertisement configuration and providers substituted.",
            "No flash persistence, async callbacks, MCU, Wi-Fi, TLS or physical advertising proof.",
            "Query03 reports application flags, not physical advertising status.",
            "No equivalence to unrecovered original C1000 or C2000 radio image."]}


def main():
    if not __debug__:
        raise SystemExit("Run without -O: replay assertions must be enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    image = args.image.read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if len(image) != 1482800 or digest != IMAGE_SHA256 or args.image.name != IMAGE_NAME:
        raise SystemExit("Wrong radio image: exact filename, size and SHA-256 required")
    results = suite(image)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "radio-ble-advertising-results.json"
    output.write_text(json.dumps(results, indent=2)+"\n")
    sources = (Path(__file__).name, "emulate_radio_rssi_routes.py", "emulate_radio_factory_routes.py")
    manifest = {"source": Path(__file__).name, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dependency_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in sources[1:]},
        "firmware_sha256": digest, "results_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "case_count": results["case_count"], "python": platform.python_version(),
        "unicorn": unicorn.__version__, "cryptography": cryptography.__version__}
    (args.output_dir / "radio-ble-advertising-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({"case_count": results["case_count"], "results_sha256": manifest["results_sha256"]}))


if __name__ == "__main__":
    main()
