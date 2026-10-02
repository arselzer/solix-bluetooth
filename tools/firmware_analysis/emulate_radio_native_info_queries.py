#!/usr/bin/env python3
"""Offline A1763 native MAC/region query and private wireless-text schema.

Runs JSON admission, local function10 dispatch, opcode0002, selected MAC/region
wrappers, opcode0003 text wrapper, reply framing and native JSON/topic builders.
OS/libc/JSON/base64, established crypto/session, MAC/driver/config observations,
country-file reads/mutexes, Wi-Fi initialization, the different function-0f
dispatcher boundary and final publish are explicit
substitutes. Only synthetic identities, text, MAC bytes and stale scratch exist.
No device, network, SSH, cloud, credentials, controller or physical radio action.
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

from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256, packet
from emulate_radio_rssi_routes import NativeMachine

os.umask(0o077)
COUNTRY_CACHE, COUNTRY_MUTEX = 0x3FC8ADC4, 0x3FC904E0
WIFI_INITIALIZED = 0x3FC9049C
COUNTRY_MAGIC = 0x55AAAA55
STALE_MAC = bytes.fromhex("02aabbccddee")
STALE_TEXT = b"STALEHEX0001"
PROTECTED = ((0x3FC86FA8, 0x368), (0x3FC86C40, 0x368),
             (0x3FC89EB4, 0x154), (0x3FC8B5BC, 0x310))


class Machine(NativeMachine):
    def __init__(self, image, *, mac_error=0, country=b"01", warm_country=True,
                 country_file_error=0, wifi_initialized=True,
                 configured_text=b"synthetic-config-net", config_error=0,
                 cached_text=b"synthetic-cached-net", **kwargs):
        self.handlers, self.effects, self.selectors, self.forward_attempts = [], [], [], []
        self.different_dispatch = []
        self.mac_error, self.country = mac_error, country
        self.country_file_error = country_file_error
        self.country_file = struct.pack("<I", COUNTRY_MAGIC) + country + b"\0"
        self.country_file = self.country_file.ljust(0x54, b"\0")
        self.configured_text, self.config_error = configured_text, config_error
        super().__init__(image, **kwargs)
        table = dict(struct.unpack("<II", self.read(0x3C147BE4 + i * 8, 8)) for i in range(42))
        assert {key: table[key] for key in (2, 3, 0x22)} == {
            2: 0x4203CECE, 3: 0x4203CC10, 0x22: 0x4203CB26}
        self.uc.mem_write(WIFI_INITIALIZED, bytes((int(wifi_initialized),)))
        self.uc.mem_write(COUNTRY_MUTEX, struct.pack("<I", 1 if warm_country else 0))
        self.uc.mem_write(COUNTRY_CACHE, self.country_file if warm_country else bytes(0x54))
        self.uc.mem_write(0x3FC8AD98, cached_text + b"\0")
        self.uc.mem_write(0x3FC90645, b"\x07")
        self.uc.mem_write(0x3FC90646, b"\x00")
        self.uc.mem_write(0x3FC90647, b"\x01")
        self.uc.mem_write(0x3FC904C5, b"\x00")
        self.uc.mem_write(0x3FC8ACF4, bytes(4))
        self.uc.mem_protect(0x42000000, 0x200000, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.uc.mem_protect(0x3C130000, 0x40000, unicorn.UC_PROT_READ)

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address + size <= hi for lo, hi in (
            (COUNTRY_CACHE, COUNTRY_CACHE + 0x54),
            (COUNTRY_MUTEX, COUNTRY_MUTEX + 4))):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def write(self, address, data):
        self.write_guard(self.uc, 0, address, len(data), 0, None)
        self.uc.mem_write(address, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(8)]
        a0, a1, a2, a3 = args[:4]
        if address == 0x40000368:
            # ROM strncpy used by the country wrapper, matching the existing
            # identity replay. Warm country text copies eight bytes; the
            # error fallback copies four from its read-only "01" constant.
            assert a2 in (4, 8), args
            self.write(a0, self.string(a1)[:a2].ljust(a2, b"\0"))
            result = a0
        elif address == 0x4203CECE:
            self.handlers.append("0002")
            # Model data left by an earlier operation in the handler's scratch.
            # These two areas are not initialized by the actual query prologue.
            stack = uc.reg_read(R.UC_RISCV_REG_SP) - 0x60
            self.write(stack + 0x1c, STALE_MAC)
            self.write(stack + 0x24, STALE_TEXT)
            return
        elif address == 0x4203CF36:
            self.selectors.append(uc.reg_read(R.UC_RISCV_REG_S1))
            return
        elif address in (0x4203CC10, 0x4203CB26):
            self.handlers.append("0003" if address == 0x4203CC10 else "0022")
            return
        elif address == 0x420A4CC0:
            assert a1 in (0, 2)
            self.effects.append({"effect": "mac_observation", "interface_type": a1,
                                 "service_result": self.mac_error})
            if not self.mac_error:
                self.write(a0, bytes.fromhex("020000000002" if a1 == 0 else "020000000001"))
            result = self.mac_error
        elif address == 0x42030A06:
            self.effects.append({"effect": "wifi_initialization_boundary",
                                 "whole_initialization_substituted": True})
            result = 0
        elif address in (0x4202D6D2, 0x4202D6F8, 0x4202D71C):
            operation = {0x4202D6D2: "mutex_create", 0x4202D6F8: "mutex_take",
                         0x4202D71C: "mutex_release"}[address]
            self.effects.append({"effect": operation, "country_cache": True})
            if address == 0x4202D6D2:
                assert a0 == COUNTRY_MUTEX
                self.write(a0, struct.pack("<I", 1))
            result = 0
        elif address == 0x4201514A:
            assert self.string(a0) == b"wifi_country_code" and a1 == COUNTRY_MAGIC and a3 == 0x54
            self.effects.append({"effect": "country_file_read", "file": "wifi_country_code",
                                 "result": self.country_file_error})
            if not self.country_file_error:
                self.write(a2, self.country_file)
            result = self.country_file_error
        elif address == 0x4204BC48 and self.string(a2) == b"%s":
            value = self.string(a3)
            self.write(a0, value[:max(0, a1 - 1)] + b"\0")
            result = len(value)
        elif address == 0x42030B8A:
            assert len(self.configured_text) <= 32
            record = self.configured_text.ljust(32, b"\0") + b"synthetic-password".ljust(64, b"\0")
            self.write(a0, record.ljust(0xb4, b"\0"))
            self.effects.append({"effect": "wifi_config_provider_boundary",
                                 "configured_text_bytes": len(self.configured_text),
                                 "result": self.config_error})
            result = self.config_error
        elif address == 0x420439CC:
            self.forward_attempts.append({"function": a0, "command": a1})
            result = 0
        elif address == 0x42045792:
            # Startup binds function 0f to this separate command dispatcher.
            # Stop at that boundary: its controller handling is not this audit.
            self.different_dispatch.append({"address": "42045792", "command": f"{a0:04x}"})
            result = 0
        elif any(lo <= address < hi for lo, hi in (
            (0x4203CECE, 0x4203CFC4), (0x42030EB2, 0x42030ED6),
            (0x42028D8A, 0x42028D8E), (0x4203701E, 0x42037036),
            (0x4201B1AA, 0x4201B1FE), (0x420114C0, 0x42011522),
            (0x420328A4, 0x4203298C), (0x4203CC10, 0x4203CD6E),
            (0x42030EE2, 0x42030F5E), (0x4203145C, 0x42031466),
            (0x4202BD9A, 0x4202BD9E), (0x42030E8E, 0x42030E98),
            (0x4202D3B0, 0x4202D3BC),
            (0x4204E046, 0x4204E0A0), (0x4204F95C, 0x4204F9A6))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xffffffff)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def protected(self):
        return [self.read(address, size) for address, size in PROTECTED]

    def query(self, command, body=b"", *, function=0x10, sequence=101):
        before = self.protected()
        wire = packet(body, command, function=function)
        envelope = self.envelope(wire)
        envelope["head"]["msg_seq"] = sequence
        envelope["head"]["sess_id"] = f"synthetic-request-{sequence}"
        self.receive_json(envelope)
        assert self.protected() == before
        assert not self.builders and not self.egress and not self.forward_attempts
        return wire

    def reply(self, command, *, index=0):
        reply = self.reply_frames[index]
        wire = reply["wire"]
        assert reply["port"] == 5
        assert wire[4:7] == bytes.fromhex("030010")
        assert int.from_bytes(wire[7:9], "big") == command | 0x0800
        body = wire[9:-1]
        fields, position = {}, 1
        while position < len(body):
            tag, length = body[position:position + 2]
            assert tag not in fields and position + 2 + length <= len(body)
            fields[tag] = body[position + 2:position + 2 + length]
            position += 2 + length
        assert position == len(body)
        publication = self.publications[index]
        assert publication["topic"] == "dt/anker_power/A1763/SYNTHETIC00000001/param_info"
        header = publication["envelope"]["head"]
        assert header["cmd"] == 16 and header["cmd_status"] == 1
        assert base64.b64decode(json.loads(publication["envelope"]["payload"])["data"]) == wire
        return {"reply_pattern": wire[4:7].hex(), "reply_command": f"{command | 0x0800:04x}",
                "body_hex": body.hex(), "status": body[0],
                "raw_fields": {f"{k:02x}": v.hex() for k, v in fields.items()},
                "wire_hex": wire.hex(), "native_reply_head": header,
                "reply_port": 5, "protected_identity_regions_unchanged": True}


def suite(image):
    rows = []
    for body in (b"", b"\xa2\x01\x00"):
        m = Machine(image)
        wire = m.query(2, body)
        reply = m.reply(2)
        assert reply["status"] == 1 and reply["raw_fields"] == {} and not m.effects
        rows.append({"case": "missing_mac_selector", "request_wire_hex": wire.hex(), **reply})
    for selector, error in itertools.product((0, 1, 2, 255), (0, 11)):
        m = Machine(image, mac_error=error)
        wire = m.query(2, bytes((0xa1, 1, selector)))
        reply = m.reply(2)
        assert reply["status"] == 0 and m.selectors == [selector]
        expected = (STALE_TEXT if selector == 0 else STALE_MAC.hex().upper().encode()) if error else (
            b"020000000001" if selector == 0 else b"020000000002")
        assert reply["raw_fields"]["a1"] == expected.hex()
        assert reply["raw_fields"].get("a2") == (b"01\0\0".hex() if selector else None)
        rows.append({"case": "mac_selector_and_provider_error", "selector": selector,
                     "mac_service_error": error, "request_wire_hex": wire.hex(),
                     "effects": m.effects, "synthetic_stale_scratch": bool(error), **reply})
    for warm, file_error, country in ((False, 0, b"DE"), (False, 1, b"DE"), (True, 0, b"ABCD")):
        m = Machine(image, warm_country=warm, country_file_error=file_error, country=country)
        cache_before = m.read(COUNTRY_CACHE, 0x54)
        m.query(2, b"\xa1\x01\x01")
        reply = m.reply(2)
        expected = bytearray((country + bytes(4))[:4] if not file_error else b"01\0\0")
        expected[2] = 0
        assert reply["raw_fields"]["a2"] == expected.hex()
        assert bool(m.read(COUNTRY_CACHE, 0x54) != cache_before) == (not warm and not file_error)
        rows.append({"case": "country_provider_cache_and_fallback", "warm_cache": warm,
                     "file_read_error": file_error, "synthetic_country": country.decode(),
                     "country_cache_changed": m.read(COUNTRY_CACHE, 0x54) != cache_before,
                     "effects": m.effects, **reply})
    m = Machine(image, wifi_initialized=False)
    m.query(2, b"\xa1\x01\x01")
    reply = m.reply(2)
    assert any(x["effect"] == "wifi_initialization_boundary" for x in m.effects)
    rows.append({"case": "wifi_uninitialized_query_reaches_initializer", "effects": m.effects,
                 "initializer_substituted_not_a_physical_test": True, **reply})
    m = Machine(image)
    m.query(2, b"\xa1\x00\xa2\x01\x00")
    reply = m.reply(2)
    assert m.selectors == [0xa2] and reply["status"] == 0
    rows.append({"case": "zero_length_selector_reads_next_tag", "observed_selector": m.selectors[0],
                 "strict_request_length_guard_required": True, **reply})

    # Distinct schema gap: execute the real 0003 text-copy wrapper with a config
    # provider boundary. Prior advertising replay replaced it with six MAC bytes.
    for configured, cached, error in (
            (b"synthetic-config-net", b"synthetic-cached-net", 0),
            (b"", b"", 0), (b"S" * 32, b"synthetic-cached-net", 0),
            (b"synthetic-config-net", b"", 1)):
        m = Machine(image, configured_text=configured, cached_text=cached, config_error=error)
        m.query(3)
        reply = m.reply(3)
        fields = reply["raw_fields"]
        assert fields["a4"] == (configured if not error else b"").hex()
        assert fields.get("a8") == (cached.hex() if cached else None)
        assert b"synthetic-password" not in bytes.fromhex(reply["body_hex"])
        assert fields["a1"] == "00" and fields["a2"] == "01" and fields["a3"] == "ba"
        rows.append({"case": "wireless_private_text_schema", "configured_text_bytes": len(configured),
                     "cached_text_present": bool(cached), "config_provider_error": error,
                     "password_in_reply": False, "effects": m.effects, **reply})

    # One mixed native sequence establishes distinct schemas/correlation. It
    # does not repeat the earlier RSSI observation/encryption/admission matrix.
    m = Machine(image)
    mixed = []
    for index, (command, body, sequence) in enumerate(((2, b"\xa1\x01\x00", 101), (3, b"", 203), (0x22, b"", 307))):
        m.query(command, body, sequence=sequence)
        reply = m.reply(command, index=index)
        header = reply["native_reply_head"]
        assert header["msg_seq"] != sequence and header["sess_id"] != f"synthetic-request-{sequence}"
        mixed.append({"request_sequence": sequence, **reply})
    assert mixed[1]["raw_fields"]["a3"] == "ba"
    assert mixed[2]["raw_fields"] == {"a1": "baffffff"}
    rows.append({"case": "mixed_native_query_schemas_and_non_echoed_correlation", "replies": mixed,
                 "request_head_sequence_or_session_not_echoed": True})

    for command in (2, 3, 0x22):
        m = Machine(image)
        m.query(command, b"\xa1\x01\x00" if command == 2 else b"", function=0x0f)
        assert not m.handlers and not m.reply_frames and not m.publications
        assert m.different_dispatch == [{"address": "42045792", "command": f"{command:04x}"}]
        rows.append({"case": "controller_function_enters_separate_dispatch_boundary",
                     "function": "0f", "command": f"{command:04x}", "query_handler_calls": 0,
                     "substituted_dispatch_boundary": m.different_dispatch,
                     "controller_effects_or_reply_after_boundary_not_tested": True})
    assert len(rows) == 23
    return {"cases_passed": len(rows), "hardware_access": False, "cases": rows,
            "firmware": {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256, "bytes": len(image)},
            "limitations": __doc__.strip()}


def main():
    if not __debug__:
        raise RuntimeError("Replay assertions are required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = Path(os.environ.get("SOLIX_FIRMWARE_DIR", Path(__file__).resolve().parents[2]
                                   / "firmware/c1000_gen2/1.1.4.9"))
    image = (args.image or directory / IMAGE_NAME).read_bytes()
    assert len(image) == 1482800 and hashlib.sha256(image).hexdigest() == IMAGE_SHA256
    result = suite(image)
    args.output.mkdir(parents=True, exist_ok=True)
    args.output.chmod(0o700)
    result_path = args.output / "radio-native-info-results.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    result_path.chmod(0o600)
    names = (Path(__file__).name, "emulate_radio_rssi_routes.py", "emulate_radio_factory_routes.py")
    manifest = {"firmware": result["firmware"], "cases_passed": result["cases_passed"],
                "hardware_access": False,
                "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
                            "cryptography": cryptography.__version__},
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in names},
                "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
                "substitution_limits": __doc__.strip(),
                "excluded": ["physical Wi-Fi/BLE/MAC services", "full Wi-Fi initialization",
                             "full Wi-Fi config provider including fallback", "flash writes",
                             "MQTT/TLS/session establishment", "controller/MCU commands",
                             "any real account, MAC, SSID, credential or phone capture"]}
    path = args.output / "radio-native-info-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"cases_passed": result["cases_passed"], "hardware_access": False,
                      "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
