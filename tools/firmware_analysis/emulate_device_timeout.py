"""Offline C1000 1.1.4.9 Device Timeout / idle / wake-queue branch replay.

Actual setting/getter, idle predicate, RTC wake-table selection/reset and
callback instructions execute. Timer installation/activation, IRQ configuration,
calendar formatting, persistence and event delivery are recorded substitutes.
Physical sleep is a stopping boundary; no shutdown or hardware is simulated.
"""

import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from emulate_clock_semantics import ClockMachine, ROOT
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256

os.umask(0o077)
SETTINGS = 0x20001d48
WAKE = 0x200034a0
ARMED = 0x200003a0
FLAGS = 0x20000164
COUNTDOWN = 0x200004da
TIMERS = 0x20007344


class TimeoutMachine(ClockMachine):
    def __init__(self, minutes=0, *, rtc=10000):
        super().__init__(rtc=rtc)
        self.uc.mem_map(0xe000e000, 0x1000)
        self.uc.mem_write(SETTINGS + 0x11, struct.pack("<H", minutes))
        self.uc.mem_write(0x20000466, b"\x14")
        self.uc.mem_write(TIMERS + 20 * 20, b"\x03")
        self.uc.mem_write(0x2000015c, b"\x05")
        self.uc.mem_write(COUNTDOWN, struct.pack("<H", 12))
        self.calls = []
        self.sleep_boundary = False

    def step(self, uc, address, size, data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        if address == 0x080238f4:
            self.calls.append({"kind": "persistence_request"})
            self.back(1)
        elif address == 0x08029be4:
            self.calls.append({"kind": "rtc_alarm", "after_seconds": r0})
            self.back()
        elif address in (0x080176d8, 0x08022262, 0x08022278):
            self.calls.append({"kind": "irq_boundary", "address": f"{address:08x}",
                               "arguments": [r0, r1, r2]})
            self.back()
        elif address in (0x080105cc, 0x080107f4):
            self.calls.append({"kind": "timer_create", "repeating": address == 0x080107f4,
                               "interval_units": r0, "callback": f"{r1:08x}"})
            uc.mem_write(r3, b"\x2a")
            self.back()
        elif address == 0x0801095c:
            self.calls.append({"kind": "timer_activate", "timer_id": r0})
            self.back()
        elif address == 0x080106b4:
            self.calls.append({"kind": "event_delivery", "event_id": r0, "delay": r1})
            self.back()
        elif address == 0x0801a6e8:
            # Used only to format the selected wake time for logging.
            self.back(self.rtc() % 86400)
        elif address == 0x08019fd4:
            uc.mem_write(r0, bytes(4))
            self.back()
        elif address == 0x080146c8:
            # RTC calendar logging, not a shutdown/sleep operation.
            self.back()
        elif address == 0x0800edd4:
            self.calls.append({"kind": "sleep_preparation_boundary", "argument": r0})
            self.back()
        elif address == 0x0800f018:
            self.sleep_boundary = True
            self.stopped = True
            uc.emu_stop()
        elif any(lo <= address < hi for lo, hi in (
            (0x0802b420, 0x0802b42a), (0x0801a5fc, 0x0801a604),
            (0x0802b5e8, 0x0802b72e), (0x0801a6d0, 0x0801a6d6),
            (0x08019760, 0x08019794), (0x08015494, 0x0801549e),
            (0x080198f8, 0x08019928), (0x0802b1d0, 0x0802b204),
            (0x08029b7c, 0x08029bae), (0x08029bb8, 0x08029bda),
            (0x08008728, 0x08008742), (0x08008142, 0x0800814a),
            (0x0800f124, 0x0800f328), (0x0800f6dc, 0x0800f6e2),
            (0x0801bf3c, 0x0801bf52), (0x08010840, 0x08010864),
            (0x0801883c, 0x08018844), (0x08019754, 0x0801975a),
            (0x08019b00, 0x08019b12),
            (0x0800f07c, 0x0800f0a4), (0x0800f0de, 0x0800f0e0),
            (0x0800e2e0, 0x0800e2e6),
            (0x0800eff0, 0x0800f00e))):
            return
        else:
            super().step(uc, address, size, data)

    def word(self, address):
        return int.from_bytes(self.uc.mem_read(address, 4), "little")

    def half(self, address):
        return int.from_bytes(self.uc.mem_read(address, 2), "little")

    def byte(self, address):
        return self.uc.mem_read(address, 1)[0]

    def set_time(self, seconds):
        self.uc.mem_write(0x40002818, struct.pack("<II", seconds >> 16, seconds & 0xffff))

    def scheduler(self):
        self.run(0x0802b5e8, 0)

    def idle_tick(self):
        self.run(0x0800f124, 0)
        return self.half(COUNTDOWN)

    def record(self):
        return {"timeout_enabled": bool(self.byte(WAKE+0x10)),
                "timeout_deadline": self.word(WAKE+0x14),
                "timeout_entry_armed": bool(self.byte(WAKE+0x1c)),
                "selected_entry": self.byte(WAKE+0x40),
                "rtc_alarm_armed": bool(self.byte(ARMED)),
                "requested_alarms_seconds": [row["after_seconds"] for row in self.calls
                                             if row["kind"] == "rtc_alarm"]}


def main():
    results = {"timeout_selection": [], "pending_alarm_and_wake_reset": [],
               "idle_state_bits": [], "power_gate_and_special_mode": [],
               "auxiliary_busy_states": [], "ac_substates": [], "sleep_boundary": [],
               "timeout_callback": [], "idle_registration": []}
    for minutes, armed in itertools.product((0, 30, 60, 120, 240, 360, 720, 1440, 65535), (0, 1)):
        m = TimeoutMachine(720)
        before = bytes(m.uc.mem_read(SETTINGS, 0x190))
        m.run(0x0802b420, minutes)
        expected = bytearray(before)
        expected[0x11:0x13] = struct.pack("<H", minutes)
        assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == expected
        m.uc.mem_write(WAKE+0x1c, bytes((armed,)))
        m.uc.mem_write(WAKE+0x14, struct.pack("<I", 10777))
        m.scheduler()
        row = m.record()
        assert row["timeout_enabled"] == bool(minutes)
        if minutes:
            expected_delay = 777 if armed else minutes*60
            assert row["selected_entry"] == 1
            assert row["requested_alarms_seconds"] == [expected_delay]
            assert row["timeout_deadline"] == 10000 + expected_delay
        else:
            assert row["requested_alarms_seconds"] == [] and row["selected_entry"] == 255
        results["timeout_selection"].append({"minutes": minutes, "previous_entry_armed": bool(armed),
                                            "settings_only_target_changed": True, **row})
    for original, replacement in itertools.product((30, 720), (0, 30, 720)):
        m = TimeoutMachine(original)
        m.scheduler()
        first = m.record()
        m.set_time(10010)
        m.run(0x0802b420, replacement)
        m.scheduler()
        # Already armed: scheduler returns before reading the replacement setting.
        assert m.record() == first
        m.run(0x0802b1d0, 0)
        assert m.byte(ARMED) == 0
        assert all(m.byte(WAKE+i*16+12) == 0 for i in range(4))
        m.calls.clear()
        m.scheduler()
        assert m.record()["requested_alarms_seconds"] == ([replacement*60] if replacement else [])
        results["pending_alarm_and_wake_reset"].append({"original_minutes": original,
            "replacement_minutes": replacement, "setting_write_does_not_cancel_existing_alarm": True,
            "normal_wake_clears_all_entry_armed_flags": True, "after_wake_reset": m.record()})
    reset_bits = {0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 18, 20, 23}
    for minutes, bit in itertools.product((0, 720), range(32)):
        m = TimeoutMachine(minutes)
        m.uc.mem_write(FLAGS, struct.pack("<I", 1 << bit))
        observed = m.idle_tick()
        assert observed == (60 if bit in reset_bits else 11)
        assert m.word(FLAGS) == 1 << bit
        results["idle_state_bits"].append({"minutes": minutes, "only_set_state_bit": bit,
            "before": 12, "after": observed, "reset_to_60": bit in reset_bits})
    for minutes, gate, special in itertools.product((0, 720), range(8), (0, 1)):
        m = TimeoutMachine(minutes)
        m.uc.mem_write(0x200004be, bytes((gate,)))
        m.uc.mem_write(0x2000039a, bytes((special << 1,)))
        reset = bool(gate & 1 or (gate & 2 and not special))
        assert m.idle_tick() == (60 if reset else 11)
        results["power_gate_and_special_mode"].append({"minutes": minutes, "raw_gate_byte": gate,
            "special_mode": bool(special), "reset_to_60": reset})
    for minutes, display, auxiliary, pending in itertools.product((0, 720), (0, 1), (0, 1), (0, 1)):
        m = TimeoutMachine(minutes)
        m.uc.mem_write(TIMERS+20*20, bytes((2 if display else 3,)))
        m.uc.mem_write(0x200028fa, bytes((auxiliary,)))
        m.uc.mem_write(0x20000728, bytes((pending,)))
        reset = bool(display or auxiliary or pending)
        assert m.idle_tick() == (60 if reset else 11)
        results["auxiliary_busy_states"].append({"minutes": minutes,
            "display_timer_active": bool(display), "raw_auxiliary_200028fa": auxiliary,
            "raw_busy_20000728": pending, "reset_to_60": reset})
    for minutes, substate in itertools.product((0, 720), range(4)):
        m = TimeoutMachine(minutes)
        m.uc.mem_write(FLAGS, struct.pack("<I", substate << 4))
        m.run(0x08019b00, 0)
        enabled = m.uc.reg_read(UC_ARM_REG_R0)
        assert enabled == bool(substate)
        assert m.idle_tick() == (60 if substate == 1 else 11)
        results["ac_substates"].append({"minutes": minutes, "raw_ac_substate": substate,
            "reported_ac_output_enabled": bool(enabled), "reset_to_60": substate == 1,
            "physical_substate_producer_executed": False})
    for minutes, active_pv in itertools.product((0, 30), (0, 1)):
        m = TimeoutMachine(minutes)
        m.uc.mem_write(COUNTDOWN, struct.pack("<H", 2))
        m.uc.mem_write(FLAGS, struct.pack("<I", active_pv))
        values = [m.idle_tick(), m.idle_tick()]
        assert values == ([60, 60] if active_pv else [1, 0])
        m.run(0x0800f07c, 0)
        assert m.sleep_boundary == (not active_pv)
        results["sleep_boundary"].append({"minutes": minutes, "pv_state_active": bool(active_pv),
            "countdown_sequence": values, "physical_sleep_boundary_reached": m.sleep_boundary,
            "physical_sleep_executed": False})
    for existing_timer, old_events in itertools.product((0, 42), (0, 2, 0x4000)):
        m = TimeoutMachine(30)
        m.scheduler()
        assert m.byte(WAKE+0x40) == 1
        m.uc.mem_write(ARMED+1, bytes((existing_timer,)))
        m.uc.mem_write(0x20000436, struct.pack("<H", old_events))
        m.run(0x08029b7c, 0)
        assert m.byte(WAKE+0x1c) == 0 and m.byte(ARMED) == 0
        creates = [row for row in m.calls if row["kind"] == "timer_create"]
        assert creates == ([] if existing_timer else [{"kind": "timer_create", "repeating": False,
            "interval_units": 5000, "callback": "08008729"}])
        assert m.calls[-2] == {"kind": "timer_activate", "timer_id": 42}
        assert m.calls[-1]["kind"] == "irq_boundary"
        assert m.calls[-1]["address"] == "08022262"
        assert m.calls[-1]["arguments"][0] == 41
        m.run(0x08008728, 0)
        assert m.half(0x20000436) == old_events | 0x1000
        assert m.calls[-1] == {"kind": "event_delivery", "event_id": 5, "delay": 0}
        assert m.word(FLAGS) == 0
        results["timeout_callback"].append({"timer_preexisting": bool(existing_timer),
            "prior_events": old_events, "after_events": m.half(0x20000436),
            "timer_create": creates, "event_delivery": m.calls[-1],
            "shutdown_event_consumer_executed": False})
    m = TimeoutMachine()
    m.run(0x0800eff0, 0)
    assert m.calls == [{"kind": "timer_create", "repeating": True,
                        "interval_units": 1000, "callback": "0800f125"},
                       {"kind": "timer_activate", "timer_id": 42}]
    results["idle_registration"].append({"calls": m.calls})
    counts = {name: len(rows) for name, rows in results.items()}
    assert sum(counts.values()) == 155
    output = ROOT / "device-timeout-results.json"
    output.write_text(json.dumps(results, indent=2) + "\n")
    output.chmod(0o600)
    package = Path(__file__).resolve().parent
    names = ("emulate_device_timeout.py", "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": {"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in names}}
    output = ROOT / "device-timeout-manifest.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    output.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
