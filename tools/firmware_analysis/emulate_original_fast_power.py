#!/usr/bin/env python3
"""Original A1761 1.5.9 charging segments, thermal limits and DSP requests.

Offline synthetic controller replay. Existing charging-session queue records
are captured before transport; DSP/electrical enforcement is not simulated.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
)

from emulate_original_fast_retention import (
    BMS, CEILING, CTX, FAST_FLAGS, IMAGE, IMAGE_SHA256, PAYLOAD, StatusMachine,
)
from emulate_original_power_paths import AC_STATE, DESCRIPTOR, VARIANT

SEGMENT = 0x2000041b
TABLE = (
    (600, 1500, 3000, 3210, 1500),
    (600, 1500, 2400, 3090, 1500),
    (600, 1200, 1800, 2400, 1500),
    (600, 1200, 1200, 1500, 1500),
    (600, 1200, 1200, 1500, 1500),
)


class ChargeMachine(StatusMachine):
    def __init__(self, image):
        self.diagnostics = []
        super().__init__(image)

    def step(self, uc, address, size, data):
        if address == 0x0800ccf0 and uc.reg_read(UC_ARM_REG_R0) == 0x0802b47c:
            self.diagnostics.append({"ChgVot": uc.reg_read(UC_ARM_REG_R1),
                "ChgCurr": uc.reg_read(UC_ARM_REG_R2),
                "AcChgPower": uc.reg_read(UC_ARM_REG_R3)})
        super().step(uc, address, size, data)

    def prepare(self, soc=50, fast=False, variant=0, temperature=25):
        self.seed(soc=soc, state=2)
        self.put(VARIANT, variant, "B")
        self.put(FAST_FLAGS, int(fast) << 2, "B")
        self.run(0x080173b8, soc)
        self.put(SEGMENT, self.uc.reg_read(UC_ARM_REG_R0), "B")
        self.put(BMS + 8, 38000)
        self.put(BMS + 0x34, 3400, "H")
        self.put(BMS + 0x36, 3350, "H")
        self.temperature(temperature)
        self.put(AC_STATE + 3, 1, "B")

    def temperature(self, value):
        self.put(BMS + 0x20, value, "B")
        self.put(BMS + 0x21, value, "B")

    def trace(self):
        self.calls.clear()
        self.diagnostics.clear()
        self.policy()
        descriptor = list(struct.unpack("<3H", self.uc.mem_read(DESCRIPTOR, 6)))
        dirty = bool(self.get(DESCRIPTOR + 6, "B"))
        expected_diagnostic = {"ChgVot": descriptor[1] * 100,
            "ChgCurr": descriptor[2] * 10, "AcChgPower": descriptor[0]}
        assert self.diagnostics == ([expected_diagnostic] if dirty else [])
        self.queued.clear()
        self.run(0x0801f570, stop=0x0801f6a2)
        assert not self.events and not self.calls
        assert len(self.queued) == int(dirty)
        wire = None
        if dirty:
            assert self.queued[0]["register"] == 4
            wire = list(struct.unpack("<6H", bytes.fromhex(self.queued[0]["payload"])))
            assert wire == [self.get(BMS + 8) // 100, descriptor[1],
                            max(10, descriptor[2] // 10), min(descriptor[0], 1600), 0, 280]
        return {"saved_d1_watts": self.get(CEILING, "H"), "e5": self.e5().hex(),
                "segment": self.get(SEGMENT, "B"), "descriptor": descriptor,
                "new_register4_words": wire,
                "new_diagnostic": expected_diagnostic if dirty else None}

    def settings_except_ceiling(self):
        result = self.protected()
        del result["saved_power_ceiling"]
        return result


def run_suite(image):
    if not __debug__:
        raise RuntimeError("Assertions are required; do not use Python -O")
    m = ChargeMachine(image)
    result = {"image_sha256": IMAGE_SHA256, "unicorn_version": unicorn.__version__,
        "scope": "Synthetic main1.5.9 requests only; no DSP, electrical or main1.7.1 validation",
        "fast_table": [], "policy_to_dsp": [], "thermal_hysteresis": [],
        "voltage_segment_advance": [], "ceiling_during_fast": []}
    offset = 0x08028bb0 - 0x08005000
    assert list(image[offset:offset + 5]) == [80, 90, 95, 99, 101]
    assert list(image[offset + 5:offset + 10]) == [10, 15, 20, 50, 100]
    raw_rows = struct.unpack_from("<25H", image, offset + 10)
    assert raw_rows == tuple(value for row in TABLE for value in row)
    for segment, (column, temperature) in itertools.product(
            range(5), enumerate((9, 10, 15, 20, 50))):
        m.reset()
        m.run(0x08017374, temperature, registers={UC_ARM_REG_R1: segment})
        actual = m.uc.reg_read(UC_ARM_REG_R0)
        assert actual == TABLE[segment][column]
        result["fast_table"].append({"segment": segment, "temperature_c": temperature,
                                      "raw_current_limit": actual})

    for soc, fast, variant in itertools.product((50, 85, 92, 97, 99), (False, True), (0, 1)):
        m.prepare(soc=soc, fast=fast, variant=variant)
        before = m.protected()
        trace = m.trace()
        segment = (50, 85, 92, 97, 99).index(soc)
        expected_current = TABLE[segment][3] if fast else (1170 if variant else 2340)
        assert trace["descriptor"] == [1000 if fast and variant else 1400 if fast else 910,
                                       568 if variant else 392, expected_current]
        assert m.protected() == before
        result["policy_to_dsp"].append({"soc": soc, "fast": fast,
            "variant_byte": variant, "temperature_c": 25, **trace,
            "saved_settings_and_outputs_unchanged": True})

    for fast, temperatures, expected in (
            (False, (25, 24, 23), (2340, 2340, 1500)),
            (True, (20, 19, 18), (3210, 3210, 3000))):
        m.prepare(fast=fast, temperature=temperatures[0])
        traces = []
        for temperature, current in zip(temperatures, expected):
            m.temperature(temperature)
            trace = m.trace()
            assert trace["descriptor"][2] == current
            traces.append({"temperature_c": temperature, **trace})
        result["thermal_hysteresis"].append({"fast": fast, "traces": traces,
            "note": "One-degree downward boundary crossing can retain previous current limit"})

    for pack_mv, expected_segments, expected_currents in (
            (39199, [0] * 7, [3210] * 7),
            (39200, [0] * 5 + [1, 1], [3210] * 6 + [3090])):
        m.prepare(soc=50, fast=True)
        m.put(BMS + 8, pack_mv)
        m.put(BMS + 0x34, 3565, "H")
        before = m.protected()
        traces = [m.trace() for _ in range(7)]
        assert [r["segment"] for r in traces] == expected_segments
        assert [r["descriptor"][2] for r in traces] == expected_currents
        assert m.protected() == before
        result["voltage_segment_advance"].append({"soc_held_constant": 50,
            "pack_voltage_mv": pack_mv, "max_cell_mv": 3565,
            "traces": traces, "clock_frequency_not_assumed": True})

    m.prepare()
    original = m.protected()
    guards = m.settings_except_ceiling()
    traces = []

    def record(step):
        traces.append({"step": step, **m.trace()})
        assert m.settings_except_ceiling() == guards

    record("baseline")
    m.set_fast(True)
    record("fast_on")
    m.put(PAYLOAD + 6, 300, "H")
    m.run(0x0800b908, CTX)
    record("saved_ceiling_300_while_fast")
    m.set_fast(False)
    record("fast_off_uses_new_saved_ceiling")
    m.put(PAYLOAD + 6, 1000, "H")
    m.run(0x0800b908, CTX)
    record("restore_saved_ceiling")
    assert [r["descriptor"][0] for r in traces] == [910, 1400, 1400, 273, 910]
    assert [r["saved_d1_watts"] for r in traces] == [1000, 1000, 300, 300, 1000]
    assert traces[2]["new_register4_words"] is None
    assert m.settings_except_ceiling() == guards
    assert m.protected() == original
    result["ceiling_during_fast"].append({"traces": traces,
        "entire_original_protected_snapshot_restored": True,
        "same_descriptor_does_not_enqueue_a_new_update": True})
    result["cases"] = sum(len(value) for value in result.values() if isinstance(value, list))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE,
                        help="Decoded original A1761 main1.5.9; SHA-256 checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
