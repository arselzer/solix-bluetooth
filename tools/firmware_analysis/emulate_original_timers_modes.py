#!/usr/bin/env python3
"""Original C1000 1.5.9 output timers, complete F8 and fast-charge policy.

Offline ARM replay with synthetic RTC registers. Output dispatch, persistence
and ACKs are captured/substituted; no hardware, transport or DSP execution.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

from unicorn.arm_const import (
    UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R4, UC_ARM_REG_R10, UC_ARM_REG_R11,
)

from emulate_original_power_paths import (
    CEILING, CTX, FLAGS, IMAGE, IMAGE_SHA256, OUTPUT, PAYLOAD, PowerMachine, VARIANT,
)

TIMER = 0x200020b0
CLOCK = 0x200006e0
CACHE = 0x20000c6c
INFO = 0x20001374
SMART = 0x20000498
DAY = 1767225600  # Synthetic 2026-01-01 00:00:00 UTC.
DOMAINS = {
    "ac": {"handler": 0x0800b990, "offset": 0, "mask": 1,
           "last": 0x10, "switch": 0x9f, "dispatch": 0x08012588},
    "car_dc": {"handler": 0x0800ba88, "offset": 8, "mask": 2,
               "last": 0x0c, "switch": 0xa0, "dispatch": 0x08013da4},
}


class TimerMachine(PowerMachine):
    def __init__(self, image: bytes):
        self.events = []
        super().__init__(image)
        self.uc.mem_map(0x40002000, 0x1000)
        self.uc.reg_write(UC_ARM_REG_C1_C0_2, 0xf << 20)
        self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)

    def reset(self):
        super().reset()
        self.events = []

    def step(self, uc, address, size, data):
        for name, config in DOMAINS.items():
            if address == config["dispatch"]:
                self.events.append({"domain": name,
                                    "requested_enabled": self.get(CACHE + config["switch"], "B")})
                self.back()
                return
        if address == 0x0800ccf0:
            self.back()  # Diagnostic print only.
        else:
            super().step(uc, address, size, data)

    def now(self, seconds):
        epoch = DAY + seconds
        self.uc.mem_write(0x40002818, struct.pack("<II", epoch >> 16, epoch & 0xffff))

    def write_timer(self, domain, seconds):
        self.put(PAYLOAD + 6, seconds)
        self.run(DOMAINS[domain]["handler"], CTX)

    def timer_readback(self):
        # Actual getter/store prefix of the information builder.
        self.run(0x08010b7a, stop=0x08010b86, registers={UC_ARM_REG_R4: INFO})
        self.put(OUTPUT + 512, 0, "H")
        # Actual serializer through A2 and A3 only, before unrelated fields.
        self.run(0x08009164, OUTPUT, stop=0x080091ce,
                 registers={UC_ARM_REG_R1: OUTPUT + 512})
        raw = bytes(self.uc.mem_read(OUTPUT, 14))
        assert raw[:3] == b"\xa2\x05\x03" and raw[7:10] == b"\xa3\x05\x03"
        return [int.from_bytes(raw[3:7], "little"), int.from_bytes(raw[10:14], "little")]

    def f8(self):
        # The enclosing serializer sets r10=1 at 08009214.
        self.run(0x080093f0, stop=0x08009448,
                 registers={UC_ARM_REG_R4: OUTPUT, UC_ARM_REG_R10: 1})
        raw = bytes(self.uc.mem_read(OUTPUT, 23))
        assert raw[:3] == b"\xf8\x15\x04"
        return raw[2:]


def run_suite(image: bytes):
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not use Python -O")
    m = TimerMachine(image)
    results = {"image_sha256": IMAGE_SHA256, "timer_expiry": [], "timer_cancel": [],
               "f8_mode_roundtrip": [], "fast_power": [], "fast_clear": []}
    clock_cases = (
        ("same_second", 43200, 43200, 300),
        ("one_second", 43200, 43201, 299),
        ("delayed_poll", 43200, 43300, 200),
        ("exact_expiry", 43200, 43500, 0),
        ("forward_clock_jump", 43200, 46800, 0),
        ("backward_clock_jump", 43200, 39600, 300),
        ("midnight_exact", 86399, 86400, 300),
        ("midnight_skipped", 86399, 86405, 300),
        ("same_time_next_day", 43200, 129600, 300),
    )
    for domain, enabled, case in itertools.product(DOMAINS, (0, 1), clock_cases):
        name, previous, current, expected = case
        m.reset()
        config = DOMAINS[domain]
        power_flags = 0x20 | (enabled << (4 if domain == "ac" else 1))
        m.put(FLAGS, power_flags)
        m.put(CACHE + config["switch"], enabled, "B")
        m.put(CLOCK + config["last"], previous)
        m.now(current)
        m.write_timer(domain, 300)
        m.run(0x0800a73c)
        readback = m.timer_readback()
        assert readback[config["offset"] // 8] == expected
        assert readback[1 - config["offset"] // 8] == 0
        assert m.get(TIMER + config["offset"]) == expected
        assert m.get(FLAGS) == power_flags  # Final dispatch is captured, not executed.
        if expected == 0:
            assert m.events == [{"domain": domain, "requested_enabled": 1 - enabled}]
            assert m.get(TIMER + 0x14, "B") & config["mask"] == 0
        else:
            assert m.events == []
            assert m.get(CACHE + config["switch"], "B") == enabled
        results["timer_expiry"].append({"domain": domain, "case": name,
            "initial_output": enabled, "previous_second_of_day": previous,
            "new_seconds_from_midnight": current, "remaining": expected,
            "events": m.events.copy(), "typed_readback": readback})

    for domain, seconds in itertools.product(DOMAINS, (300, 3600, 86400)):
        m.reset()
        config = DOMAINS[domain]
        m.put(FLAGS, 0x32)
        m.put(CACHE + config["switch"], 1, "B")
        m.put(TIMER + 0x14, 4, "B")  # Unrelated fast-charge bit must survive.
        m.put(CLOCK + config["last"], 43200)
        m.now(43200)
        m.write_timer(domain, seconds)
        m.run(0x0800a73c)
        assert m.timer_readback()[config["offset"] // 8] == seconds
        m.write_timer(domain, 0)
        m.now(43210)
        m.run(0x0800a73c)
        assert m.timer_readback() == [0, 0]
        assert not m.events and m.get(FLAGS) == 0x32
        assert m.get(TIMER + 0x14, "B") == 4
        # The previous display/report checkpoint is deliberately not cleared.
        assert m.get(TIMER + config["offset"] + 4) == seconds
        results["timer_cancel"].append({"domain": domain, "requested_seconds": seconds,
            "remaining_after_cancel": 0, "unrelated_fast_bit_preserved": True,
            "previous_checkpoint_retained": seconds, "output_dispatches": 0})

    for dc, ac, target in itertools.product((1, 2), (1, 2), ("dc", "ac")):
        m.reset()
        m.uc.mem_write(SMART, bytes(range(24)))
        m.uc.mem_write(0x200004ab, bytes((dc, ac)))
        before_settings = bytes(m.uc.mem_read(SMART, 24))
        before = m.f8()
        assert before == bytes((4, dc, ac, 1, 1)) + bytes(16)
        index = 1 if target == "dc" else 2
        handler = 0x0800bd48 if target == "dc" else 0x0800bd28
        stored = dc if target == "dc" else ac
        m.put(PAYLOAD + 6, int(stored == 1), "B")
        m.run(handler, CTX)
        changed = m.f8()
        expected = bytearray(before)
        expected[index] = 3 - stored
        assert changed == expected
        expected_settings = bytearray(before_settings)
        expected_settings[18 + index] = 3 - stored
        assert bytes(m.uc.mem_read(SMART, 24)) == expected_settings
        m.put(PAYLOAD + 6, int(stored == 2), "B")
        m.run(handler, CTX)
        assert m.f8() == before
        assert bytes(m.uc.mem_read(SMART, 24)) == before_settings
        results["f8_mode_roundtrip"].append({"target": target, "initial_modes": [dc, ac],
            "before": before.hex(), "changed": changed.hex(),
            "complete_f8_restored": True, "unrelated_saved_bytes_preserved": True})

    coefficient = struct.unpack_from("<d", image, 0x08014514 - 0x08005000)[0]
    assert coefficient == 0.91
    for variant, fast, watts in itertools.product((0, 1, 2), (0, 1), (100, 500, 1000)):
        m.reset()
        m.put(VARIANT, variant, "B")
        m.put(FLAGS, 0x32)
        m.put(TIMER + 0x14, fast << 2, "B")
        m.put(CEILING, watts, "H")
        m.run(0x080141ce, stop=0x080141d2)  # Actual vldr of the firmware coefficient.
        m.run(0x08014304, stop=0x08014338)
        actual = m.uc.reg_read(UC_ARM_REG_R11)
        expected = (1000 if variant else 1400) if fast else int(watts * coefficient)
        assert actual == expected
        assert m.get(CEILING, "H") == watts and m.get(FLAGS) == 0x32
        results["fast_power"].append({"variant": variant, "fast": bool(fast),
            "saved_watts": watts, "internal_power_allowance": actual,
            "saved_limit_and_output_bits_preserved": True})

    for input_flags in (0, 1, 2, 3):
        m.reset()
        m.put(FLAGS, 0x32)
        m.put(TIMER + 0x14, 3, "B")
        m.put(0x20000690, input_flags, "B")
        m.put(PAYLOAD + 6, 1, "B")
        m.run(0x0800bd00, CTX)
        assert m.get(TIMER + 0x14, "B") == 7
        assert m.calls == ["command_acknowledgement"]  # No persistence requested.
        m.run(0x0801412a, stop=0x08014136)
        expected = 7 if input_flags & 1 else 3
        assert m.get(TIMER + 0x14, "B") == expected
        assert m.get(FLAGS) == 0x32
        results["fast_clear"].append({"input_flags": input_flags,
            "fast_after_periodic_gate": bool(expected & 4), "countdown_flags_preserved": True})
    m.reset()
    m.put(TIMER + 0x14, 7, "B")
    m.put(FLAGS, 0x32)
    m.run(0x08007282, stop=0x08007288)
    assert m.get(TIMER + 0x14, "B") == 3 and m.get(FLAGS) == 0x32
    results["fast_clear"].append({"case": "portAcOut_removal_event_tail", "fast_after": False})
    results["cases"] = sum(len(value) for value in results.values() if isinstance(value, list))
    results["scope"] = "Synthetic RTC; actual calendar/timer/serializer/policy instructions; no electrical simulation"
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE,
                        help="Decoded original C1000 main 1.5.9 image; SHA-256 checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
