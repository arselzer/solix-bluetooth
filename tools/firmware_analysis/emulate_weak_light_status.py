"""Execute A1763 weak-light getter, producer, timer and A3 serializer offline.

The actual ARM branches run against synthetic RAM/RTC. Backup-register IO,
logging and the low-SOC action helper are substitutes. The MPPT/DSP producer,
physical PV conditions, scheduler and power actions are not executed.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from emulate_additional_features import TelemetryMachine
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
CONTEXT = 0x20000394
FLAGS = CONTEXT + 6
GLOBAL = 0x20000164


class WeakLightMachine(TelemetryMachine):
    def __init__(self, flags=0, counter=0):
        super().__init__()
        self.backup = flags | (counter << 8)
        self.backup_writes = []
        self.low_soc_actions = []
        self.uc.mem_write(FLAGS, bytes((flags, counter)))

    def step(self, uc, address, size, data):
        r0, r1 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1))
        if address == 0x08014278:
            assert r0 == 2
            self.back(self.backup)
        elif address in (0x080142a4, 0x080142cc):
            assert r0 == 2 and r1 <= 0xffff
            self.backup = r1
            self.backup_writes.append(r1)
            self.back()
        elif address == 0x08011730:
            # This helper has low-SOC power/timer consequences. Record the
            # request without executing it; no actuator behavior is claimed.
            self.low_soc_actions.append(r0)
            self.back()
        elif any(lo <= address < hi for lo, hi in (
            (0x0800d5d0, 0x0800d73e), (0x08030418, 0x080304c0),
            (0x0802af38, 0x0802af44), (0x0801bc7c, 0x0801bc82),
            (0x080198f8, 0x08019928))):
            return
        else:
            super().step(uc, address, size, data)

    def state(self):
        raw = bytes(self.uc.mem_read(CONTEXT, 12))
        return {"flags": raw[6], "event_count": raw[7],
                "lock": (raw[6] >> 1) & 1, "retry_flag": (raw[6] >> 2) & 1,
                "timer_count": raw[3], "start_rtc": int.from_bytes(raw[8:12], "little")}

    def update(self, mode, event):
        self.uc.reg_write(UC_ARM_REG_R1, event)
        return self.call(0x0800d5d0, mode)

    def set_rtc(self, value):
        self.uc.mem_write(0x40002818, struct.pack("<II", value >> 16, value & 0xffff))

    def timer(self, *, rtc, start, activity=0, module_bit5=False, blocked=False):
        self.set_rtc(rtc)
        self.uc.mem_write(CONTEXT+8, struct.pack("<I", start))
        self.uc.mem_write(0x200003e9, bytes((activity,)))
        self.uc.mem_write(0x20003f4c, bytes((int(module_bit5) << 5,)))
        self.uc.mem_write(GLOBAL, struct.pack("<I", (1 << 23) if blocked else 0))
        self.call(0x08030418)


def main():
    image = firmware_image()
    registration = image[0x08032d88-0x08005000:0x08032d90-0x08005000]
    assert registration[:2] == bytes((0xa3, 4))
    assert int.from_bytes(registration[4:], "little") == 0x0801a2a9
    results = {"getter_and_serializer": [], "lock_accumulation": [],
               "clear_state": [], "timer_transitions": [], "remaining_time": []}
    flag_values = (0, 1, 2, 3, 4, 8, 0x10, 0x12, 0x20, 0x22, 0x40, 0x42, 0x80, 0x82, 0xfc, 0xff)
    for flags in flag_values:
        for mode in (1, 3):
            m = WeakLightMachine(flags)
            # Preserve unrelated settings/output memory throughout serializers.
            protected = bytes(m.uc.mem_read(0x20001d48, 0x200))
            m.uc.mem_write(0x2000041c, b"\x02")
            m.uc.mem_write(0x200000c0, b"\x21")
            before = bytes(m.uc.mem_read(CONTEXT, 12))
            field = m.serialize(0xa3, mode=mode, prior=b"\x04"+b"\x77"*13)
            assert field[13] == m.call(0x08015494) == ((flags >> 1) & 1)
            assert field[1:3] == b"\x02\x21" and field[11:13] == struct.pack("<H", 600)
            assert bytes(m.uc.mem_read(CONTEXT, 12)) == before
            assert bytes(m.uc.mem_read(0x20001d48, 0x200)) == protected
            assert not m.backup_writes and not m.low_soc_actions
            results["getter_and_serializer"].append({"synthetic_flags": flags,
                "serializer_mode": mode, "a3_weak_light_lock": field[13],
                "a3_length": len(field), "a3_type": field[0],
                "lock_state_and_settings_unchanged": True})
    for previous_mode in (0, 1, 2, 3):
        for event in (0, 1):
            m = WeakLightMachine(previous_mode << 4)
            sequence = []
            first_lock = 4 if previous_mode in (1, 2) else 5
            for ordinal in range(1, 6):
                newly_locked = m.update(1, event)
                assert m.state()["lock"] == int(bool(event) and ordinal >= first_lock)
                assert newly_locked == int(bool(event) and ordinal == first_lock)
                sequence.append({"event": ordinal, "newly_locked": newly_locked, **m.state()})
            assert m.low_soc_actions == ([0] if event else [])
            results["lock_accumulation"].append({"initial_mode_nibble": previous_mode,
                "condition_argument": event, "sequence": sequence,
                "backup_write_values": m.backup_writes,
                "substituted_low_soc_actions": m.low_soc_actions})
    for flags in flag_values:
        m = WeakLightMachine(flags, 3)
        assert m.update(0, 0) == 0
        assert m.state()["flags"] == m.state()["event_count"] == 0
        assert m.backup == 0 and not m.low_soc_actions
        results["clear_state"].append({"initial_flags": flags, "initial_event_count": 3,
            "state": m.state(), "backup_register_after": m.backup})
    timer_cases = (
        ("unlocked", 0x10, 1600, 1000, 0, False, False, 0x10, 0xffffffff, []),
        ("user_action", 0x1b, 1600, 1000, 0, False, False, 4, 0xffffffff, []),
        ("global_gate", 0x1b, 1601, 1000, 0, False, True, 0x1b, 1000, []),
        ("equal_start", 0x13, 1000, 1000, 1, False, False, 0x13, 1000, []),
        ("earlier_rtc", 0x13, 999, 1000, 1, False, False, 0x13, 999, []),
        ("elapsed_600", 0x13, 1600, 1000, 1, False, False, 0x13, 1000, []),
        ("elapsed_601", 0x13, 1601, 1000, 1, False, False, 0x17, 0xffffffff, [1]),
    )
    for name, flags, rtc, start, activity, module, blocked, expected, saved, actions in timer_cases:
        m = WeakLightMachine(flags)
        m.timer(rtc=rtc, start=start, activity=activity, module_bit5=module, blocked=blocked)
        assert m.state()["flags"] == expected and m.state()["start_rtc"] == saved
        assert m.low_soc_actions == actions
        results["timer_transitions"].append({"case": name, "synthetic_rtc": rtc,
            "initial_start_rtc": start, "state": m.state(),
            "substituted_low_soc_actions": m.low_soc_actions})
    for activity, module in ((0, False), (1, False), (0, True)):
        m = WeakLightMachine(0x13)
        observations = []
        for ordinal in range(1, 17):
            m.timer(rtc=1000+ordinal, start=1000, activity=activity, module_bit5=module)
            expected = int(bool(activity or module) or ordinal < 16)
            assert m.state()["lock"] == expected
            observations.append({"callback": ordinal, **m.state()})
        results["timer_transitions"].append({"case": "callback_count",
            "activity_byte": activity, "module_bit5": module, "sequence": observations})
    for rtc, start, remaining in ((1000, 0xffffffff, 600), (999, 1000, 600),
                                   (1000, 1000, 600), (1001, 1000, 599),
                                   (1599, 1000, 1), (1600, 1000, 0), (1601, 1000, 0)):
        m = WeakLightMachine(0x13)
        m.set_rtc(rtc)
        m.uc.mem_write(CONTEXT+8, struct.pack("<I", start))
        value = m.call(0x080198f8)
        assert value == remaining
        results["remaining_time"].append({"synthetic_rtc": rtc, "start_rtc": start,
                                         "internal_remaining_value": value})
    counts = {name: len(rows) for name, rows in results.items()}
    assert counts == {"getter_and_serializer": 32, "lock_accumulation": 8,
                      "clear_state": 16, "timer_transitions": 10, "remaining_time": 7}
    result = ROOT / "weak-light-status-results.json"
    result.write_text(json.dumps(results, indent=2)+"\n")
    result.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_additional_features.py", "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": {"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in sources},
        "substitutions": ["backup-register read/write", "low-SOC action helper",
                          "logging", "memcpy/memset", "system ticks"],
        "not_executed": ["MPPT/DSP physical producer", "scheduler", "PV hardware", "actuators"]}
    result = ROOT / "weak-light-status-manifest.json"
    result.write_text(json.dumps(manifest, indent=2)+"\n")
    result.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
