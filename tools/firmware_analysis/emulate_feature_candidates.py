"""C1000 1.1.4.9 fast-charge readiness and low-power candidates, offline only.

Real native parser/handler, tariff selector, fast setter, A4 builder, selected
charging policy, validator and successful-load branch execute. Inherited
transport/persistence/LCD/timer substitutes remain. Charge-plan delivery and
history updates are recorded boundaries; battery sensor readings, RTC, network
readiness, power gate and AC-charge-available flags are synthetic. No producer
of those gate flags, peripheral/DSP, physical charging or network is simulated.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_charging_followup import NativeAcSettingsMachine, DefaultsMachine
from emulate_clock_semantics import PAYLOAD, ROOT
from emulate_general_settings import SETTINGS
from emulate_soc_cap_handler import REQUEST
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, firmware_image

os.umask(0o077)


class FeatureMachine(NativeAcSettingsMachine):
    def __init__(self, *, schedule="standard", ready=True, gate=True,
                 soc=50, current=10, ac_available=True):
        super().__init__()
        self.uc.reg_write(UC_ARM_REG_C1_C0_2, 0xf << 20)
        self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        self.soc = soc
        self.current = current
        self.plans = []
        self.sensor_reads = []
        self.history = []
        self.uc.mem_write(SETTINGS+0x2c, b"\xf7")
        self.uc.mem_write(0x200007af, bytes((int(ready),)))
        self.uc.mem_write(0x200004be, bytes((int(gate),)))
        self.uc.mem_write(0x20000164, struct.pack("<I", 0x20 | (0x40 if ac_available else 0)))
        mode, count, tariff, start, end = {
            "standard": (0, 0, 0, 0, 0),
            "peak": (1, 1, 1, 0, 24),
            "mid_peak": (1, 1, 2, 0, 24),
            "off_peak": (1, 1, 3, 0, 24),
            "gap": (1, 1, 1, 0, 1),  # Synthetic RTC is outside this slot.
        }[schedule]
        self.uc.mem_write(0x20001d76, bytes((mode, count, tariff, start, end))+bytes(15))

    def step(self, uc, address, size, data):
        if address == 0x08018384:
            kind = uc.reg_read(UC_ARM_REG_R0)
            assert kind in (2, 10, 11)
            self.sensor_reads.append(kind)
            self.back({2: self.soc, 10: 600, 11: self.current}[kind])
        elif address == 0x0802a15c:
            pointer, channel = uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)
            self.plans.append({"channel": channel, "power_voltage_current":
                               list(struct.unpack("<HHH", uc.mem_read(pointer+8*channel, 6)))})
            self.back()
        elif address == 0x080147e0:
            self.history.append(uc.reg_read(UC_ARM_REG_R0))
            self.back()
        elif address == 0x08018aac:
            self.back(0x2000f000)  # Synthetic inactive backup plan.
        elif address == 0x0800d0c8:
            self.back()
        elif any(lo <= address < hi for lo, hi in (
            (0x08014d58, 0x0801542a), (0x08018560, 0x080185ae),
            (0x08019488, 0x080194b2), (0x0801473c, 0x080147dc),
            (0x08028944, 0x08028960), (0x080288e6, 0x080288ea))):
            return
        else:
            super().step(uc, address, size, data)

    def command(self, *, fast=None, watts=None):
        assert (fast is None) != (watts is None)
        self.acked = 0
        field = bytes((0xa7, 2, 1, int(fast))) if fast is not None else bytes((0xa4, 3, 2))+struct.pack("<H", watts)
        body = bytes.fromhex("a10122")+field+bytes.fromhex("fe050300f15365")
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST+0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800bd1c, REQUEST)
        assert self.acked == 1
        return self.a4()

    def policy(self):
        self.plans.clear()
        self.sensor_reads.clear()
        self.run(0x08014d58, 0)
        return {"fast_readback": self.a4()[21], "charge_plans": self.plans.copy(),
                "sensor_kinds_read": sorted(set(self.sensor_reads)),
                "output_enabled_flag": bool(int.from_bytes(self.uc.mem_read(0x20000164, 4), "little") & 0x30)}


def main():
    firmware_image()
    results = {"fast_acceptance_and_retention": [], "fast_readiness_transitions": [], "low_power_limits": []}
    tariff_numbers = {"standard": 0, "gap": 0, "peak": 1, "mid_peak": 2, "off_peak": 3}
    for schedule in tariff_numbers:
        for ready in (False, True):
            for gate in (False, True):
                for soc in (50, 100):
                    for current in (0, 10):
                        for ac_available in (False, True):
                            machine = FeatureMachine(schedule=schedule, ready=ready, gate=gate,
                                                     soc=soc, current=current, ac_available=ac_available)
                            before = bytes(machine.uc.mem_read(SETTINGS, 0x190))
                            active = machine.run(0x0801bcc4, 0)
                            assert active == (tariff_numbers[schedule] if ready and gate else 0)
                            accepted = machine.command(fast=True)[21]
                            assert accepted == int(active == 0)
                            after = machine.policy()
                            assert after["fast_readback"] == int(gate and active == 0)
                            assert after["output_enabled_flag"]
                            assert machine.command(fast=False)[21] == 0
                            assert bytes(machine.uc.mem_read(SETTINGS, 0x190)) == before
                            assert machine.scheduled == 0
                            results["fast_acceptance_and_retention"].append({
                                "schedule": schedule, "synthetic_network_ready": ready,
                                "synthetic_power_gate": gate, "synthetic_soc": soc,
                                "synthetic_bms_current_limit": current,
                                "synthetic_ac_charge_available": ac_available,
                                "selected_tariff": active, "enable_acknowledged": True,
                                "immediate_fast_readback": accepted, "after_policy": after,
                                "disable_readback": 0, "persistent_settings_unchanged": True})
    for change in ("lose_power_gate", "network_becomes_ready", "enter_scheduled_slot", "full_bms_stops_current"):
        machine = FeatureMachine(schedule="peak" if change == "network_becomes_ready" else "gap" if change == "enter_scheduled_slot" else "standard",
                                 ready=change != "network_becomes_ready")
        assert machine.command(fast=True)[21] == 1
        before = machine.policy()
        assert before["fast_readback"] == 1
        if change == "lose_power_gate":
            machine.uc.mem_write(0x200004be, b"\0")
        elif change == "network_becomes_ready":
            machine.uc.mem_write(0x200007af, b"\1")
        elif change == "enter_scheduled_slot":
            # Advance only the synthetic RTC to the next midnight; retain the
            # existing 00:00-01:00 schedule. This is not a time-sync command.
            rtc = (1600000000 // 86400 + 1) * 86400
            machine.uc.mem_write(0x40002818, struct.pack("<II", rtc >> 16, rtc & 0xffff))
        else:
            machine.soc, machine.current = 100, 0
        after = machine.policy()
        assert after["fast_readback"] == int(change == "full_bms_stops_current")
        if change == "full_bms_stops_current":
            assert after["charge_plans"][0]["power_voltage_current"] == [1400, 600, 0]
        results["fast_readiness_transitions"].append({"change": change, "before": before, "after": after,
            "note": "Synthetic gate/RTC/sensor transition only; no gate producer or real device operation"})
    for watts in (100, 200, 300, 1200):
        for fast in (False, True):
            for soc in (50, 100):
                for current in (0, 1, 10):
                    machine = FeatureMachine(soc=soc, current=current)
                    before = bytearray(machine.uc.mem_read(SETTINGS, 0x190))
                    a4 = machine.command(watts=watts)
                    assert int.from_bytes(a4[5:7], "little") == watts
                    assert machine.run(0x0802c890, 0) == 0
                    DefaultsMachine.loaded_settings_decision(machine)
                    assert int.from_bytes(machine.a4()[5:7], "little") == watts
                    assert machine.a4()[14:16] == b"\0\0"
                    assert machine.command(fast=fast)[21] == int(fast)
                    after = machine.policy()
                    expected_power = 1400 if fast else {100: 88, 200: 176, 300: 264, 1200: 1056}[watts]
                    assert after["charge_plans"] == [{"channel": 0, "power_voltage_current": [expected_power, 600, current*10]}]
                    assert after["fast_readback"] == int(fast)
                    before[0xf:0x11] = struct.pack("<H", watts)
                    assert bytes(machine.uc.mem_read(SETTINGS, 0x190)) == bytes(before)
                    results["low_power_limits"].append({"configured_watts": watts, "fast": fast,
                        "synthetic_soc": soc, "synthetic_bms_current_limit": current,
                        "validator_invalid": 0, "loaded_power_preserved": watts,
                        "device_timeout_preserved_minutes": 0, "after_policy": after})
    expected_counts = {"fast_acceptance_and_retention": 160, "fast_readiness_transitions": 4, "low_power_limits": 48}
    assert {name: len(rows) for name, rows in results.items()} == expected_counts
    result_path = ROOT / "feature-candidates-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n")
    result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    source_names = ("emulate_feature_candidates.py", "emulate_charging_followup.py", "emulate_clock_semantics.py",
                    "emulate_general_settings.py", "emulate_soc_cap_handler.py", "emulate_tariff_power_policy.py",
                    "emulate_tou_controller_encoding.py", "emulate_tou_d9.py", "replay_io.py")
    manifest = {"hardware_access": False, "case_counts": expected_counts,
        "firmware": {"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in source_names}}
    manifest_path = ROOT / "feature-candidates-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2)+"\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": sum(expected_counts.values()), "groups": expected_counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
