#!/usr/bin/env python3
"""Offline BLE routes for Gen 2 radio clock004a/status004b, with MQTT cutoff.

Runs actual radio startup registration, BLE receive/parser/dispatch, both
handlers/getters, clock selector, response framing and BLE callback selection.
MQTT raw callback runs only to its controller-forward wrapper entry; that
boundary is captured and substituted, with no UART or controller execution.
OS/libc, session keys/GCM, time/year, optional binding provider and BLE send
are host substitutes. All state and frames are synthetic; no hardware/network.
"""
from __future__ import annotations

import argparse
import datetime as dt
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

from emulate_radio_rssi_routes import Machine as RssiRoutes
from emulate_radio_factory_routes import packet, crypt, IMAGE_NAME, IMAGE_SHA256
from emulate_radio_status_features import TZ_RULES, TZ_TEXT, CALLBACK

os.umask(0o077)


class Machine(RssiRoutes):
    def __init__(self, image: bytes):
        self.forward_attempts = []
        self.handler_calls = []
        self.time_calls = 0
        self.year_calls = 0
        self.times = [1790812800]
        self.year = 2026
        self.binding_callback = 0
        self.allow_cache_refresh = False
        super().__init__(image)
        self.uc.mem_write(CALLBACK, b"\x13\0\0\0")
        table = dict(struct.unpack("<II", self.read(0x3C147BE4+i*8, 8)) for i in range(42))
        assert table[0x4A] == 0x4203FAC0 and table[0x4B] == 0x4203DA2C
        self.seed_status()
        self.seed_clock()

    def string(self, address):
        for size in range(256):
            if self.read(address+size, 1) == b"\0":
                return self.read(address, size)
        raise AssertionError("Unterminated synthetic string")

    def write_guard(self, uc, access, address, size, value, data):
        if self.allow_cache_refresh and any(
                TZ_RULES+i*40+0x20 <= address < address+size <= TZ_RULES+i*40+0x26
                for i in range(2)):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2, a3 = args[:4]
        if address == 0x420439CC:
            # Stop at the radio forwarding wrapper, before its send builder.
            self.forward_attempts.append({"function": a0, "command": a1,
                                          "body": self.read(a2, a3).hex()})
            result = 0
        elif address in (0x4203FAC0, 0x4203DA2C):
            self.handler_calls.append(f"{address:08x}")
            return
        elif address == 0x42115E56:
            assert a0 == 0
            result = self.times[min(self.time_calls, len(self.times)-1)]
            self.time_calls += 1
        elif address == 0x4203A3F2:
            self.write_guard(uc, 0, a1, 2, self.year, None)
            uc.mem_write(a1, struct.pack("<H", self.year))
            self.year_calls += 1
            result = 0
        elif address == CALLBACK:
            result = self.binding_callback
        elif address == 0x40000360:
            left, right = self.read(a0, a2), self.read(a1, a2)
            result = (left > right) - (left < right)
        elif address == 0x40000404:
            result = min(len(self.string(a0)), a1)
        elif any(lo <= address < hi for lo, hi in (
            (0x4203DA2E, 0x4203DB1E), (0x4203FAC2, 0x4203FB38),
            (0x4202BEBA, 0x4202BED4), (0x42028D8E, 0x42028D92),
            (0x420379EA, 0x420379F4), (0x4201874C, 0x42018758),
            (0x42035DA4, 0x42035DB4), (0x42035DCE, 0x42035DDE),
            (0x42035E30, 0x4203604E), (0x420365F0, 0x4203664C))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def seed_status(self, bind=1, ble=3, mqtt=1, *, callback=False):
        self.uc.mem_write(0x3FC8AAD0, struct.pack("<I", CALLBACK if callback else 0))
        self.uc.mem_write(0x3FC8AB70, bytes((bind,)))
        self.uc.mem_write(0x3FC90534, bytes((ble,)))
        self.uc.mem_write(0x3FC9036C, bytes((mqtt,)))
        self.binding_callback = bind
        self.status_expected = b"\0\xa1\1" + bytes((bind if callback else int(bool(bind)),))
        self.status_expected += b"\xa2\1" + bytes((int(ble == 3),)) + b"\xa3\1" + bytes((mqtt,))

    def seed_clock(self, epoch=1790812800, *, timezone="SYN0", offsets=(-3600, -3600),
                   second_time=None, refresh=False):
        self.times = [epoch] if second_time is None else [epoch, second_time]
        self.time_calls = self.year_calls = 0
        self.allow_cache_refresh = refresh
        self.uc.mem_write(TZ_TEXT, timezone.encode()+b"\0")
        for i, offset in enumerate(offsets):
            base = TZ_RULES+i*40
            self.uc.mem_write(base, bytes(40))
            self.uc.mem_write(base+0x1C, struct.pack("<i", offset))
            self.uc.mem_write(base+0x20, struct.pack("<I", (100, 200)[i]))
            self.uc.mem_write(base+0x24, struct.pack("<H", 2025 if refresh else 2026))
            if refresh:
                self.uc.mem_write(base+0x0C, struct.pack("<I", 1))
                self.uc.mem_write(base+0x14, struct.pack("<H", (100, 300)[i]))
        if timezone == "GMT0":
            offset = 1
        elif offsets[0] == offsets[1]:
            offset = -offsets[0]
        else:
            transition = (100, 200)
            if refresh:
                start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp())
                transition = tuple(start+(day-1)*86400-old for day, old in zip((100, 300), offsets))
            current = epoch if second_time is None else second_time
            offset = -offsets[int(transition[0] <= current < transition[1])]
        self.clock_expected = b"\0\xa1\4"+struct.pack("<I", epoch & 0xFFFFFFFF)
        self.clock_expected += b"\xa2\4"+struct.pack("<I", offset & 0xFFFFFFFF)

    def ble_query(self, command, body=b"", *, encrypted=False):
        before = self.read(0x3FC80000, 0x20000)
        wire = packet(crypt(body) if encrypted else body, command | (0x4000 if encrypted else 0),
                      function=0x10)
        self.receive_ble(wire)
        expected = self.clock_expected if command == 0x4A else self.status_expected
        assert len(self.handler_calls) == len(self.reply_frames) == len(self.transmitted) == 1
        reply = self.reply_frames[0]
        assert reply["port"] == 0
        assert reply["wire"] == packet(crypt(expected) if encrypted else expected,
            command | (0x4800 if encrypted else 0x0800), function=0x10)
        assert self.transmitted[0]["transport"] == "ble"
        assert self.transmitted[0]["wire"] == reply["wire"]
        assert not self.builders and not self.egress and not self.forward_attempts
        assert self.ap_info_calls == 0
        after = self.read(0x3FC80000, 0x20000)
        changed = [0x3FC80000+i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        allowed = set(range(0x3FC905EC, 0x3FC905F4)) | set(range(0x3FC906D0, 0x3FC906DC))
        if self.allow_cache_refresh:
            allowed |= {TZ_RULES+i*40+offset for i in range(2) for offset in range(0x20, 0x26)}
        assert set(changed) <= allowed
        return {"command": f"{command:04x}", "function": "10", "gcm": encrypted,
                "response_command": f"{command | (0x4800 if encrypted else 0x0800):04x}",
                "response_plain_body": expected.hex(), "reply_port": 0,
                "time_service_calls": self.time_calls, "calendar_service_calls": self.year_calls,
                "changed_global_addresses": [f"{address:08x}" for address in changed],
                "controller_forward_attempts": 0}


def suite(image):
    results = []
    bodies = {"empty": b"", "source21": b"\xa1\1\x21",
              "source22_timestamp": b"\xa1\1\x22\xfe\5\3"+struct.pack("<I", 1700000000),
              "unused_tag": b"\xdf\1\x5a"}
    for command, encrypted, (name, body) in itertools.product((0x4A, 0x4B), (False, True), bodies.items()):
        machine = Machine(image)
        results.append({"case": "ble_complete_route", "payload_fixture": name,
                        **machine.ble_query(command, body, encrypted=encrypted)})
    for encrypted, values in itertools.product((False, True), ((0, 2, 0, False), (2, 3, 255, True))):
        machine = Machine(image)
        bind, ble, mqtt, callback = values
        machine.seed_status(bind, ble, mqtt, callback=callback)
        results.append({"case": "status_observation", "synthetic_bind": bind,
            "synthetic_ble_state": ble, "synthetic_mqtt_flag": mqtt,
            "binding_provider_substituted": callback,
            **machine.ble_query(0x4B, encrypted=encrypted)})
    for encrypted, fixture in itertools.product((False, True),
            ("gmt_sentinel", "zero_epoch", "invalid_epoch", "transition_between_samples", "stale_year")):
        machine = Machine(image)
        if fixture == "gmt_sentinel":
            machine.seed_clock(timezone="GMT0", offsets=(0, 0))
        elif fixture == "zero_epoch":
            machine.seed_clock(epoch=0)
        elif fixture == "invalid_epoch":
            machine.seed_clock(epoch=0xFFFFFFFF)
        elif fixture == "transition_between_samples":
            machine.seed_clock(epoch=99, second_time=100, offsets=(-3600, -7200))
        else:
            machine.seed_clock(offsets=(-3600, -7200), refresh=True)
        results.append({"case": "clock_observation", "fixture": fixture,
                        **machine.ble_query(0x4A, encrypted=encrypted)})
    for command, encrypted, source in itertools.product((0x4A, 0x4B), (False, True), (0x21, 0x22)):
        machine = Machine(image)
        body = bytes((0xA1, 1, source))
        payload = crypt(body) if encrypted else body
        wire = packet(payload, command | (0x4000 if encrypted else 0), function=0x10)
        machine.receive(wire, mqtt=True)
        assert machine.forward_attempts == [{"function": 16, "command": command, "body": payload.hex()}]
        assert not machine.handler_calls and not machine.reply_frames and not machine.transmitted
        assert not machine.builders and not machine.egress
        assert not machine.visited["host_gcm_decrypt"]
        assert machine.time_calls == 0
        results.append({"case": "mqtt_high_command_boundary", "command": f"{command:04x}",
            "gcm_marker": encrypted, "synthetic_source": source,
            "normalized_function": "10", "forward_wrapper_called": True,
            "payload_unchanged": True, "radio_handler_calls": 0,
            "gcm_decrypt_calls": 0, "controller_or_uart_executed": False})
    for command, encrypted in itertools.product((0x84A, 0x84B), (False, True)):
        machine = Machine(image)
        body = b"\xa1\1\x22"
        machine.receive_ble(packet(crypt(body) if encrypted else body,
            command | (0x4000 if encrypted else 0), function=0x10))
        assert not machine.handler_calls and not machine.reply_frames and not machine.forward_attempts
        results.append({"case": "reply_opcode_not_a_query", "command": f"{command:04x}",
            "gcm": encrypted, "radio_handler_calls": 0, "response_count": 0})
    assert len(results) == 42
    return {"cases_passed": len(results), "cases": results, "hardware_access": False,
            "limits": __doc__.strip()}


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
    result_path = args.output / "radio-status-routes-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n")
    result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_radio_rssi_routes.py",
               "emulate_radio_factory_routes.py", "emulate_radio_status_features.py")
    manifest = {"firmware": {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256, "bytes": len(image)},
        "cases_passed": results["cases_passed"], "hardware_access": False,
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
                    "cryptography": cryptography.__version__},
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in sources},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "substitution_limits": __doc__.strip()}
    output = args.output / "radio-status-routes-manifest.json"
    output.write_text(json.dumps(manifest, indent=2)+"\n")
    output.chmod(0o600)
    print(json.dumps({"cases_passed": results["cases_passed"], "hardware_access": False}))


if __name__ == "__main__":
    main()
