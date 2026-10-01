#!/usr/bin/env python3
"""Replay radio link/MQTT status and clock getters in public A1763 radio 0.3.3.0.

Actual handlers, simple state getters, timezone selector and raw TLV serializer
execute. ROM libc, time/calendar observations, optional binding-provider callback,
logging and final response transport are explicit host substitutes. No network,
device, output command, credential read, binding change or persistence backend.
External request reachability is not established by these direct-handler cases.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn import riscv_const as R


SHA256 = "e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8"
STOP, CALLBACK, STACK = 0x50000000, 0x50000020, 0x3FD0F000
TZ_TEXT, TZ_RULES = 0x3FC82F24, 0x3FC8AF2C


class Machine:
    def __init__(self, image: bytes):
        self.uc = unicorn.Uc(unicorn.UC_ARCH_RISCV, unicorn.UC_MODE_RISCV32)
        for address, size in ((0x3C130000, 0x40000), (0x3FC80000, 0x90000),
                              (0x40380000, 0x10000), (0x42000000, 0x200000),
                              (0x40000000, 0x1000), (STOP, 0x1000), (0x20000000, 0x10000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(0x40000000, b"\x13\0\0\0" * 1024)
        offset = 24
        for _ in range(image[1]):
            address, size = struct.unpack_from("<II", image, offset)
            offset += 8
            self.uc.mem_write(address, image[offset:offset + size])
            offset += size
        self.uc.mem_write(STOP, b"\x13\0\0\0" * 16)
        self.uc.reg_write(R.UC_RISCV_REG_SP, STACK)
        self.uc.reg_write(R.UC_RISCV_REG_GP, 0x3FC83000)
        self.responses = []
        self.visited = set()
        self.writes = set()
        self.response_error = 0
        self.bind_callback = 0
        self.times = [1790812800]
        self.time_calls = 0
        self.year_calls = 0
        self.year = 2026
        self.allow_cache_refresh = False
        self.stopped = False
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.step)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.guard)

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def string(self, address: int) -> bytes:
        data = bytearray()
        for _ in range(256):
            byte = self.read(address + len(data), 1)[0]
            if not byte:
                return bytes(data)
            data.append(byte)
        raise AssertionError("Unterminated synthetic string")

    def guard(self, uc, _access, address, size, _value, _data):
        allowed = [(STACK - 0x1000, STACK)]
        if self.allow_cache_refresh:
            allowed += [(TZ_RULES + i * 40 + 0x20, TZ_RULES + i * 40 + 0x26) for i in range(2)]
        assert any(lo <= address < address + size <= hi for lo, hi in allowed), (
            f"Unexpected write {address:08x} at {uc.reg_read(R.UC_RISCV_REG_PC):08x}")
        self.writes.update(range(address, address + size))

    def write(self, address: int, value: bytes):
        if value:
            self.guard(self.uc, 0, address, len(value), 0, None)
            self.uc.mem_write(address, value)

    def step(self, uc, address, _size, _data):
        self.visited.add(address)
        a0, a1, a2 = [uc.reg_read(R.UC_RISCV_REG_A0 + i) for i in range(3)]
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
            return
        if address in (0x4202D3B0, 0x420232B8, 0x42023636, 0x421271AC, 0x421270B8):
            result = 0
        elif address == 0x40000358:
            self.write(a0, self.read(a1, a2))
            result = a0
        elif address == 0x40000360:
            # This call is the four-byte comparison against literal GMT0.
            left, right = self.read(a0, a2), self.read(a1, a2)
            result = (left > right) - (left < right)
        elif address == 0x40000404:
            # ROM strnlen, not strchr: a1 is the maximum character count.
            result = min(len(self.string(a0)), a1)
        elif address == 0x42115E56:
            assert a0 == 0
            result = self.times[min(self.time_calls, len(self.times) - 1)]
            self.time_calls += 1
        elif address == 0x4203A3F2:
            # Calendar conversion observation boundary: caller uses year u16.
            self.write(a1, struct.pack("<H", self.year))
            self.year_calls += 1
            result = 0
        elif address == CALLBACK:
            result = self.bind_callback
        elif address == 0x4204FA68:
            self.responses.append(self.read(a1, a2))
            result = self.response_error
        elif any(lo <= address < hi for lo, hi in (
                (0x4203DA2C, 0x4203DB1E), (0x4203FAC0, 0x4203FB38),
                (0x4202BEBA, 0x4202BED4), (0x42028D8E, 0x42028D92),
                (0x420379EA, 0x420379F4), (0x4201874C, 0x42018758),
                (0x42035DA4, 0x42035DB4), (0x42035DCE, 0x42035DDE),
                (0x42035E30, 0x4203604E), (0x420365F0, 0x4203664C),
                (0x4204DD4C, 0x4204DDF4))):
            return
        else:
            raise AssertionError(f"Unexpected instruction {address:08x}")
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def run(self, handler: int) -> dict[int, bytes]:
        before = self.read(0x3FC80000, 0x20000)
        self.uc.reg_write(R.UC_RISCV_REG_A0, 0x20000000)  # Synthetic transport context.
        self.uc.reg_write(R.UC_RISCV_REG_RA, STOP)
        self.uc.emu_start(handler, 0, count=20000)
        assert self.stopped and len(self.responses) == 1
        after = self.read(0x3FC80000, 0x20000)
        changed = [0x3FC80000 + i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert all(address in self.writes for address in changed)
        if not self.allow_cache_refresh:
            assert not changed
        raw = self.responses[0]
        assert raw[0] == 0
        fields, offset = {}, 1
        while offset < len(raw):
            tag, size = raw[offset:offset + 2]
            fields[tag] = raw[offset + 2:offset + 2 + size]
            offset += size + 2
        assert offset == len(raw)
        return fields


def status_case(image: bytes, bind: int, ble: int, mqtt: int, *, callback=False, error=0) -> dict:
    m = Machine(image)
    m.uc.mem_write(0x3FC8AAD0, struct.pack("<I", CALLBACK if callback else 0))
    m.uc.mem_write(0x3FC8AB70, bytes((bind,)))
    m.uc.mem_write(0x3FC90534, bytes((ble,)))
    m.uc.mem_write(0x3FC9036C, bytes((mqtt,)))
    m.bind_callback, m.response_error = bind, error
    fields = m.run(0x4203DA2C)
    assert fields == {0xA1: bytes((bind if callback else int(bool(bind)),)),
                      0xA2: bytes((int(ble == 3),)), 0xA3: bytes((mqtt,))}
    return {"case": "link_status_004b", "bind_observation": bind, "ble_state": ble,
            "mqtt_connected_byte": mqtt, "binding_callback_substituted": callback,
            "transport_result": error, "reply": m.responses[0].hex(),
            "global_state_preserved": True, "response_attempts": 1}


def clock_case(image: bytes, *, epoch: int, offsets: tuple[int, int], timezone: str,
               transition: tuple[int, int] = (100, 200), second_time=None,
               cache_refresh=False, error=0) -> dict:
    m = Machine(image)
    m.times = [epoch] if second_time is None else [epoch, second_time]
    m.response_error = error
    m.allow_cache_refresh = cache_refresh
    m.uc.mem_write(TZ_TEXT, timezone.encode() + b"\0")
    for i, offset in enumerate(offsets):
        base = TZ_RULES + 40 * i
        m.uc.mem_write(base, bytes(40))
        m.uc.mem_write(base + 0x1C, struct.pack("<i", offset))
        if cache_refresh:
            # Simple Julian-day transition mode, day 100 / day 300, midnight.
            m.uc.mem_write(base + 0x0C, struct.pack("<I", 1))
            m.uc.mem_write(base + 0x14, struct.pack("<H", (100, 300)[i]))
            m.uc.mem_write(base + 0x24, struct.pack("<H", 2025))
        else:
            m.uc.mem_write(base + 0x20, struct.pack("<I", transition[i]))
            m.uc.mem_write(base + 0x24, struct.pack("<H", 2026))
    fields = m.run(0x4203FAC0)
    now = epoch if second_time is None else second_time
    if timezone == "GMT0":
        expected_offset = 1  # Actual -1 getter sentinel negated by handler.
    elif offsets[0] == offsets[1]:
        expected_offset = -offsets[0]
    else:
        if cache_refresh:
            year_start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp())
            transition = tuple(year_start + (day - 1) * 86400 - offset
                               for day, offset in zip((100, 300), offsets))
            for i, expected in enumerate(transition):
                assert int.from_bytes(m.read(TZ_RULES + i * 40 + 0x20, 4), "little") == expected
                assert m.read(TZ_RULES + i * 40 + 0x24, 2) == struct.pack("<H", 2026)
        first, last = transition
        index = int(first <= now < last) if last >= first else int(now < last or now >= first)
        expected_offset = -offsets[index]
    assert fields == {0xA1: struct.pack("<I", epoch & 0xFFFFFFFF),
                      0xA2: struct.pack("<I", expected_offset & 0xFFFFFFFF)}
    return {"case": "clock_004a", "epoch_observation": epoch,
            "second_time_observation": second_time, "timezone_fixture": timezone,
            "stored_offset_seconds": list(offsets), "expected_reply_offset": expected_offset,
            "transition_epochs": list(transition), "calendar_cache_refresh": cache_refresh,
            "time_service_calls": m.time_calls, "calendar_service_calls": m.year_calls,
            "transport_result": error, "response_attempts": 1,
            "reply": m.responses[0].hex(), "settings_or_connection_writes": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions are required")
    image = args.image.read_bytes()
    assert hashlib.sha256(image).hexdigest() == SHA256
    machine = Machine(image)
    inventory = [struct.unpack("<II", machine.read(0x3C147BE4 + i * 8, 8)) for i in range(42)]
    assert dict(inventory)[0x4B] == 0x4203DA2C and dict(inventory)[0x4A] == 0x4203FAC0
    cases = [status_case(image, *values) for values in itertools.product((0, 1, 255), (0, 1, 2, 3, 4, 255), (0, 1, 2, 255))]
    cases += [status_case(image, value, 3, 1, callback=True) for value in (0, 1, 2, 255)]
    cases += [status_case(image, 1, 3, 1, error=error) for error in (1, 0xFFFFFFFF)]
    for epoch, offset in itertools.product((0, 1790812800, 0xFFFFFFFF), (0, -3600, -7200, 18000)):
        cases.append(clock_case(image, epoch=epoch, offsets=(offset, offset), timezone="SYN0"))
    cases.append(clock_case(image, epoch=1790812800, offsets=(0, 0), timezone="GMT0"))
    for epoch, transition in itertools.product((99, 100, 101, 199, 200, 201), ((100, 200), (200, 100))):
        cases.append(clock_case(image, epoch=epoch, offsets=(-3600, -7200), timezone="SYN0", transition=transition))
    cases.append(clock_case(image, epoch=99, second_time=100, offsets=(-3600, -7200), timezone="SYN0"))
    cases.append(clock_case(image, epoch=1790812800, offsets=(-3600, -7200), timezone="SYN0", cache_refresh=True))
    cases.append(clock_case(image, epoch=1790812800, offsets=(-3600, -3600), timezone="SYN0", error=1))
    result = {"firmware_sha256": SHA256, "model": "A1763 C1000 Gen 2 radio 0.3.3.0",
              "cases_passed": len(cases), "dispatch_inventory": [{"command": f"{cmd:04x}", "handler": f"{fn:08x}"} for cmd, fn in inventory],
              "cases": cases, "limits": __doc__.strip()}
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(encoded)
    args.manifest.write_text(json.dumps({"script": Path(__file__).name,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "firmware_sha256": SHA256, "results_sha256": hashlib.sha256(encoded).hexdigest(),
        "unicorn_version": unicorn.__version__, "cases_passed": len(cases),
        "fixture_source": "Public firmware and synthetic state/time observations only"}, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases_passed": len(cases), "firmware_sha256": SHA256}))


if __name__ == "__main__":
    main()
