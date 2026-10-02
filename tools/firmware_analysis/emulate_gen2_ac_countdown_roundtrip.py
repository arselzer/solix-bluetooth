#!/usr/bin/env python3
"""Offline native 600-second AC countdown, early cancel and rearm lifecycle.

Original parser/handler, stable-output on-task fragments, sampling gate, policy,
mode-1 A4 serializer, stop scanner/worker and selected off/on reset fragments
execute against synthetic RAM. No device, transport or physical output access.
"""

import hashlib
import itertools
import json
import os
from pathlib import Path

from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R10, UC_ARM_REG_SP,
)

from emulate_clock_semantics import STACK, STOP
from emulate_general_settings import SETTINGS, TIMERS
from emulate_gen2_native_output_readiness import ReadinessMachine
from emulate_gen2_output_policy import CONFIG, COUNTDOWNS, DSP, EVENTS, TICKS
from emulate_gen2_output_stop_worker import OUTPUT, SCRATCH, STOP_FLAGS
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
AC = CONFIG["ac"]
CONTROL_REQUEST = 0x20000436
CONTROL_INDEX = 0x2000015c
CURRENT_TIMESTAMP = "fd_milliseconds"


class AcRoundtripMachine(ReadinessMachine):
    def __init__(self, *, mode=2, smart=False, counter=0, previous=False, checkpoint=0):
        super().__init__()
        assert mode in (1, 2)
        self.mode = mode
        self.put(OUTPUT, (self.get(OUTPUT) & ~0x30) | (mode << 4))
        self.put(DSP, 8 if mode == 1 else 0, "B")
        self.put(AC["state"], 1, "B")
        self.put(AC["smart"], int(smart), "B")
        self.put(AC["counter"], counter, "H")
        self.put(AC["previous"], int(previous), "B")
        self.put(COUNTDOWNS + 16, checkpoint, "H")
        self.put(COUNTDOWNS + 12, 777)
        self.put(DSP + 0x1e, 18, "h")
        self.put(CONTROL_REQUEST, 0, "H")
        # Synthetic active registration for the real request-marking helper.
        # The full deferred control callback is deliberately not executed.
        self.put(CONTROL_INDEX, 6, "B")
        self.uc.mem_write(EVENTS + 6 * 12, b"\x01" + bytes(11))
        # Voltage getter for actual AC-on initialization, ordinary 230 V.
        image = firmware_image()
        voltage_base = int.from_bytes(image[0x08019268 - 0x08005000:0x0801926c - 0x08005000], "little")
        self.put(voltage_base + 0x3e, 230, "H")

    def step(self, uc, address, size, data):
        r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if address in self.endpoints:
            self.reached_endpoint = address
            self.stopped = True
            uc.emu_stop()
        elif address == 0x08020a4c:
            assert r0 in (2, 6)
            self.back(SCRATCH if self.allocate else 0)
        elif address == 0x08027e40:
            assert r0 == 1 and r2 == 0
            raw = bytes(uc.mem_read(r1, 24))
            pointer = int.from_bytes(raw[16:20], "little")
            length = int.from_bytes(raw[12:14], "little")
            assert length in (2, 6)
            self.dsp_descriptors.append({
                "channel": int.from_bytes(raw[8:10], "little"),
                "command": int.from_bytes(raw[10:12], "little"),
                "length": length, "payload": bytes(uc.mem_read(pointer, length)).hex(),
                "accepted_by_substitute": self.queue_accept,
            })
            self.back(int(self.queue_accept))
        elif any(lo <= address < hi for lo, hi in (
            (0x08024954, 0x08024a26), (0x08024a26, 0x08024a88),
            (0x0800ce98, 0x0800cf34), (0x0802a870, 0x0802a940),
            (0x08019260, 0x08019266), (0x0801a580, 0x0801a588),
            (0x08013708, 0x08013720), (0x08008142, 0x0800814a),
        )):
            return
        else:
            super().step(uc, address, size, data)

    def native_timer(self, seconds):
        return self.setting("ac", 0xa3, b"\x03" + seconds.to_bytes(4, "little"), CURRENT_TIMESTAMP)

    def on_fragment(self, *, start=0x0802494a, endpoints=(0x08024ad8, 0x08024a88)):
        self.endpoints = set(endpoints)
        self.reached_endpoint = None
        self.stopped = False
        for register, value in (
            (UC_ARM_REG_SP, STACK), (UC_ARM_REG_LR, STOP | 1),
            (UC_ARM_REG_R4, 0), (UC_ARM_REG_R5, 1),
            (UC_ARM_REG_R6, AC["state"]), (UC_ARM_REG_R7, DSP),
            (UC_ARM_REG_R10, OUTPUT),
        ):
            self.uc.reg_write(register, value)
        try:
            self.uc.emu_start(start | 1, 0, count=30000)
            assert self.stopped and self.reached_endpoint is not None
        finally:
            self.endpoints = set()
        return self.reached_endpoint

    def task_sample(self, *, eligible=True):
        self.put(TIMERS + AC["sample_timer"] * 20, 3 if eligible else 2, "B")
        if eligible:
            self.put(TICKS, self.get(TICKS) + 2000)
        reached = self.on_fragment()
        assert reached == (0x08024ad8 if eligible else 0x08024a88)
        return self.lifecycle()

    def lifecycle(self):
        return {**self.snapshot(), "counter": self.get(AC["counter"], "H"),
                "previous_countdown": self.get(AC["previous"], "B"),
                "ac_task_initialized": self.get(AC["state"], "B"),
                "countdown_block_hex": bytes(self.uc.mem_read(COUNTDOWNS, 24)).hex()}


def main():
    firmware_image()
    rows = []

    # Bounded trial through stable on-task dispatch/gate/mode match/policy.
    # Counterfactual uses the same sample sequence without a timer write.
    for mode, smart, counter, checkpoint, samples in itertools.product(
            (1, 2), (False, True), (0, 598), (0, 55), (0, 1, 5)):
        m = AcRoundtripMachine(mode=mode, smart=smart, counter=counter, checkpoint=checkpoint)
        reference = AcRoundtripMachine(mode=mode, smart=smart, counter=counter, checkpoint=checkpoint)
        baseline, saved, a4_before = m.lifecycle(), m.saved(), m.a4()
        request = m.native_timer(600)
        ack = m.lifecycle()
        assert ack["ac_remaining"] == 600 and m.saved() == saved
        assert m.a4()[1:5] == (600).to_bytes(4, "little")
        assert m.task_sample(eligible=False)["ac_remaining"] == 600
        reference.task_sample(eligible=False)
        active = []
        for i in range(samples):
            active.append(m.task_sample())
            reference.task_sample()
            assert active[-1]["ac_remaining"] == 600 - 2 * (i + 1)
            assert active[-1]["counter"] == 0
        m.native_timer(0)
        cancel = m.lifecycle()
        assert cancel["ac_remaining"] == 0 and not cancel["countdown_enable"] & 1
        assert cancel["ac_checkpoint"] == 0
        assert m.saved() == saved and m.a4() == a4_before
        skipped = m.task_sample(eligible=False)
        reference.task_sample(eligible=False)
        assert skipped["previous_countdown"] == int(bool(samples))
        after = m.task_sample()
        reference_after = reference.task_sample()
        assert after["previous_countdown"] == 0
        assert after["output_word"] == baseline["output_word"]
        assert after["stop_flags"] == after["event_pending"] == 0
        assert m.saved() == saved and m.a4() == a4_before
        assert not m.dsp_descriptors
        rows.append({"group": "bounded_600_second_cancel", "mode": mode,
                     "smart": smart, "counter_before": counter,
                     "checkpoint_before": checkpoint, "eligible_active_samples": samples,
                     "request_tlv_hex": request, "baseline": baseline,
                     "after_ack": ack, "active_samples": active,
                     "after_zero_ack": cancel, "after_skipped_sample": skipped,
                     "after_eligible_inactive_sample": after,
                     "counterfactual_without_timer": reference_after,
                     "a4_before": a4_before.hex(), "a4_restored": m.a4().hex(),
                     "all_415_saved_bytes_preserved": True,
                     "counter_history_matches_counterfactual": after["counter"] == reference_after["counter"],
                     "countdown_block_restored_exactly": after["countdown_block_hex"] == baseline["countdown_block_hex"]})

    # Reachable rapid rearm: before the first sample previous remains zero;
    # after an active sample previous is one but that sample cleared counter.
    for mode, counter, first_samples, inactive_between in itertools.product(
            (1, 2), (0, 598, 65534), (0, 1), (0, 1)):
        m = AcRoundtripMachine(mode=mode, smart=True, counter=counter)
        saved, baseline = m.saved(), m.lifecycle()
        m.native_timer(600)
        for _ in range(first_samples):
            m.task_sample()
        m.native_timer(0)
        for _ in range(inactive_between):
            m.task_sample()
        before_rearm = m.lifecycle()
        m.native_timer(600)
        after_sample = m.task_sample()
        assert after_sample["ac_remaining"] == 598 and after_sample["counter"] == 0
        assert after_sample["stop_flags"] == after_sample["event_pending"] == 0
        m.native_timer(0)
        m.task_sample()
        assert m.saved() == saved and m.get(OUTPUT) == baseline["output_word"]
        rows.append({"group": "ordinary_cancel_rearm", "mode": mode,
                     "initial_counter": counter, "first_active_samples": first_samples,
                     "inactive_samples_between": inactive_between,
                     "before_rearm": before_rearm, "after_rearm_sample": after_sample,
                     "restored": m.lifecycle(), "all_415_saved_bytes_preserved": True})

    # Adverse seeded hidden combinations are not claimed reachable through the
    # ordinary arm/cancel/rearm sequences above. Same A4 baseline hides them.
    for previous, counter in itertools.product((False, True), (0, 598, 600, 28800, 65534, 65535)):
        m = AcRoundtripMachine(smart=True, counter=counter, previous=previous)
        baseline, a4_before = m.lifecycle(), m.a4()
        m.native_timer(600)
        after = m.task_sample()
        elapsed = ((counter + 2) & 0xffff) if previous else 2
        expires = elapsed >= 600
        assert bool(after["stop_flags"] & 2) == expires
        assert bool(after["event_pending"]) == expires
        assert after["ac_remaining"] == (600 if expires else 600 - elapsed)
        m.native_timer(0)
        after_zero = m.lifecycle()
        assert bool(after_zero["event_pending"]) == expires
        rows.append({"group": "adverse_hidden_state_not_established_reachable",
                     "previous": previous, "initial_counter": counter,
                     "a4_inactive_baseline": a4_before.hex(), "baseline": baseline,
                     "elapsed_used": elapsed, "after_sample": after,
                     "after_zero_ack": after_zero,
                     "ordinary_lifecycle_reachability_proved": False})

    # Native AC output off is deferred, not a synchronous counter reset.
    for previous, counter in itertools.product((False, True), (0, 65534)):
        m = AcRoundtripMachine(counter=counter, previous=previous)
        before, saved = m.lifecycle(), m.saved()
        m.setting("ac", 0xa2, b"\x01\x00", CURRENT_TIMESTAMP)
        after = m.lifecycle()
        assert after == before and m.saved() == saved
        assert m.get(CONTROL_REQUEST, "H") == 0x40
        assert m.get(EVENTS + 6 * 12 + 1, "B") == 1
        rows.append({"group": "native_ac_off_handler_deferred",
                     "previous": previous, "counter": counter, "before": before,
                     "after_ack": after, "request_halfword": 0x40,
                     "synthetic_registered_event_pending": True,
                     "full_control_callback_executed": False})

    # Off-task then on-initialization clears the counter, preserves previous.
    # Deferred output-word transitions are synthetic boundaries; DSP descriptor
    # builder is real, final allocation/queue outcomes are substitutes.
    for previous, counter, accepted in itertools.product((False, True), (598, 65534), (False, True)):
        m = AcRoundtripMachine(smart=True, counter=counter, previous=previous)
        before, saved = m.lifecycle(), m.saved()
        m.put(OUTPUT, m.get(OUTPUT) & ~0x30)
        assert m.dispatch("ac") == 0x08024b92
        off = m.lifecycle()
        assert off["counter"] == 0 and off["ac_task_initialized"] == 0
        assert off["previous_countdown"] == int(previous)
        m.put(OUTPUT, before["output_word"])
        m.queue_accept = accepted
        assert m.on_fragment(start=0x08024954, endpoints=(0x080249f6,)) == 0x080249f6
        on = m.lifecycle()
        assert on["counter"] == 0 and on["previous_countdown"] == int(previous)
        assert on["ac_task_initialized"] == int(accepted)
        assert m.saved() == saved and len(m.dsp_descriptors) == 1
        m.queue_accept = True
        # Finish/retry normal initialization before arming; no hardware implied.
        if not accepted:
            m.on_fragment(start=0x08024954, endpoints=(0x080249f6,))
        m.native_timer(600)
        armed = m.task_sample()
        assert armed["ac_remaining"] == 598
        assert armed["stop_flags"] == armed["event_pending"] == 0
        m.native_timer(0)
        assert m.saved() == saved
        rows.append({"group": "off_on_task_reset", "previous": previous,
                     "initial_counter": counter, "on_queue_accepts": accepted,
                     "before": before, "after_off_task": off,
                     "after_on_initialization": on, "after_timer_sample": armed,
                     "descriptors": m.dsp_descriptors,
                     "deferred_output_flag_transition_substituted": True,
                     "all_415_saved_bytes_preserved": True})

    # New full 600-second sampled interleaving complements the previous direct
    # two-second expiry worker cases. Check 415 bytes with saved recovery = 1.
    for eligible_samples in (299, 300):
        m = AcRoundtripMachine()
        m.put(SETTINGS + 0x22, 1, "B")
        before, saved = m.lifecycle(), m.saved()
        m.native_timer(600)
        for _ in range(eligible_samples):
            m.task_sample()
        before_zero = m.lifecycle()
        expired = eligible_samples == 300
        assert bool(before_zero["event_pending"]) == expired
        m.native_timer(0)
        after_zero = m.lifecycle()
        m.drain()
        after_worker = m.lifecycle()
        expected_saved = bytearray(saved)
        if expired:
            expected_saved[0x22] = 0
        assert m.saved() == bytes(expected_saved)
        assert after_worker["output_word"] == (before["output_word"] & ~0xc30 if expired else before["output_word"])
        rows.append({"group": "full_600_second_expiry_cancel_boundary",
                     "eligible_samples": eligible_samples,
                     "nominal_sampled_seconds": eligible_samples * 2,
                     "physical_time_measured": False, "before": before,
                     "before_zero": before_zero, "after_zero_ack": after_zero,
                     "after_worker": after_worker,
                     "saved_bytes_changed": [0x22] if expired else [],
                     "zero_after_expiry_prevents_stop": False if expired else None})

    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
              "cases": len(rows), "hardware_access": False, "results": rows}
    output = ROOT / "gen2-ac-countdown-results.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.chmod(0o600)
    names = (Path(__file__).name, "emulate_gen2_native_output_readiness.py",
             "emulate_gen2_output_stop_worker.py", "emulate_gen2_output_policy.py",
             "emulate_preference_candidates.py", "emulate_charging_followup.py",
             "emulate_general_settings.py", "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows),
                "hardware_access": False,
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in names},
                "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "boundaries": ["logging/libc", "native transport/response delivery",
                               "LCD and diagnostic/report delivery", "persistence/refresh transports",
                               "allocation/free and final DSP queue",
                               "deferred control event registration/callback and output transitions",
                               "software sample-timer eligibility and tick values"],
                "excluded": ["whole AC task and scheduler", "post-policy task body",
                             "mismatched DSP/software output-mode branch", "radio/MQTT",
                             "DSP/BMS/relay execution", "device hardware",
                             "physical duration, output continuity and safety protections"]}
    path = ROOT / "gen2-ac-countdown-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "hardware_access": False,
                      "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
