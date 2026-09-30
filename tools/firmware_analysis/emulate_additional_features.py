"""C1000 main 1.1.4.9 battery/PV/error telemetry replay; no hardware access.

Actual sensor getters, power getters, tag serializers and current-error setter
execute against synthetic BMS/port/status RAM. Memory copy/clear, logger, tick
and error-report timer services are substitutes. BMS/DSP receive, ADC, physical
input detection, actual fault production and app behavior are not modeled.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_clock_semantics import ClockMachine, ROOT, BUFFER, LENGTH, DESC
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, firmware_image

os.umask(0o077)
BUILDERS = {0xa3: (0x0801a2a8, 14), 0xa5: (0x08019798, 6),
            0xa6: (0x0801a9f4, 10), 0xa8: (0x08019e90, 4)}


class TelemetryMachine(ClockMachine):
    def __init__(self):
        super().__init__()
        self.tick = 10000
        self.timer_calls = []

    def step(self, uc, address, size, data):
        r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if address == 0x080052a8:
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            self.back(r0)
        elif address == 0x080052da:
            uc.mem_write(r0, bytes(r1))
            self.back(r0)
        elif address == 0x08010eb0:
            self.back(self.tick)
        elif address in (0x08010998, 0x0801095c):
            self.timer_calls.append(hex(address))
            self.back(0)
        elif any(lo <= address < hi for lo, hi in (
            (0x08018384, 0x08018546), (0x08019798, 0x08019846),
            (0x0801a724, 0x0801a784), (0x08019e90, 0x08019f04),
            (0x0801a518, 0x0801a55c), (0x08019444, 0x0801944e),
            (0x0801a9f4, 0x0801aa8a), (0x0801a2a8, 0x0801a3a6),
            (0x080181cc, 0x080181da), (0x080182e4, 0x080182ec),
            (0x0801a598, 0x0801a5a0), (0x0801aab8, 0x0801aae0),
            (0x0801b03c, 0x0801b046), (0x0801a214, 0x0801a21c),
            (0x08015494, 0x0801549e), (0x08007030, 0x08007088))):
            return
        else:
            super().step(uc, address, size, data)

    def call(self, address, argument=0):
        self.run(address, argument)
        return self.uc.reg_read(UC_ARM_REG_R0)

    def serialize(self, tag, *, mode=1, prior=None):
        address, size = BUILDERS[tag]
        self.uc.mem_write(BUFFER, bytes(256))
        if prior is not None:
            assert len(prior) == size
            self.uc.mem_write(BUFFER+1, prior)
        self.uc.mem_write(LENGTH, b"\0\0")
        self.uc.mem_write(DESC, struct.pack("<B3xIIHBx", 4, BUFFER, LENGTH, 255, mode))
        self.run(address, DESC)
        length = int.from_bytes(self.uc.mem_read(LENGTH, 2), "little")
        body = bytes(self.uc.mem_read(BUFFER, length))
        assert length == size+1 and body[0] == size and body[1] == 4
        return body[1:]


def main():
    image = firmware_image()
    # Establish the field registration independently of supplied descriptor tags.
    registrations = {0xa3: 0x08032d88, 0xa5: 0x08032da0, 0xa6: 0x08032dac, 0xa8: 0x08032dc4}
    for tag, address in registrations.items():
        entry = image[address-0x08005000:address-0x08005000+8]
        assert entry[:2] == bytes((tag, 4))
        assert int.from_bytes(entry[4:], "little") == BUILDERS[tag][0] | 1
    results = {"battery_temperature_and_fixed_health": [], "pv_input_and_power_mirror": [],
               "pv_incremental_update": [], "current_error_code": []}
    temperatures = ((0, 10, 20, 30), (20, 30, 40, 50), (25, 25, 25, 25),
                    (10, 20, 30, 40), (11, 20, 30, 40), (-20, -10, 0, 10), (26, 26, 26, 26))
    for seed in (0, 0x55, 0xaa, 0xff):
        for soc in (0, 50, 100):
            for celsius in temperatures:
                m = TelemetryMachine()
                m.uc.mem_write(0x20003fec, bytes((seed,))*0x100)
                m.uc.mem_write(0x2000040c, bytes(0x20))
                m.uc.mem_write(0x2000041d, struct.pack("<H", soc))
                raw_temps = tuple(2731+10*t for t in celsius)
                m.uc.mem_write(0x2000403f, struct.pack("<4H", *raw_temps))
                # Change unrelated synthetic aggregate flows too; these influence
                # A5[2] but must not turn the fixed100 field into measured SOH.
                m.uc.mem_write(0x20002198, struct.pack("<H", seed))
                m.uc.mem_write(0x200021a6, struct.pack("<H", 255-seed))
                a5 = m.serialize(0xa5)
                average = int(((sum(raw_temps) >> 2)-2731)/10)
                expected = max(celsius) if average > 25 else min(celsius)
                observed = int.from_bytes(a5[1:2], "little", signed=True)
                assert observed == expected and a5[3] == soc and a5[4] == 100
                assert m.call(0x08018384, 27) == 100
                results["battery_temperature_and_fixed_health"].append({
                    "synthetic_bms_fill_byte": seed, "soc": soc, "sensor_temperatures_c": celsius,
                    "truncated_mean_c": average, "a5_temperature_c": observed,
                    "a5_soc": a5[3], "a5_reported_health_byte": a5[4],
                    "health_getter_27_returns_constant": True})
    for enabled in (False, True):
        for watts in (0, 1, 6, 123, 600, 65535):
            for ac_watts in (0, 750):
                m = TelemetryMachine()
                m.uc.mem_write(0x20000164, struct.pack("<I", 0x20 | int(enabled)))
                m.uc.mem_write(0x20002198+10, struct.pack("<HH", ac_watts, watts))
                a8, a6 = m.serialize(0xa8), m.serialize(0xa6)
                assert a8 == bytes((4, int(enabled)))+struct.pack("<H", watts)
                assert int.from_bytes(a6[3:5], "little") == ac_watts
                assert a6[5:7] == a8[2:4]
                results["pv_input_and_power_mirror"].append({
                    "synthetic_dc_input_state_bit": enabled, "synthetic_pv_power_word": watts,
                    "synthetic_ac_power_word": ac_watts, "a8_hex": a8.hex(),
                    "a6_ac_input_word": ac_watts, "a6_dc_input_word": watts,
                    "power_mirror_equal": True})
    for old_power in (0, 123, 600):
        for enabled in (False, True):
            m = TelemetryMachine()
            m.uc.mem_write(0x20000164, struct.pack("<I", int(enabled)))
            m.uc.mem_write(0x20002198+12, struct.pack("<H", 999))
            prior = bytes((4, int(not enabled)))+struct.pack("<H", old_power)
            a8 = m.serialize(0xa8, mode=3, prior=prior)
            assert a8 == bytes((4, int(enabled)))+struct.pack("<H", old_power)
            results["pv_incremental_update"].append({"prior_power_word": old_power,
                "new_ram_power_word": 999, "new_input_state_bit": enabled,
                "a8_hex": a8.hex(), "power_preserved_from_previous_buffer": True})
    for old in (0, 8, 36):
        for new in (0, 1, 2, 8, 27, 28, 32, 33, 36, 47, 255):
            m = TelemetryMachine()
            m.uc.mem_write(0x200000c0, bytes((old,)))
            assert m.call(0x08007030, new) == 1
            a3 = m.serialize(0xa3)
            assert a3[2] == new
            m.tick += 999
            assert m.call(0x08007030, new) == 0
            m.tick += 1
            assert m.call(0x08007030, new) == 1
            results["current_error_code"].append({"old_code": old, "synthetic_new_code": new,
                "a3_error_byte": a3[2], "duplicate_within_1000_tick_units_suppressed": True,
                "same_code_after_1000_tick_units_accepted": True,
                "semantic_error_name": None})
    counts = {name: len(rows) for name, rows in results.items()}
    assert counts == {"battery_temperature_and_fixed_health": 84, "pv_input_and_power_mirror": 24,
                      "pv_incremental_update": 6, "current_error_code": 33}
    output = ROOT / "additional-features-results.json"
    output.write_text(json.dumps(results, indent=2)+"\n")
    output.chmod(0o600)
    package = Path(__file__).resolve().parent
    names = ("emulate_additional_features.py", "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": {"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in names}}
    output = ROOT / "additional-features-manifest.json"
    output.write_text(json.dumps(manifest, indent=2)+"\n")
    output.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
