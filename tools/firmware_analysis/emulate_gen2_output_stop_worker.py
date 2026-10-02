#!/usr/bin/env python3
"""Offline A1763 timer expiry -> real event scanner -> stop worker -> DSP intent.

Reuses the synthetic output-policy machine. Runs real event initialization,
registration, scanner, stop callback, countdown cleanup and selected off-task
fragments plus DSP descriptor builder. LCD event handling, diagnostic-report
delivery, allocation/free and final DSP queue are explicit boundaries. No
device, firmware write, real DSP/relay, output sensing or physical protection.
"""

import hashlib
import itertools
import json
import os
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_gen2_output_policy import CONFIG, COUNTDOWNS, EVENTS, OutputPolicyMachine
from emulate_clock_semantics import PAYLOAD
from emulate_general_settings import REQUEST, SETTINGS, tlv
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
OUTPUT = 0x20000164
STOP_FLAGS = 0x20000140
STOP_INDEX = 0x2000015e
SCRATCH = 0x21001800


class WorkerMachine(OutputPolicyMachine):
    def __init__(self):
        super().__init__()
        self.worker_entries = 0
        self.lcd_events = 0
        self.report_events = []
        self.dsp_descriptors = []
        self.allocate = True
        self.queue_accept = True
        self.freed = 0
        self.put(STOP_FLAGS, 0, "B")
        self.put(STOP_FLAGS + 1, 0, "B")
        self.put(OUTPUT, 0x73b)  # Synthetic enabled domains plus unrelated bits.
        self.run(0x08010680, 0)
        self.fragment(0x08006f20, (0x08006f48,), "ac")
        assert self.get(STOP_INDEX, "B") == 4
        assert self.get(EVENTS + 4 * 12 + 4) == 0x0800709d
        # Actual first LCD-registration block, after its unrelated reset prefix.
        self.fragment(0x0800e3e2, (0x0800e3ea,), "ac")
        self.lcd_index = self.get(0x20000462, "B")
        assert self.lcd_index == 5

    def step(self, uc, address, size, data):
        r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if address == 0x0800709c:
            self.worker_entries += 1
        elif address == 0x08024bf0:
            self.lcd_events += 1
            self.back(0)  # The complete LCD/display worker is outside scope.
        elif address == 0x0802e9be:
            self.report_events.append({"address": f"{r0:08x}", "value": r1})
            self.back(0)
        elif address == 0x08020a4c:
            assert r0 == 2
            self.back(SCRATCH if self.allocate else 0)
        elif address == 0x08017a8c:
            assert r0 == SCRATCH
            self.freed += 1
            self.back(0)
        elif address == 0x08027e40:
            assert r0 == 1 and r2 == 0
            descriptor = bytes(uc.mem_read(r1, 24))
            payload = int.from_bytes(descriptor[16:20], "little")
            assert int.from_bytes(descriptor[12:14], "little") == 2
            self.dsp_descriptors.append({
                "channel": int.from_bytes(descriptor[8:10], "little"),
                "command": int.from_bytes(descriptor[10:12], "little"),
                "payload": bytes(uc.mem_read(payload, 2)).hex(),
                "accepted_by_substitute": self.queue_accept,
            })
            self.back(int(self.queue_accept))
        elif any(lo <= address < hi for lo, hi in (
            (0x08010644, 0x080106b4), (0x080105e0, 0x0801062e),
            (0x08010778, 0x0801077a), (0x08006f20, 0x08006f48),
            (0x0800e3e2, 0x0800e3ea), (0x0800709c, 0x0800720a),
            (0x080296c0, 0x080296d2), (0x0802984c, 0x0802985e),
            (0x0800d014, 0x0800d02c), (0x080194b8, 0x080194be),
            (0x080245f6, 0x0802462e), (0x08024b66, 0x08024b92),
            (0x0802a758, 0x0802a858),
            (0x0801a518, 0x0801a55c), (0x08019aec, 0x08019af6),
            (0x08019b00, 0x08019b12),
        )):
            return
        else:
            super().step(uc, address, size, data)

    def snapshot(self):
        stop = self.get(STOP_INDEX, "B")
        return {"output_word": self.get(OUTPUT), "stop_flags": self.get(STOP_FLAGS, "B"),
                "event_pending": self.get(EVENTS + stop * 12 + 1, "B"),
                "event_argument": self.get(EVENTS + stop * 12 + 8),
                "ac_remaining": self.get(COUNTDOWNS),
                "dc_remaining": self.get(COUNTDOWNS + 8),
                "dc_initial_duration": self.get(COUNTDOWNS + 12),
                "ac_checkpoint": self.get(COUNTDOWNS + 16, "H"),
                "dc_checkpoint": self.get(COUNTDOWNS + 18, "H"),
                "countdown_enable": self.get(COUNTDOWNS + 20, "B"),
                "ac_saved_timer": self.get(SETTINGS + 0x22, "B"),
                "dc_saved_timer": self.get(SETTINGS + 0x23, "B"),
                "persistence_requests": self.scheduled,
                "worker_entries": self.worker_entries, "lcd_events": self.lcd_events}

    def expire(self, kind):
        c = CONFIG[kind]
        self.prepare(kind, counter=2, countdown=True, remaining=2, previous=True)
        self.put(COUNTDOWNS + 16, 55, "H")
        self.put(COUNTDOWNS + 18, 66, "H")
        self.put(COUNTDOWNS + 12, 777)
        self.run(c["policy"], c["counter"])

    def drain(self):
        self.run(0x08010644, 0)

    def setting(self, kind, tag, value):
        before = self.get(OUTPUT)
        self.acked = 0
        body = tlv(0xa1, b"\x22") + tlv(tag, value) + tlv(0xfe, bytes.fromhex("0300f15365"))
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(CONFIG[kind]["handler"], REQUEST)
        assert self.acked == 1 and self.get(OUTPUT) == before


def main():
    firmware_image()
    rows = []

    def record(group, **values):
        rows.append({"group": group, **values})

    m = WorkerMachine()
    record("registration", stop_index=m.get(STOP_INDEX, "B"),
           callback=f"{m.get(EVENTS + 4 * 12 + 4) & ~1:08x}",
           lcd_index=m.lcd_index, actual_event_init_register_and_scan=True)

    # Every stop-bit combination, including the all-output request bit.
    masks = {1: 0x40, 2: 0xc30, 4: 1, 8: 8, 16: 4, 32: 0x302}
    for flags, special, display in itertools.product(range(128), (0, 1, 13, 14), (0, 1)):
        m = WorkerMachine()
        m.put(STOP_FLAGS, flags, "B")
        m.put(STOP_FLAGS + 1, special, "B")
        m.put(0x20007344 + 5 * 20, 2 if display else 3, "B")
        m.put(COUNTDOWNS, 111)
        m.put(COUNTDOWNS + 8, 222)
        m.put(COUNTDOWNS + 12, 777)
        m.put(COUNTDOWNS + 16, 55, "H")
        m.put(COUNTDOWNS + 18, 66, "H")
        m.put(COUNTDOWNS + 20, 0xa3, "B")
        m.put(SETTINGS + 0x22, 1, "B")
        m.put(SETTINGS + 0x23, 1, "B")
        before = m.snapshot()
        # Zero argument, matching the actual timer policy's request.
        m.uc.reg_write(UC_ARM_REG_R1, 0)
        m.run(0x080106b4, m.get(STOP_INDEX, "B"))
        m.drain()
        after = m.snapshot()
        expanded = ((flags | 2 | 8 | 16 | 32) & ~64) if flags & 64 else flags
        expected = before["output_word"]
        for bit, mask in masks.items():
            if expanded & bit:
                expected &= ~mask
        assert after["output_word"] == expected
        assert after["stop_flags"] == 0
        assert after["worker_entries"] == 1 and after["event_pending"] == 0
        assert after["event_argument"] == 0
        assert after["dc_initial_duration"] == 777
        assert after["ac_remaining"] == (0 if expanded & 2 else 111)
        assert after["dc_remaining"] == (0 if expanded & 32 else 222)
        assert after["ac_checkpoint"] == (0 if expanded & 2 else 55)
        assert after["dc_checkpoint"] == (0 if expanded & 32 else 66)
        expected_enable = 0xa3 & ~(int(bool(expanded & 2)) | (2 if expanded & 32 else 0))
        assert after["countdown_enable"] == expected_enable
        clears_saved = special in (0, 13, 14)
        assert after["ac_saved_timer"] == int(not (clears_saved and expanded & 2))
        assert after["dc_saved_timer"] == int(not (clears_saved and expanded & 32))
        assert after["persistence_requests"] == (
            int(clears_saved and bool(expanded & 2)) + int(clears_saved and bool(expanded & 32)))
        record("worker_stop_flags", flags=flags, special_mode=special, display_active=display,
               expanded_flags=expanded, before=before, after=after)

    # A cancellation before expiry differs from cancellation of an already pending event.
    for kind, cancel_at in itertools.product(CONFIG, ("before_expiry", "after_expiry", "none")):
        m = WorkerMachine()
        c = CONFIG[kind]
        before = m.snapshot()
        if cancel_at == "before_expiry":
            m.prepare(kind, counter=2, countdown=True, remaining=3600)
            m.setting(kind, 0xa3, b"\x03" + bytes(4))
            m.run(c["policy"], c["counter"])
        else:
            m.expire(kind)
            assert m.get(STOP_FLAGS, "B") == c["request_flag"]
            assert m.get(EVENTS + 4 * 12 + 1, "B") == 1
            assert m.get(OUTPUT) == before["output_word"]
            if cancel_at == "after_expiry":
                m.setting(kind, 0xa3, b"\x03" + bytes(4))
                assert m.get(c["remaining"]) == 0
                assert m.get(STOP_FLAGS, "B") == c["request_flag"]
                assert m.get(EVENTS + 4 * 12 + 1, "B") == 1
        pre_worker = m.snapshot()
        m.drain()
        after = m.snapshot()
        expected_mask = masks[c["request_flag"]] if cancel_at != "before_expiry" else 0
        assert after["output_word"] == before["output_word"] & ~expected_mask
        assert after["event_pending"] == after["stop_flags"] == 0
        record("expiry_cancel_worker", kind=kind, cancellation=cancel_at,
               before=before, before_worker=pre_worker, after=after,
               cancel_after_expiry_still_stops=cancel_at == "after_expiry")

    # Event request coalescing and synthetic nonzero argument retention.
    for first, second in itertools.product((0, 7), repeat=2):
        m = WorkerMachine()
        slot = m.get(STOP_INDEX, "B")
        m.uc.reg_write(UC_ARM_REG_R1, first)
        m.run(0x080106b4, slot)
        m.uc.reg_write(UC_ARM_REG_R1, second)
        m.run(0x080106b4, slot)
        arg = m.get(EVENTS + slot * 12 + 8)
        assert arg == (first or second)
        m.drain()
        assert m.worker_entries == 1 and m.get(EVENTS + slot * 12 + 8) == 0
        record("coalesced_event_arguments", first=first, second=second,
               delivered_argument=arg, callback_calls=1)

    # Later off-task builder emits DSP intent; queue failure cannot restore output flags.
    for kind, allocated, accepted in itertools.product(CONFIG, (False, True), (False, True)):
        m = WorkerMachine()
        m.expire(kind)
        m.drain()
        c = CONFIG[kind]
        before = m.get(OUTPUT)
        m.allocate, m.queue_accept = allocated, accepted
        state_flag = c["state"] + (0xb if kind == "ac" else 5)
        m.put(state_flag, 1, "B")
        start, end = (0x08024b66, 0x08024b92) if kind == "ac" else (0x080245f6, 0x0802462e)
        m.fragment(start, (end,), kind)
        code = "5200" if kind == "ac" else "5b00"
        if allocated:
            assert m.dsp_descriptors == [{"channel": 1, "command": 0 if kind == "ac" else 0x22,
                                           "payload": code, "accepted_by_substitute": accepted}]
        else:
            assert m.dsp_descriptors == []
        assert m.get(state_flag, "B") == int(not (allocated and accepted))
        assert m.get(OUTPUT) == before
        assert m.freed == int(allocated and not accepted)
        # Published output-enabled fields use these actual logical flag getters.
        getter_index = 0 if kind == "ac" else 6
        output_enabled = m.run(0x0801a518, getter_index)
        assert output_enabled == 0
        record("off_task_dsp_intent", kind=kind, allocation_succeeds=allocated,
               queue_accepts=accepted, descriptors=m.dsp_descriptors,
               state_flag_remaining=m.get(state_flag, "B"),
               logical_output_flags_stay_off=True, logical_output_getter=output_enabled,
               freed_failed_payload=m.freed)

    # A later execution of the same off fragment retries an unaccepted stop intent.
    for kind, c in CONFIG.items():
        m = WorkerMachine()
        m.expire(kind)
        m.drain()
        state_flag = c["state"] + (0xb if kind == "ac" else 5)
        m.put(state_flag, 1, "B")
        start, end = (0x08024b66, 0x08024b92) if kind == "ac" else (0x080245f6, 0x0802462e)
        m.queue_accept = False
        m.fragment(start, (end,), kind)
        assert m.get(state_flag, "B") == 1
        m.queue_accept = True
        m.fragment(start, (end,), kind)
        assert m.get(state_flag, "B") == 0
        assert len(m.dsp_descriptors) == 2 and m.freed == 1
        assert m.dsp_descriptors[0]["accepted_by_substitute"] is False
        assert m.dsp_descriptors[1]["accepted_by_substitute"] is True
        assert m.dsp_descriptors[0]["payload"] == m.dsp_descriptors[1]["payload"]
        assert m.run(0x0801a518, 0 if kind == "ac" else 6) == 0
        record("off_task_repeated_fragment", kind=kind, descriptors=m.dsp_descriptors,
               state_flag_after_rejected=1, state_flag_after_accepted=0,
               logical_output_getter=0, task_scheduler_executed=False)

    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
              "cases": len(rows), "hardware_access": False, "results": rows}
    output = ROOT / "gen2-output-stop-worker-results.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.chmod(0o600)
    names = (Path(__file__).name, "emulate_gen2_output_policy.py",
             "emulate_preference_candidates.py", "emulate_charging_followup.py",
             "emulate_general_settings.py", "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows),
                "hardware_access": False,
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in names},
                "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "boundaries": ["LCD event worker", "diagnostic/report events", "logging/libc",
                               "allocation/free", "final DSP queue", "persistence/refresh transports"],
                "excluded": ["full output tasks and task scheduler", "radio/MQTT", "DSP execution",
                             "relay/power measurements", "device hardware and physical protections"]}
    manifest_path = ROOT / "gen2-output-stop-worker-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "hardware_access": False,
                      "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
