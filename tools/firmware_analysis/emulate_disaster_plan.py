"""Offline C1000 Gen 2 1.1.4.9 disaster schedule and power-policy replay.

Executes the native TLV parser, 005e handler, storage/getters, active-plan
selector, D9 builder and selected charging/BMS policy instructions. Calendar
conversion is replaced by a synthetic year; real RTC/offset reads execute.
Inherited sensor, transport, flash, timer, LCD and memcpy substitutes remain.
No station, network, DSP or real electrical output is accessed or simulated.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4,
    UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_SP,
)

from emulate_charging_followup import CapsMachine
from emulate_clock_semantics import PAYLOAD, ROOT, STACK, STOP
from emulate_feature_candidates import FeatureMachine
from emulate_general_settings import REQUEST, SETTINGS, tlv
from emulate_tou_d9 import D9Machine
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, firmware_image

os.umask(0o077)
AUTOMATIC = SETTINGS + 0x161
MANUAL = SETTINGS + 0x17c
MANUAL_SWITCH = SETTINGS + 0x185
AUTO_SWITCH = SETTINGS + 0x186
ACTIVE = 0x200018ec
NOW = 1790726400  # Synthetic 2026 timestamp, never taken from a capture.


def record(start, end, maximum=100):
    return struct.pack("<BII", maximum, start, end)


def wire_record(start, end, maximum=100, minimum=0):
    return b"\x04" + struct.pack("<BBII", maximum, minimum, start, end)


class DisasterMachine(FeatureMachine):
    def __init__(self, *, now=NOW, offset=0, year=2026, **kwargs):
        super().__init__(**kwargs)
        self.year = year
        self.bms = []
        self.now(now, offset)

    def now(self, now, offset=0):
        rtc = now - offset
        self.uc.mem_write(0x40002818, struct.pack("<II", rtc >> 16, rtc & 0xffff))
        self.uc.mem_write(SETTINGS + 5, struct.pack("<i", offset))

    def step(self, uc, address, size, data):
        if address == 0x08014710:
            # Substitute calendar conversion only, not the actual year gate.
            uc.mem_write(0x200008b4, struct.pack("<I", 0x2000e000))
            uc.mem_write(0x2000e014, struct.pack("<I", self.year - 1900))
            self.back()
        elif any(lo <= address < hi for lo, hi in (
            (0x0800c0a8, 0x0800c284), (0x080093dc, 0x080094f8),
            (0x08018aac, 0x08018c7c), (0x08015450, 0x0801546e),
            (0x0801a608, 0x0801a648), (0x0802b430, 0x0802b490),
            (0x080190d4, 0x080191fe), (0x08023f3e, 0x08024054),
        )):
            return
        elif address in (0x080107f4, 0x0801095c, 0x080105cc, 0x0802940c,
                         0x08020a4c, 0x08027e40) or any(
                lo <= address < hi for lo, hi in (
                    (0x0800dc50, 0x0800dd3e), (0x08029542, 0x080295a2))):
            CapsMachine.step(self, uc, address, size, data)
        else:
            super().step(uc, address, size, data)

    def disaster(self, *, option=0, switch=None, count=None, entries=None,
                 cancel=None, selector=3):
        body = tlv(0xa1, b"\x22")
        for tag, value in ((0xa3, selector), (0xa4, option),
                           (0xa5, switch), (0xa6, count), (0xaa, cancel)):
            if value is not None:
                body += tlv(tag, bytes((1, value)))
        for index, value in (entries or {}).items():
            body += tlv(0xa7 + index, value)
        body += tlv(0xfe, b"\x03" + struct.pack("<I", NOW))
        self.acked = 0
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800c0a8, REQUEST)
        return self.acked

    def active(self):
        pointer = self.run(0x08018aac, 0)
        assert pointer == ACTIVE
        raw = bytes(self.uc.mem_read(pointer, 12))
        maximum, start, end, kind, active, index = struct.unpack("<BIIBBB", raw)
        return {"stored_maximum": maximum, "start": start, "end": end,
                "kind": kind, "active": active, "index": index}

    def seed(self, *, kind=1, index=0, start=NOW-10, end=NOW+60,
             maximum=100, switch=1):
        address = MANUAL if kind == 1 else AUTOMATIC + 9*index
        self.uc.mem_write(address, record(start, end, maximum))
        self.uc.mem_write(MANUAL_SWITCH if kind == 1 else AUTO_SWITCH,
                          bytes((switch,)))

    def d9(self):
        return D9Machine.d9(self)

    def bms_update(self):
        """Run only the limit-update tail of 08023e0c, after unrelated events."""
        self.stopped = False
        self.bms.clear()
        self.uc.mem_write(STACK - 24, struct.pack("<6I", 0, 0, 0, 0, 0, STOP | 1))
        self.uc.reg_write(UC_ARM_REG_SP, STACK - 24)
        self.uc.reg_write(UC_ARM_REG_R4, 0x200003ec)
        self.uc.reg_write(UC_ARM_REG_R5, 0x2000040c)
        self.uc.reg_write(UC_ARM_REG_R6, 0x20000164)
        self.uc.emu_start(0x08023f3f, 0, count=30000)
        assert self.stopped
        return self.bms.copy()


def main():
    firmware_image()
    results = {name: [] for name in (
        "switch_getters", "wire_storage", "selector_and_clear", "time_windows",
        "selection_and_cancellation", "charging_policy", "policy_gate_inputs", "bms_limit_update",
    )}
    for value in range(256):
        m = DisasterMachine()
        m.uc.mem_write(MANUAL_SWITCH, bytes((value, value)))
        manual, automatic = (m.run(0x0801a61c, kind) for kind in (1, 2))
        assert manual == bool(value) and automatic == bool(value)
        results["switch_getters"].append({"stored": value, "manual": manual, "automatic": automatic})

    for option in (0, 1, 3, 255):
        for count in (0, 1, 2, 3, 4, 255):
            for minimum in (0, 37, 255):
                m = DisasterMachine()
                original = bytes(m.uc.mem_read(SETTINGS, 0x190))
                payload = {i: wire_record(NOW+10+i*100, NOW+90+i*100, 80+i, minimum)
                           for i in range(3)}
                assert m.disaster(option=option, switch=1, count=count, entries=payload) == 1
                expected = bytearray(original)
                if option == 0:
                    expected[0x17c:0x185] = record(NOW+10, NOW+90, 80)
                    expected[0x185] = 1
                else:
                    expected[0x186] = 1
                    for i in range(min(count, 3)):
                        expected[0x161+9*i:0x16a+9*i] = record(NOW+10+i*100, NOW+90+i*100, 80+i)
                assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == bytes(expected)
                results["wire_storage"].append({"option": option, "count": count,
                    "ignored_minimum_byte": minimum, "manual_record_stored": option == 0,
                    "automatic_records_stored": 0 if option == 0 else min(count, 3),
                    "unrelated_settings_preserved": True})

    for selector in (None, 0, 1, 2, 3, 4, 255):
        for option in (None, 0, 1, 2):
            m = DisasterMachine()
            m.seed(); m.seed(kind=2)
            original = bytes(m.uc.mem_read(SETTINGS, 0x190))
            ack = m.disaster(selector=selector, option=option)
            expected_ack = int(selector != 3 or option is not None)
            assert ack == expected_ack
            changed = bytes(m.uc.mem_read(SETTINGS, 0x190)) != original
            assert changed == (selector == 3 and option == 2)
            if changed:
                assert bytes(m.uc.mem_read(AUTOMATIC, 38)) == bytes(38)
                assert m.active()["active"] == 0
            results["selector_and_clear"].append({"selector": selector, "option": option,
                "ack_count": ack, "all_disaster_records_and_flags_cleared": changed})

    for kind in (1, 2):
        for switch in (0, 1):
            for relative in (-11, -10, 0, 60, 61):
                for offset in (-7200, 0, 7200):
                    for year in (2024, 2025):
                        m = DisasterMachine(now=NOW+relative, offset=offset, year=year)
                        m.seed(kind=kind, switch=switch)
                        active = m.active()
                        expected = year > 2024 and -10 <= relative <= 60 and switch != 0
                        assert bool(active["active"]) == expected
                        assert m.active() == active  # Includes exact-end re-selection.
                        d9 = m.d9()
                        tail = 7 + 3*d9[6]
                        assert d9[tail:tail+3] == bytes((kind if expected else 0, switch if kind == 1 else 0, switch if kind == 2 else 0))
                        assert struct.unpack("<II", d9[tail+3:tail+11]) == ((NOW-10, NOW+60) if kind == 1 else (0, 0))
                        assert struct.unpack("<II", d9[tail+11:tail+19]) == ((NOW-10, NOW+60) if expected else (0, 0))
                        results["time_windows"].append({"kind": kind, "stored_switch": switch,
                            "relative_now": relative, "stored_offset": offset,
                            "synthetic_calendar_year": year, "active": bool(expected),
                            "actual_d9_tail_matches": True})

    m = DisasterMachine(); m.seed(); m.seed(kind=2, index=1)
    assert m.active()["kind"] == 2 and m.active()["index"] == 1
    results["selection_and_cancellation"].append({"case": "automatic_initial_priority", "kind": 2, "index": 1})
    m = DisasterMachine(); m.seed(); assert m.active()["kind"] == 1
    m.seed(kind=2, index=1)
    assert m.active()["kind"] == 1
    results["selection_and_cancellation"].append({"case": "existing_manual_selection_sticky", "kind": 1})
    m = DisasterMachine(); m.seed(); m.seed(kind=2, index=1)
    assert m.disaster(option=0, switch=0) == 1
    assert m.uc.mem_read(MANUAL_SWITCH, 1)[0] == 0 and m.active()["active"] == 0
    replaced = bytes(m.uc.mem_read(AUTOMATIC+9, 9))
    assert replaced == record(0xffffffff, 0xffffffff)
    results["selection_and_cancellation"].append({"case": "manual_off_disables_manual_and_invalidates_overlapping_auto",
        "manual_active": False, "auto_replacement_hex": replaced.hex()})
    for kind in (1, 2):
        m = DisasterMachine(); m.seed(kind=kind)
        assert m.disaster(option=kind-1, cancel=1) == 1
        assert m.active()["active"] == 0
        address = MANUAL if kind == 1 else AUTOMATIC
        assert bytes(m.uc.mem_read(address, 9)) == record(0xffffffff, 0xffffffff)
        results["selection_and_cancellation"].append({"case": "cancel_current", "kind": kind,
            "active_after": False, "stored_sentinel": "64ffffffffffffffff"})
    m = DisasterMachine(); m.seed(kind=2, index=0); m.seed(kind=2, index=1)
    assert m.disaster(option=1, count=1, entries={0: wire_record(NOW+200, NOW+300)}) == 1
    assert bytes(m.uc.mem_read(AUTOMATIC+9, 18)) == bytes(18)
    results["selection_and_cancellation"].append({"case": "auto_partial_update_zeros_unsupplied_slots", "cleared_slots": [1, 2]})
    for kind in (1, 2):
        for start, end in ((0, NOW+10), (NOW, NOW), (NOW+1, NOW),
                           (NOW+1, NOW+2), (NOW-2, NOW-1),
                           (NOW-1, NOW+1), (NOW-1, 0xffffffff)):
            m = DisasterMachine(); m.seed(kind=kind, start=start, end=end)
            expected = start != 0 and end > start and start <= NOW <= end
            assert bool(m.active()["active"]) == expected
            results["selection_and_cancellation"].append({"case": "window_validity",
                "kind": kind, "start": start, "end": end, "active": expected})
    for kind in (1, 2):
        m = DisasterMachine(); m.seed(kind=kind)
        states = []
        for relative in (0, 60, 61):
            m.now(NOW+relative)
            states.append(m.active()["active"])
        assert states == [1, 1, 0]
        results["selection_and_cancellation"].append({"case": "natural_expiry",
            "kind": kind, "at_midwindow_end_after": states})

    for schedule in ("standard", "peak", "mid_peak", "off_peak"):
        for backup_kind in (0, 1, 2):
            backup = backup_kind != 0
            for current in (0, 10):
                for maximum in (0, 80, 100):
                    for power in (100, 300, 1200):
                        m = DisasterMachine(schedule=schedule, soc=100 if current == 0 else 91,
                                            current=current)
                        m.uc.mem_write(SETTINGS+0xf, struct.pack("<H", power))
                        m.uc.mem_write(SETTINGS+0x17, bytes((80, 5, 75)))
                        if backup:
                            m.seed(kind=backup_kind, maximum=maximum)
                        before = bytes(m.uc.mem_read(SETTINGS, 0x190))
                        after = m.policy()
                        plan = after["charge_plans"]
                        expected_current = current*10 if backup or schedule in ("standard", "off_peak") else 0
                        assert len(plan) == 1 and plan[0]["channel"] == 0
                        assert plan[0]["power_voltage_current"][2] == expected_current
                        if backup:
                            assert plan[0]["power_voltage_current"][0] == 1400
                        assert after["output_enabled_flag"] and after["fast_readback"] == 0
                        assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == before
                        results["charging_policy"].append({"schedule": schedule, "active_disaster": backup,
                            "disaster_kind": backup_kind,
                            "synthetic_bms_current_limit": current, "stored_plan_maximum": maximum,
                            "configured_power": power, "stored_cap": 80, "stored_lower": 5, "stored_reserve": 75,
                            "result": after, "all_persistent_settings_preserved": True})
    for ready in (False, True):
        for gate in (False, True):
            for available in (False, True):
                m = DisasterMachine(schedule="peak", ready=ready, gate=gate, ac_available=available)
                m.seed()
                after = m.policy()
                assert m.active()["active"] == 1
                assert after["charge_plans"] == ([{"channel": 0, "power_voltage_current": [1400, 600, 100]}]
                                                 if available else [])
                assert after["output_enabled_flag"]
                results["policy_gate_inputs"].append({"synthetic_network_ready": ready,
                    "synthetic_debounced_power_gate": gate, "synthetic_ac_charge_available": available,
                    "result": after, "physical_gate_producers_bypassed": True})
    for mode in (0, 1, 2, 3):
        m = DisasterMachine(schedule="peak"); m.seed()
        m.uc.mem_write(0x20000164, struct.pack("<I", 0x40+16*mode))
        m.policy()
        flags = int.from_bytes(m.uc.mem_read(0x20000164, 4), "little")
        assert flags == (0x60 if mode else 0x40)
        results["policy_gate_inputs"].append({"initial_output_mode_bits": mode,
            "final_output_mode_bits": (flags >> 4) & 3, "enabled_boolean_preserved": True})
    for schedule in ("standard", "peak", "mid_peak", "off_peak"):
        for backup_kind in (0, 1, 2):
            backup = backup_kind != 0
            for upper, lower, reserve in ((80, 5, 75), (90, 1, 85), (100, 10, 95)):
                m = DisasterMachine(schedule=schedule)
                m.uc.mem_write(SETTINGS+0x17, bytes((upper, lower, reserve)))
                if backup:
                    m.seed(kind=backup_kind, maximum=80)
                before = bytes(m.uc.mem_read(SETTINGS, 0x190))
                bms = m.bms_update()
                expected = [100 if backup else upper, 1 if backup else lower,
                            lower if schedule == "standard" else min(100 if backup else upper, reserve)]
                assert bms == [expected], (schedule, backup, bms, expected)
                assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == before
                results["bms_limit_update"].append({"schedule": schedule, "active_disaster": backup,
                    "disaster_kind": backup_kind,
                    "configured_upper_lower_reserve": [upper, lower, reserve], "bms_descriptor_bytes": bms[0],
                    "all_persistent_settings_preserved": True})

    counts = {name: len(rows) for name, rows in results.items()}
    assert counts == {"switch_getters": 256, "wire_storage": 72, "selector_and_clear": 28,
                      "time_windows": 120, "selection_and_cancellation": 22,
                      "charging_policy": 216, "policy_gate_inputs": 12, "bms_limit_update": 36}
    result_path = ROOT / "disaster-plan-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n")
    result_path.chmod(0o600)
    source_names = ("emulate_disaster_plan.py", "emulate_feature_candidates.py", "emulate_charging_followup.py",
                    "emulate_clock_semantics.py", "emulate_general_settings.py", "emulate_soc_cap_handler.py",
                    "emulate_tariff_power_policy.py", "emulate_tou_controller_encoding.py", "emulate_tou_d9.py", "replay_io.py")
    package = Path(__file__).resolve().parent
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": {"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in source_names}}
    manifest_path = ROOT / "disaster-plan-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2)+"\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
