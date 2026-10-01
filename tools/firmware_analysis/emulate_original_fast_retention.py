#!/usr/bin/env python3
"""Original C1000 1.5.9: live Fast status and complete-policy retention.

Runs public controller instructions with synthetic BMS/RAM state. No radio,
network, physical outputs or real clock advancement is involved. Inherits the
RAM-only write guard and captured output/persistence hooks from earlier tools.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import unicorn
from unicorn.arm_const import (
    UC_ARM_REG_R1, UC_ARM_REG_R4, UC_ARM_REG_R5,
    UC_ARM_REG_R9, UC_ARM_REG_R10,
)

from emulate_original_timers_modes import (
    CACHE, CEILING, CTX, FLAGS, IMAGE, IMAGE_SHA256, INFO,
    OUTPUT, PAYLOAD, SMART, TIMER, TimerMachine,
)

BMS = 0x200005d4
INPUT_FLAGS = 0x20000690
POLICY_STATE = 0x20000419
FAST_FLAGS = TIMER + 0x14
FAST_REQUEST_CACHE = CACHE + 0xa4
POLICY_ITERATIONS = 120


class StatusMachine(TimerMachine):
    def e5(self) -> bytes:
        # Execute the complete tag/type/getter/store block, not a Python decoder.
        self.run(0x080093cc, stop=0x080093f0,
                 registers={UC_ARM_REG_R1: 0, UC_ARM_REG_R4: OUTPUT,
                            UC_ARM_REG_R9: 2, UC_ARM_REG_R10: 1})
        value = bytes(self.uc.mem_read(OUTPUT, 4))
        assert value[:3] == bytes.fromhex("e50201")
        return value[2:]

    def protected(self) -> dict:
        return {
            "output_and_charge_flags": self.get(FLAGS),
            "saved_power_ceiling": self.get(CEILING, "H"),
            "full_f8": self.f8().hex(),
            "saved_smart_block": bytes(self.uc.mem_read(SMART, 24)).hex(),
            "timer_words": bytes(self.uc.mem_read(TIMER, 16)).hex(),
            "non_fast_flags": self.get(FAST_FLAGS, "B") & ~4,
        }

    def seed(self, *, soc: int = 100, present: int = 1,
             selected_bms: int = 0, state: int = 0) -> None:
        self.reset()
        self.put(FLAGS, 0x30)  # AC output and charging gate, not measured power.
        self.put(INPUT_FLAGS, present, "B")
        self.put(FAST_FLAGS, 4, "B")
        self.put(FAST_REQUEST_CACHE, 1, "B")
        self.put(CEILING, 1000, "H")
        self.uc.mem_write(0x200004ab, bytes((2, 1)))
        self.put(BMS + 0x33, selected_bms, "B")
        self.put(BMS + 0x26, selected_bms * 2, "B")
        for pointer in (BMS, BMS + 0x38):
            self.put(pointer + 0x20, 25, "B")
            self.put(pointer + 0x21, 25, "B")
            self.put(pointer + 0x23, soc, "B")
        self.put(POLICY_STATE, state, "B")

    def policy(self) -> None:
        self.run(0x08014118, count=200000)

    def set_fast(self, enabled: bool) -> None:
        self.put(PAYLOAD + 6, int(enabled), "B")
        self.run(0x0800bd00, CTX)

    def battery_states(self, primary: int, expansion: int) -> dict:
        for pointer, code in ((BMS, primary), (BMS + 0x38, expansion)):
            # Actual parser copy: incoming uint16 +34 to BMS uint8 +25.
            # The enclosing frame validation and unrelated fields are not run.
            self.put(PAYLOAD + 0x34, code, "H")
            self.run(0x080134c2, stop=0x080134c8,
                     registers={UC_ARM_REG_R4: PAYLOAD + 0x3c,
                                UC_ARM_REG_R5: pointer + 0x14})
        # Both real getters/stores, then the actual A2..CF serializer prefix.
        self.run(0x08010c88, stop=0x08010c9a,
                 registers={UC_ARM_REG_R4: INFO})
        self.put(OUTPUT + 512, 0, "H")
        self.run(0x08009164, OUTPUT, stop=0x0800924a,
                 registers={UC_ARM_REG_R1: OUTPUT + 512})
        raw = bytes(self.uc.mem_read(OUTPUT, 214))
        fields, offset = {}, 0
        while offset < len(raw):
            tag, size = raw[offset:offset + 2]
            value = raw[offset + 2:offset + 2 + size]
            assert len(value) == size and tag not in fields
            fields[tag] = value
            offset += size + 2
        assert offset == len(raw)
        assert fields[0xbf] == bytes((1, primary))
        assert fields[0xc0] == bytes((1, expansion))
        return {"bf": fields[0xbf].hex(), "c0": fields[0xc0].hex()}


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Replay assertions are required; do not use Python -O")
    m = StatusMachine(image)
    result = {
        "image_sha256": IMAGE_SHA256,
        "unicorn_version": unicorn.__version__,
        "scope": "Original A1761 main 1.5.9; synthetic state, not a 1.7.1 or electrical test",
        "complete_policy_retention": [], "runtime_vs_request_cache": [],
        "fast_full_f8_roundtrip": [], "bms_exception_retention": [],
        "defaults_clear_block": [], "battery_state_readback": [],
    }

    for soc, state, selected, present in itertools.product(
            (99, 100), range(8), (0, 1), (0, 1)):
        m.seed(soc=soc, present=present, selected_bms=selected, state=state)
        before = m.protected()
        visited_states = {state}
        for _ in range(POLICY_ITERATIONS):
            m.policy()
            visited_states.add(m.get(POLICY_STATE, "B"))
            assert m.e5() == bytes((1, present))
            assert m.get(FAST_REQUEST_CACHE, "B") == 1
            assert m.protected() == before
            assert not m.events and not m.queued and not m.calls
        result["complete_policy_retention"].append({
            "soc": soc, "initial_policy_state": state,
            "selected_bms": selected, "input_present": bool(present),
            "callback_iterations": POLICY_ITERATIONS,
            "visited_policy_states": sorted(visited_states),
            "retained_e5": m.e5().hex(),
            "full_f8_and_protected_state_unchanged": True,
            "last_requested_fast_after_policy": 1,
        })

    for selected, (offset, expected_state) in itertools.product(
            (0, 1), ((0x2d, 5), (0x31, 7))):
        m.seed(soc=99, state=1, selected_bms=selected)
        m.put(BMS + 0x38 * selected + offset, 1, "B")
        before = m.protected()
        for _ in range(POLICY_ITERATIONS):
            m.policy()
            assert m.e5() == bytes((1, 1))
            assert m.get(POLICY_STATE, "B") == expected_state
            assert m.protected() == before
            assert not m.events and not m.queued and not m.calls
        result["bms_exception_retention"].append({
            "selected_bms": selected, "bms_byte_offset": f"{offset:02x}",
            "raw_value": 1, "policy_state": expected_state,
            "callback_iterations": POLICY_ITERATIONS, "typed_e5": m.e5().hex(),
            "full_f8_and_protected_state_unchanged": True,
        })

    for runtime, cached in itertools.product((0, 1), repeat=2):
        m.seed()
        m.put(FAST_FLAGS, runtime << 2, "B")
        m.put(FAST_REQUEST_CACHE, cached, "B")
        assert m.e5() == bytes((1, runtime))
        assert m.get(FAST_REQUEST_CACHE, "B") == cached
        result["runtime_vs_request_cache"].append({
            "runtime_fast": runtime, "last_requested_fast": cached,
            "typed_e5": m.e5().hex(), "cache_changed": False,
        })

    m.seed()
    before = m.protected()
    # Only the clear call within broad defaults is executed. The surrounding
    # defaults routine changes many settings and is not a safe live probe.
    m.run(0x08023b9e, stop=0x08023ba4)
    assert m.e5() == bytes((1, 0))
    assert m.get(FAST_REQUEST_CACHE, "B") == 1
    assert m.protected() == before
    assert not m.events and not m.queued and not m.calls
    result["defaults_clear_block"].append({
        "typed_e5": m.e5().hex(), "last_requested_fast": 1,
        "scope": "Only 08023b9e..08023ba4, not the complete defaults routine",
    })

    for initial, timer_bits in itertools.product((0, 1), (0, 3)):
        m.seed()
        m.put(FAST_FLAGS, timer_bits | initial << 2, "B")
        m.put(FAST_REQUEST_CACHE, initial, "B")
        if timer_bits:
            m.put(TIMER, 600)
            m.put(TIMER + 8, 900)
        before = m.protected()
        for enabled in (not initial, bool(initial)):
            m.calls.clear()
            m.set_fast(enabled)
            assert m.calls == ["command_acknowledgement"]
            m.calls.clear()
            for _ in range(4):
                m.policy()
                assert m.e5() == bytes((1, enabled))
                assert m.protected() == before
                assert not m.events and not m.queued and not m.calls
        assert m.get(FAST_REQUEST_CACHE, "B") == initial
        result["fast_full_f8_roundtrip"].append({
            "initial_fast": bool(initial), "seeded_timer_enable_bits": timer_bits,
            "full_f8_before_and_after": before["full_f8"],
            "settings_and_timers_restored": True,
            "note": "Timer callback is not run; no claim about elapsed countdown safety",
        })

    for primary, expansion in itertools.product((0, 1, 2, 255), repeat=2):
        m.seed()
        before = m.protected()
        fields = m.battery_states(primary, expansion)
        assert m.protected() == before
        assert not m.events and not m.queued and not m.calls
        result["battery_state_readback"].append({
            "primary_raw_state": primary, "expansion_raw_state": expansion,
            **fields, "unrecognized_value_preserved": True,
        })
    result["cases"] = sum(len(value) for value in result.values() if isinstance(value, list))
    result["policy_callback_executions"] = 68 * POLICY_ITERATIONS + 4 * 2 * 4
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE,
                        help="Decoded original C1000 main 1.5.9; SHA-256 checked")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
