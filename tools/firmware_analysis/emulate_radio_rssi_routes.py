#!/usr/bin/env python3
"""Offline C1000 Gen 2 radio RSSI BLE/native MQTT route replay.

Actual RISC-V receive, parser, dispatch, identity/header checks, RSSI wrapper,
response framing, transport registration/selection and MQTT JSON/topic builders
execute. OS queues/allocation, cJSON/libc/base64, GCM primitives, established
session keys, AP-info, identity-refresh providers, time, BLE TX and final MQTT
publish are substitutes. No TLS, pairing, Wi-Fi, controller, device or network.
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

from emulate_radio_factory_routes import (
    Machine as Base, packet, crypt, IMAGE_NAME, IMAGE_SHA256,
)

os.umask(0o077)


class Machine(Base):
    def __init__(self, image: bytes, *, rssi=-70, ap_info_error=0):
        self.rssi = rssi
        self.ap_info_error = ap_info_error
        self.ap_info_calls = 0
        self.reply_frames = []
        self.reply_contexts = []
        self.transmitted = []
        super().__init__(image)
        self.run(0x42048852, stop_at=0x4204887C)

    def write_guard(self, uc, access, address, size, value, data):
        if 0x3FC905EC <= address < address + size <= 0x3FC905F4:
            return  # Last radio command and repeat-log counter.
        if 0x3FC8C5D8 <= address < address + size <= 0x3FC8C608:
            return  # Actual startup transport registration.
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address == 0x420EBE0A:
            self.ap_info_calls += 1
            uc.mem_write(a0+0x2C, bytes((self.rssi & 255,)))
            result = self.ap_info_error
        elif address == 0x42053EC0:
            self.reply_frames.append({"port": a0, "wire": self.read(a1, a2)})
            return
        elif address in (0x42028CF2, 0x420206B4):
            self.transmitted.append({"transport": "ble" if address == 0x42028CF2 else "mqtt",
                                     "wire": self.read(a0, a1), "arguments": args[2:5]})
            result = 0
        elif address == 0x4204FA68:
            self.reply_contexts.append({"port": self.u32(a0),
                "route_kind": self.read(a0+0x632, 1)[0],
                "route_detail": self.read(a0+0x633, 1)[0]})
            return
        elif any(lo <= address < hi for lo, hi in (
            (0x42011522, 0x420115A4), (0x4203C9E0, 0x4203CAB4),
            (0x4203CB26, 0x4203CC10), (0x4204DD4C, 0x4204DDF4),
            (0x4204F846, 0x4204F95C), (0x4204FA68, 0x4204FCAE),
            (0x42048852, 0x4204887C), (0x42053EC2, 0x42053F70),
            (0x42054202, 0x420542B8), (0x4203C98A, 0x4203C9A0),
            (0x4203FC9A, 0x4203FD00))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def receive_ble(self, wire):
        self.run(0x4203FC9A, 7, self.alloc(wire), len(wire))
        if self.queue:
            self.run(0x4204D30E)


class NativeMachine(Machine):
    """Run native JSON admission using synthetic cJSON/libc/identity providers."""
    def __init__(self, image, **kwargs):
        self.objects = {}
        self.refresh_requests = []
        self.json_keys = []
        self.publications = []
        super().__init__(image, **kwargs)
        self.account = b"synthetic-rssi-account"
        self.serial = b"SYNTHETIC00000001"
        self.account_pointer = self.alloc(self.account + b"\0")
        self.uc.mem_write(0x3FC89F08, (self.account + b"\0").ljust(128, b"\0"))
        self.uc.mem_write(0x3FC89EE8, (self.serial + b"\0").ljust(32, b"\0"))
        self.run(0x42048500, stop_at=0x42048546)
        assert self.u32(0x3FC8A10C) == 0x42043A18
        self.uc.mem_write(0x3FC89EB4, struct.pack("<I", 0x20221224))
        self.uc.mem_write(0x3FC89EB8, b"anker_power\0")
        self.uc.mem_write(0x3FC89ED8, b"A1763\0")
        self.uc.mem_write(0x3FC8A0EC, struct.pack("<I", 1))  # Synthetic initialized MQTT.

    def string(self, address):
        for length in range(4096):
            if self.read(address+length, 1) == b"\0":
                return self.read(address, length)
        raise AssertionError("unterminated synthetic string")

    def node(self, value):
        pointer = self.alloc(40)
        self.objects[pointer] = value
        if isinstance(value, str):
            self.uc.mem_write(pointer+0x10, struct.pack("<I", self.alloc(value.encode()+b"\0")))
        elif type(value) is int:
            self.uc.mem_write(pointer+0x14, struct.pack("<I", value & 0xFFFFFFFF))
        return pointer

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address + size <= hi for lo, hi in (
            (0x3FC8A0F4, 0x3FC8A0F8),  # Current optional response-topic node.
            (0x3FC8A0F8, 0x3FC8A12C),  # Actual application callback registration.
            (0x3FC90390, 0x3FC90394),  # Uplink message sequence.
            (0x3FC89E8C, 0x3FC89E96),  # Locally generated uplink session label.
            (0x3FC903E8, 0x3FC903F0))):  # Uplink sequence/time and last request timestamp.
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2, a3, a4 = args[:5]
        if address == 0x420206B4:
            self.transmitted.append({"transport": "mqtt", "wire": self.read(a0, a1),
                                     "arguments": args[2:5]})
            return
        elif address == 0x4204CE9A:
            try:
                result = self.node(json.loads(self.string(a0)))
            except (ValueError, UnicodeDecodeError):
                result = 0
        elif address == 0x4204CECE:
            key = self.string(a1).decode()
            self.json_keys.append(key)
            obj = self.objects.get(a0)
            result = self.node(obj[key]) if isinstance(obj, dict) and key in obj else 0
        elif address == 0x42017E00:
            result = self.alloc(a0)
        elif address == 0x4204D0A4:
            result = self.node({})
        elif address == 0x4204CFE4:
            self.objects[a0][self.string(a1).decode()] = self.string(a2).decode()
            result = 1
        elif address in (0x4204CEDE, 0x4204CED8):
            self.objects[a0][self.string(a1).decode()] = self.objects[a2]
            result = 1
        elif address == 0x4201B1FE:
            self.objects[a0][self.string(a1).decode()] = a2
            result = 1
        elif address in (0x4204CEA2, 0x4204CEA8):
            result = self.alloc(json.dumps(self.objects[a0]).encode()+b"\0")
        elif address in (0x42017E4E, 0x4204C93C, 0x4204D146):
            result = 0
        elif address == 0x42012450:
            result = 0x3FC89EE8
        elif address == 0x42035DCE:
            result = 1700000000
        elif address == 0x40000374:
            result = len(self.string(a0))
        elif address == 0x40000404:
            result = len(self.string(a0)[:a1])
        elif address == 0x40000360:
            x, y = self.read(a0, a2), self.read(a1, a2)
            result = (x > y) - (x < y)
        elif address == 0x4201B52A:
            decoded = base64.b64decode(self.read(a0, a1), validate=True)
            assert len(decoded) <= a2
            uc.mem_write(a3, decoded)
            uc.mem_write(a4, struct.pack("<I", len(decoded)))
            result = 0
        elif address == 0x4201B404:
            encoded = base64.b64encode(self.read(a0, a1))
            assert len(encoded)+1 <= a2
            uc.mem_write(a3, encoded+b"\0")
            uc.mem_write(a4, struct.pack("<I", len(encoded)))
            result = 0
        elif address == 0x4204BC48:
            template = self.string(a2)
            assert template == b"dt/%s/%s/%s/param_info"
            rendered = template % tuple(self.string(p) for p in args[3:6])
            assert len(rendered)+1 <= a1
            uc.mem_write(a0, rendered+b"\0")
            result = len(rendered)
        elif address == 0x42026E32:
            self.publications.append({"topic": self.read(a2, a3).decode(),
                "envelope": json.loads(self.read(a0, a1)), "publish_flag": a4})
            result = 0
        elif address in (0x420115E8, 0x4201263C, 0x42026DB4):
            self.refresh_requests.append(f"{address:08x}")
            result = 0
        elif address == 0x420122F4:
            self.refresh_requests.append(f"{address:08x}")
            result = self.account_pointer
        elif any(lo <= address < hi for lo, hi in (
            (0x4203B120, 0x4203B6D2), (0x42027C90, 0x42027FB6),
            (0x42026CF6, 0x42026DB4), (0x42026F4E, 0x42026FA6),
            (0x42027022, 0x42027042), (0x42027238, 0x42027272),
            (0x4202733E, 0x42027406), (0x420206B6, 0x4202080E),
            (0x4201C17A, 0x4201C1D0), (0x42025BE8, 0x42026388),
            (0x42026A50, 0x42026C10), (0x42048500, 0x42048546),
            (0x42028022, 0x4202804A))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def envelope(self, wire):
        return {"head": {"version": "1.0.0.1", "sess_id": "synthetic-session",
            "client_id": "synthetic-client", "msg_seq": 1, "cmd": 17,
            "cmd_status": 2, "timestamp": 1700000000, "sign_code": 1},
            "payload": json.dumps({"account_id": self.account.decode(),
                "device_sn": self.serial.decode(), "data": base64.b64encode(wire).decode()})}

    def receive_json(self, envelope):
        raw = json.dumps(envelope).encode() + b"\0"
        pointer = self.alloc(raw)
        topic = b"cmd/anker_power/A1763/" + self.serial + b"/req"
        self.run(0x42027F5E, self.alloc(topic+b"\0"), len(topic), pointer, len(raw)-1)
        if self.queue:
            self.run(0x4204D30E)


def assert_response(machine, *, encrypted, mqtt, rssi, error, index=0):
    expected_body = b"\x01" if error or rssi == 0 else b"\0\xa1\4" + struct.pack("<i", rssi)
    reply = machine.reply_frames[index]
    wire = reply["wire"]
    assert reply["port"] == (5 if mqtt else 0)
    expected = packet(crypt(expected_body) if encrypted else expected_body,
                      0x4822 if encrypted else 0x0822, function=0x10)
    assert wire == expected
    assert machine.transmitted[index]["wire"] == wire
    assert machine.transmitted[index]["transport"] == ("mqtt" if mqtt else "ble")
    assert not machine.builders and not machine.egress  # No MCU command forwarding.
    if mqtt:
        publication = machine.publications[index]
        assert publication["topic"] == "dt/anker_power/A1763/SYNTHETIC00000001/param_info"
        assert publication["publish_flag"] == 1
        head = publication["envelope"]["head"]
        assert head["cmd"] == 16 and head["cmd_status"] == 1
        payload = json.loads(publication["envelope"]["payload"])
        assert payload["pn"] == "A1763" and payload["sn"] == machine.serial.decode()
        assert base64.b64decode(payload["data"]) == wire
    return {"reply_port": reply["port"], "response_command": f"{int.from_bytes(wire[7:9], 'big'):04x}",
            "response_body_hex": expected_body.hex(), "reply_function": wire[6],
            "no_controller_forward": True,
            "mqtt_reply_command": 16 if mqtt else None,
            "mqtt_topic_suffix": "param_info" if mqtt else None}


def suite(image):
    results = []
    bodies = {"empty": b"", "source21": b"\xa1\1\x21",
              "source22_timestamp": b"\xa1\1\x22\xfe\5\3" + struct.pack("<I", 1700000000)}
    for mqtt, encrypted, (rssi, error), (label, body) in itertools.product(
            (False, True), (False, True), ((-70, 0), (-100, 0), (0, 0), (-70, 1)), bodies.items()):
        machine = NativeMachine(image, rssi=rssi, ap_info_error=error) if mqtt else Machine(
            image, rssi=rssi, ap_info_error=error)
        wire = packet(crypt(body) if encrypted else body,
                      0x4022 if encrypted else 0x0022, function=0x10)
        if mqtt:
            machine.receive_json(machine.envelope(wire))
        else:
            machine.receive_ble(wire)
        assert machine.ap_info_calls == 1
        assert len(machine.reply_frames) == len(machine.transmitted) == 1
        result = assert_response(machine, encrypted=encrypted, mqtt=mqtt, rssi=rssi, error=error)
        results.append({"case": "complete_route", "transport": "mqtt" if mqtt else "ble",
            "incoming_gcm": encrypted, "body_kind": label,
            "synthetic_rssi": rssi, "ap_info_error": error,
            "parser_reply_context": machine.reply_contexts[0], **result})

    rejects = ("missing_account", "missing_serial", "wrong_account", "wrong_serial",
        "account_prefix", "serial_prefix", "missing_sess_id", "missing_client_id",
        "missing_msg_seq", "missing_cmd", "missing_cmd_status", "missing_timestamp",
        "missing_version", "cmd_status_zero", "cmd_status_three")
    for case in rejects:
        machine = NativeMachine(image)
        envelope = machine.envelope(packet(b"\xa1\1\x22", 0x22, function=16))
        payload = json.loads(envelope["payload"])
        if case == "missing_account":
            del payload["account_id"]
        elif case == "missing_serial":
            del payload["device_sn"]
        elif case == "wrong_account":
            payload["account_id"] = "different-synthetic-account"
        elif case == "wrong_serial":
            payload["device_sn"] = "DIFFERENT0000001"
        elif case == "account_prefix":
            payload["account_id"] = payload["account_id"][:-1]
        elif case == "serial_prefix":
            payload["device_sn"] = payload["device_sn"][:-1]
        elif case.startswith("missing_"):
            del envelope["head"][case[8:]]
        else:
            envelope["head"]["cmd_status"] = 0 if case.endswith("zero") else 3
        envelope["payload"] = json.dumps(payload)
        machine.receive_json(envelope)
        assert machine.ap_info_calls == 0 and not machine.reply_frames and not machine.publications
        results.append({"case": "native_admission", "mutation": case,
            "rssi_observations": 0, "reply_count": 0,
            "substituted_identity_refresh_calls": machine.refresh_requests})

    for case in ("no_sign_code", "changed_sign_code", "different_client_id",
                 "irrelevant_head_identity", "older_timestamp"):
        machine = NativeMachine(image)
        envelope = machine.envelope(packet(b"\xa1\1\x22", 0x22, function=16))
        if case == "no_sign_code":
            del envelope["head"]["sign_code"]
        elif case == "changed_sign_code":
            envelope["head"]["sign_code"] = 99
        elif case == "different_client_id":
            envelope["head"]["client_id"] = "another-synthetic-client"
        elif case == "irrelevant_head_identity":
            envelope["head"].update(device_sn="WRONG_SYNTHETIC", account_id="wrong-synthetic")
        else:
            machine.uc.mem_write(0x3FC903EC, struct.pack("<I", 1700000001))
        machine.receive_json(envelope)
        assert machine.ap_info_calls == 1 and not machine.refresh_requests
        result = assert_response(machine, encrypted=False, mqtt=True, rssi=-70, error=0)
        results.append({"case": "native_metadata", "mutation": case,
            "last_request_timestamp": machine.u32(0x3FC903EC), **result})

    for mqtt in (False, True):
        machine = NativeMachine(image) if mqtt else Machine(image)
        for index, rssi in enumerate((-80, -70, -50)):
            machine.rssi = rssi
            wire = packet(b"\xa1\1\x22", 0x22, function=16)
            if mqtt:
                machine.receive_json(machine.envelope(wire))
            else:
                machine.receive_ble(wire)
            assert_response(machine, encrypted=False, mqtt=mqtt, rssi=rssi, error=0, index=index)
        assert machine.ap_info_calls == 3
        results.append({"case": "repeated_observations", "transport": "mqtt" if mqtt else "ble",
            "synthetic_rssi_sequence": [-80, -70, -50], "ap_info_calls": 3,
            "reply_count": 3, "repeat_does_not_return_cached_rssi": True})

    machine = NativeMachine(image)
    machine.uc.mem_write(0x3FC8A0EC, bytes(4))
    machine.receive_json(machine.envelope(packet(b"\xa1\1\x22", 0x22, function=16)))
    assert machine.ap_info_calls == 0 and not machine.publications
    results.append({"case": "mqtt_uninitialized", "rssi_observations": 0, "reply_count": 0})
    assert len(results) == 71
    return {"cases_passed": len(results), "cases": results, "hardware_access": False,
            "registrations": machine.registrations,
            "transport_callbacks": {"0": "4203fcf0", "5": "4203c98a"},
            "native_raw_callback": "42043a18", "limitations": __doc__.strip()}


def main():
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = Path(os.environ.get("SOLIX_FIRMWARE_DIR", Path(__file__).resolve().parents[2]
                                   / "firmware/c1000_gen2/1.1.4.9"))
    image = (args.image or directory / IMAGE_NAME).read_bytes()
    assert len(image) == 1482800 and hashlib.sha256(image).hexdigest() == IMAGE_SHA256
    results = suite(image)
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "radio-rssi-routes-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n")
    result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_radio_factory_routes.py")
    manifest = {"firmware": {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256, "bytes": len(image)},
        "cases_passed": results["cases_passed"], "hardware_access": False,
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
                    "cryptography": cryptography.__version__},
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in sources},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "substitution_limits": __doc__.strip()}
    output = args.output / "radio-rssi-routes-manifest.json"
    output.write_text(json.dumps(manifest, indent=2)+"\n")
    output.chmod(0o600)
    print(json.dumps({"cases_passed": results["cases_passed"], "hardware_access": False}))


if __name__ == "__main__":
    main()
