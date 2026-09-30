"""Offline C1000 Gen 2 0091 clock-screen configuration and scheduling replay.

Runs actual ARM parser, command, settings, schedule, LCD-state, callback and
DA/readback instructions. Substitutes ACK/persistence/logging, software timer
operations, display-timer query, allocation and asset-transfer startup. RAM,
RTC and readiness are synthetic. No device, network or real display is used.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_clock_semantics import BUFFER, DESC, LENGTH, PAYLOAD
from emulate_soc_cap_handler import REQUEST, SocMachine
from emulate_tou_controller_encoding import tlv
from replay_io import FIRMWARE_SHA256, ROOT
import replay_io

THEME = 0x20001d8e
STAGED = 0x20002914
ASSET = 0x200027fa
STATUS = ASSET + 0x100
FLAGS = 0x20000164
LCD = 0x200042b0
READBACK = 0x21001800


def local_epoch(day: int = 28, hour: int = 12, minute: int = 0, *, year=2026) -> int:
    """Synthetic RTC calendar; September 28, 2026 is Monday."""
    return int(datetime(year, 9, day, hour, minute, tzinfo=timezone.utc).timestamp())


def byte(value: int) -> bytes:
    return bytes((1, value))


def short(value: int) -> bytes:
    return b"\x02" + struct.pack("<H", value)


class ClockScreenMachine(SocMachine):
    def __init__(self, *, ready=True, power_gate=True, display_timer=False,
                 rtc=None, busy=False, transfer_accept=True):
        super().__init__()
        self.uc.mem_write(THEME, bytes(0x118))
        self.uc.mem_write(ASSET, bytes(0x232))
        self.uc.mem_write(0x200007af, bytes((int(ready),)))
        self.uc.mem_write(0x200007b6, b"\x02")
        self.uc.mem_write(0x200004be, bytes((int(power_gate),)))
        self.uc.mem_write(FLAGS, struct.pack("<I", 0x33 | (int(busy) << 23)))
        self.uc.mem_write(0x20001d57, struct.pack("<H", 1200))
        self.uc.mem_write(0x20001d76, bytes((1, 2, 1, 0, 12, 3, 12, 24)))
        self.uc.mem_write(0x20000459, b"\x05\x06\x00")
        self.uc.mem_write(LCD + 3, b"\x01")
        self.set_rtc(local_epoch() if rtc is None else rtc)
        self.display_timer = display_timer
        self.transfer_accept = transfer_accept
        self.timer_calls = []
        self.transfers = []
        self.protected_settings = bytes(self.uc.mem_read(0x20001d48, THEME - 0x20001d48))
        self.protected_flags = int.from_bytes(self.uc.mem_read(FLAGS, 4), "little")

    def step(self, uc, address, size, data):
        r0, r1 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1))
        if address in (0x08010998, 0x0801095c):
            self.timer_calls.append(("stop" if address == 0x08010998 else "start", r0))
            self.back(1)
        elif address == 0x0801bf3c:
            self.back(int(self.display_timer))
        elif address == 0x0802c264:
            self.transfers.append({"pointer": r0, "callback": r1})
            self.back(int(self.transfer_accept))
        elif address == 0x0803124c:
            assert r0 == 256
            self.uc.mem_write(READBACK, bytes(512))
            self.back(READBACK)
        elif any(lo <= address < hi for lo, hi in (
            (0x0800c314, 0x0800c51e), (0x0801a5e8, 0x0801a5ec),
            (0x08005210, 0x080052de), (0x08005340, 0x08005354),
            (0x08005400, 0x08005438), (0x0800d1d8, 0x0800d1f4),
            (0x0802b39c, 0x0802b3ca), (0x0802b808, 0x0802b838),
            (0x08018848, 0x0801888e), (0x0801a808, 0x0801a9f0),
            (0x08015450, 0x0801546e), (0x08014710, 0x08014724),
            (0x0800fb2c, 0x0800fbca), (0x0800e654, 0x0800e672),
            (0x0800e6c0, 0x0800e716), (0x08018730, 0x08018818),
            (0x08029e18, 0x08029e6c), (0x0800b644, 0x0800b73e),
            (0x0802d2b8, 0x0802d32e), (0x0801883c, 0x08018844),
            (0x0802b36c, 0x0802b388),
        )):
            return
        else:
            super().step(uc, address, size, data)

    def set_rtc(self, epoch):
        self.uc.mem_write(0x40002818, struct.pack("<II", epoch >> 16, epoch & 0xffff))

    def protected(self):
        assert bytes(self.uc.mem_read(0x20001d48, THEME - 0x20001d48)) == self.protected_settings
        current = int.from_bytes(self.uc.mem_read(FLAGS, 4), "little")
        assert current & ~(1 << 20) == self.protected_flags

    def command(self, fields: dict[int, bytes]):
        self.acked = 0
        body = tlv(0xa1, b"\x22") + b"".join(tlv(tag, value) for tag, value in fields.items())
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800c314, REQUEST)
        assert self.acked == 1
        self.protected()
        return bytes(self.uc.mem_read(THEME, 0x118))

    def schedule(self):
        self.run(0x0801a808, 0)
        self.protected()
        return self.uc.reg_read(UC_ARM_REG_R0)

    def eligible(self):
        self.run(0x08018848, 0)
        self.protected()
        return self.uc.reg_read(UC_ARM_REG_R0)

    def timer(self):
        self.run(0x0802d2b8, 6)
        self.protected()
        return bool(int.from_bytes(self.uc.mem_read(FLAGS, 4), "little") & (1 << 20))

    def asset_callback(self, success):
        self.uc.reg_write(UC_ARM_REG_R1, 0)
        self.run(0x08029e18, int(success))
        self.protected()

    def da(self):
        self.uc.mem_write(BUFFER, bytes(128))
        self.uc.mem_write(LENGTH, b"\x00\x00")
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBx", 4, BUFFER, LENGTH, 128, 1))
        self.run(0x08018730, DESC)
        raw = bytes(self.uc.mem_read(BUFFER, 25))
        assert raw[:2] == b"\x18\x04"
        self.protected()
        return raw[1:]

    def readback(self):
        self.responses.clear()
        self.run(0x0800b644, REQUEST)
        assert len(self.responses) == 1
        self.protected()
        return self.responses[0]


def run_cases():
    results = []

    def record(name, **values):
        results.append({"case": name, **values, "protected_power_settings_unchanged": True})

    # Field-by-field writes execute the real TLV parser and 0091 handler.
    fields = {
        0xa2: (byte(0x83), 0x10, b"\x83"),
        0xa3: (b"\x04\x11\x22\x33\x44", 0x11, bytes.fromhex("11223344")),
        0xa4: (b"\x03\x78\x56\x34\x12", 0x0c, bytes.fromhex("78563412")),
        0xa5: (b"\x00synthetic", 0x17, b"synthetic\0"),
        0xa7: (byte(7), 0x15, b"\x01"),
        0xa8: (short(480), 2, struct.pack("<H", 480)),
        0xa9: (short(1020), 4, struct.pack("<H", 1020)),
        0xaa: (byte(0x7f), 0x16, b"\x7f"),
        0xab: (byte(3), 6, b"\x01"),
        0xac: (byte(3), 0, b"\x02"),
        0xad: (byte(3), 6, b"\x02"),
        0xae: (short(1320), 8, struct.pack("<H", 1320)),
        0xaf: (short(360), 10, struct.pack("<H", 360)),
    }
    for tag, (value, offset, expected) in fields.items():
        m = ClockScreenMachine(ready=False)
        before = bytes(m.uc.mem_read(THEME, 0x118))
        after = m.command({tag: value})
        assert after[offset:offset + len(expected)] == expected
        if tag == 0xa2:
            assert after[0] == 1
        assert m.scheduled == 1
        record(f"field_{tag:02x}", stored_offset=offset, stored_hex=expected.hex(),
               changed_bytes=sum(a != b for a, b in zip(before, after)))

    m = ClockScreenMachine()
    before = bytes(m.uc.mem_read(THEME, 0x118))
    m.command({0xa6: b"\x00synthetic-resource\0"})
    assert bytes(m.uc.mem_read(ASSET, 19)) == b"synthetic-resource\0"
    assert bytes(m.uc.mem_read(THEME, 0x118)) == before and not m.transfers
    record("asset_locator_alone_does_not_start_transfer")

    m = ClockScreenMachine(ready=False)
    m.command({0xa2: byte(0x83), 0xac: byte(2), 0xab: byte(2), 0xad: byte(2), 0xa7: byte(0)})
    state = bytes(m.uc.mem_read(THEME, 0x18))
    assert state[0] == 1 and state[6] == 0 and state[0x15] == 0
    record("flag_lsb_not_boolean_normalization")

    # Two daily windows, Monday-first weekday mask, 5/20 brightness result.
    def plan(**kwargs):
        m = ClockScreenMachine(**kwargs)
        m.command({0xa2: byte(0x80), 0xa8: short(480), 0xa9: short(1020), 0xaa: byte(0x7f)})
        return m

    for hour, minute, expected in ((7, 59, 0), (8, 0, 5), (16, 59, 5), (17, 0, 0)):
        m = plan(rtc=local_epoch(hour=hour, minute=minute))
        assert m.schedule() == expected
        record(f"window_{hour:02}_{minute:02}", brightness=expected)
    for day in range(28, 31):
        for mask in (1, 2, 4, 0x40, 0x7f):
            m = plan(rtc=local_epoch(day=day))
            m.command({0xaa: byte(mask)})
            expected = 5 if mask & (1 << (day - 28)) else 0
            assert m.schedule() == expected
            record(f"weekday_{day}_mask_{mask}", brightness=expected)
    for tag in (0xac, 0xad):
        m = plan()
        if tag == 0xad:
            m.command({0xa8: short(0), 0xa9: short(1), 0xab: byte(1),
                       0xae: short(480), 0xaf: short(1020)})
        m.command({tag: byte(1)})
        assert m.schedule() == 20
        record(f"brightness_{tag:02x}", brightness=20)

    for day, hour, expected in ((28, 21, 0), (28, 22, 5), (29, 5, 5), (29, 6, 0), (29, 22, 0)):
        m = plan(rtc=local_epoch(day=day, hour=hour))
        m.command({0xa8: short(1320), 0xa9: short(360), 0xaa: byte(1)})
        assert m.schedule() == expected
        record(f"overnight_{day}_{hour}", brightness=expected)
    for hour in (0, 8, 12, 23):
        m = plan(rtc=local_epoch(hour=hour))
        m.command({0xa8: short(480), 0xa9: short(480)})
        assert m.schedule() == 5
        record(f"equal_endpoints_full_day_{hour}")
    m = plan()
    m.command({0xab: byte(1), 0xad: byte(1), 0xae: short(0), 0xaf: short(1440)})
    assert m.schedule() == 5
    record("first_matching_window_wins")

    # Clock visibility requires enable, qualified input-power gate and year >2024.
    for name, options, expected in (("normal", {}, 1), ("no_power_gate", {"power_gate": False}, 0),
                                    ("2024", {"rtc": local_epoch(year=2024)}, 0),
                                    ("2025", {"rtc": local_epoch(year=2025)}, 1)):
        m = plan(**options)
        assert m.eligible() == expected
        record(f"visibility_gate_{name}", eligible=expected)
    m = plan()
    m.command({0xa2: byte(0)})
    assert m.eligible() == 0
    record("visibility_gate_disabled")

    # Actual callback changes only display state and one-shot clock enable.
    m = plan(rtc=local_epoch(hour=8))
    assert m.timer()
    m.set_rtc(local_epoch(hour=17))
    assert not m.timer()
    assert m.uc.mem_read(THEME + 0x10, 1)[0] == 0x80
    record("recurring_timer_turns_screen_off_preserves_enable")
    m = plan(rtc=local_epoch(hour=8))
    m.command({0xaa: byte(0)})
    assert m.timer() and m.uc.mem_read(0x2000045b, 1)[0] == 1
    m.set_rtc(local_epoch(hour=17))
    assert not m.timer() and m.uc.mem_read(THEME + 0x10, 1)[0] == 0
    assert ("stop", 6) in m.timer_calls
    record("zero_weekday_mask_one_shot_disables_after_window")
    m = plan(display_timer=True)
    assert not m.timer()
    record("active_normal_display_timer_defers_clock_screen")

    # Asset metadata changes defer the entire settings commit while online.
    for success in (False, True):
        m = ClockScreenMachine()
        old = bytes(m.uc.mem_read(THEME, 0x118))
        new = {0xa2: byte(0x83), 0xa3: b"\x04abcd", 0xa4: b"\x03\x01\x00\x00\x00",
               0xa5: b"\x00synthetic", 0xa6: b"\x00synthetic-resource\0"}
        m.command(new)
        assert bytes(m.uc.mem_read(THEME, 0x118)) == old
        assert m.uc.mem_read(STATUS, 1)[0] == 1
        assert m.transfers == [{"pointer": ASSET, "callback": 0x08029e19}]
        m.asset_callback(success)
        assert m.uc.mem_read(STATUS, 1)[0] == (0 if success else 2)
        assert (m.uc.mem_read(THEME + 0x10, 1)[0] == 0x83) == success
        record(f"asset_callback_{success}", final_transfer_status=0 if success else 2)
    m = ClockScreenMachine(busy=True)
    m.command({0xa3: b"\x04abcd", 0xa8: short(600)})
    assert not m.transfers and m.uc.mem_read(THEME + 2, 2) == b"\x00\x00"
    record("busy_asset_update_ack_without_committing")
    m = ClockScreenMachine(transfer_accept=False)
    m.command({0xa3: b"\x04abcd"})
    assert m.uc.mem_read(STATUS, 1)[0] == 1 and m.scheduled == 0
    record("rejected_transfer_start_still_marks_pending")

    # Native status DA and query0092 readback use different schemas.
    m = ClockScreenMachine(ready=False)
    m.command({0xa2: byte(0x82), 0xa3: b"\x04abcd", 0xa4: b"\x03\x78\x56\x34\x12",
               0xa5: b"\x00synthetic", 0xa7: byte(1), 0xa8: short(480), 0xa9: short(1020),
               0xaa: byte(0x55), 0xab: byte(1), 0xac: byte(1), 0xad: byte(0),
               0xae: short(1320), 0xaf: short(360)})
    da = m.da()
    assert da == bytes.fromhex("048200616263647856341201e001fc035501010028056801")
    record("da_serializer_layout", value_hex=da.hex())
    m.uc.mem_write(STATUS, b"\x02")
    response = m.readback()
    assert response[:7] == bytes.fromhex("00a10131a21104")
    assert response[7:9] == bytes((0x82, 2))
    assert response[9:17] == b"abcd" + bytes.fromhex("78563412")
    assert response[23:] == tlv(0xa3, b"\x04synthetic")
    assert m.uc.mem_read(STATUS, 1)[0] == 0
    record("query0092_readback_clears_failure", response_hex=response.hex())
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "timer-plan-results.json")
    parser.add_argument("--firmware-dir", type=Path,
                        help="Directory containing the exact documented MainMcu-decoded.bin")
    args = parser.parse_args()
    os.umask(0o077)
    if args.firmware_dir is not None:
        os.environ["SOLIX_FIRMWARE_DIR"] = str(args.firmware_dir.expanduser().resolve())
    output = args.output.expanduser().resolve()
    replay_io.ROOT = output.parent
    results = run_cases()
    sources = (Path(__file__).name, "emulate_clock_semantics.py", "emulate_soc_cap_handler.py",
               "emulate_tou_controller_encoding.py", "replay_io.py")
    report = {"firmware_sha256": FIRMWARE_SHA256, "cases_passed": len(results),
              "hardware_or_network_used": False,
              "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                for name in sources}, "results": results}
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o600)
    print(json.dumps({key: value for key, value in report.items() if key not in ("results", "source_sha256")}))


if __name__ == "__main__":
    main()
