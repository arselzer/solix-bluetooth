#!/usr/bin/env python3
"""Offline native AC Smart preservation and countdown/off-task interleaving.

Runs actual parser, native setting handlers, A4 serializer and output-flag
dispatch fragments. For off domains, executes the selected off-task cleanup.
Stops on the on-domain branch before the rest of that task. No device access.
"""

import hashlib
import itertools
import json
import os
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R10

from emulate_clock_semantics import PAYLOAD
from emulate_general_settings import REQUEST, SETTINGS, tlv
from emulate_gen2_output_policy import CONFIG, COUNTDOWNS
from emulate_gen2_output_stop_worker import OUTPUT, WorkerMachine
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
SAVED_SIZE = 415
DOMAIN_MASK = {"ac": 0x30, "car_dc": 2}
DISPATCH = {
    "ac": (0x0802494a, 0x08024954, 0x08024b92),
    "car_dc": (0x0802449c, 0x080244aa, 0x0802462e),
}
TIMESTAMP = {
    "fe_seconds": tlv(0xfe, bytes.fromhex("0300f15365")),
    "fd_milliseconds": tlv(0xfd, b"\x001700000000000"),
}


class ReadinessMachine(WorkerMachine):
    def step(self, uc, address, size, data):
        if any(lo <= address < hi for lo, hi in (
            (0x0802449c, 0x080244aa), (0x080245a6, 0x080245a8),
            (0x0802494a, 0x08024954), (0x08024a4c, 0x08024a4e),
        )):
            return
        super().step(uc, address, size, data)

    def setting(self, kind, tag, value, timestamp):
        before = self.get(OUTPUT)
        self.acked = 0
        body = tlv(0xa1, b"\x22") + tlv(tag, value) + TIMESTAMP[timestamp]
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(CONFIG[kind]["handler"], REQUEST)
        assert self.acked == 1 and self.get(OUTPUT) == before
        return body.hex()

    def dispatch(self, kind):
        start, on, off_end = DISPATCH[kind]
        self.uc.reg_write(UC_ARM_REG_R10, OUTPUT)
        return self.fragment(start, (on, off_end), kind)

    def saved(self):
        return bytes(self.uc.mem_read(SETTINGS, SAVED_SIZE))


def main():
    firmware_image()
    rows = []

    # A timer ACK while off precedes ordinary off-task cleanup, regardless of
    # whether a pending DSP-stop descriptor needs to be built.
    for kind, seconds, pending, timestamp in itertools.product(
            CONFIG, (0, 1, 2, 3600, 0xffffffff), (0, 1), TIMESTAMP):
        m = ReadinessMachine()
        c = CONFIG[kind]
        m.put(OUTPUT, m.get(OUTPUT) & ~DOMAIN_MASK[kind])
        state_flag = c["state"] + (11 if kind == "ac" else 5)
        m.put(state_flag, pending, "B")
        m.put(c["counter"], 899, "H")
        before = m.snapshot()
        body = m.setting(kind, 0xa3, b"\x03" + seconds.to_bytes(4, "little"), timestamp)
        acknowledged = m.snapshot()
        a4_ack = m.a4()
        start = 1 if kind == "ac" else 9
        assert int.from_bytes(a4_ack[start:start + 4], "little") == seconds
        assert acknowledged["output_word"] == before["output_word"]
        assert m.dispatch(kind) == DISPATCH[kind][2]
        after = m.snapshot()
        a4_task = m.a4()
        assert after["output_word"] == before["output_word"]
        assert m.get(c["remaining"]) == 0
        assert m.get(c["counter"], "H") == 0
        assert int.from_bytes(a4_task[start:start + 4], "little") == 0
        assert after["stop_flags"] == after["event_pending"] == 0
        assert len(m.dsp_descriptors) == pending
        assert m.get(state_flag, "B") == 0
        initial = seconds if kind == "car_dc" and seconds else 0
        assert after["dc_initial_duration"] == initial
        # Restore zero after task cleanup: this is still not a queued-stop undo.
        m.setting(kind, 0xa3, b"\x03" + bytes(4), timestamp)
        restored = m.snapshot()
        assert restored["output_word"] == before["output_word"]
        assert restored["stop_flags"] == restored["event_pending"] == 0
        rows.append({"group": "timer_off_dispatch", "kind": kind,
                     "seconds": seconds, "pending_dsp_stop": pending,
                     "timestamp": timestamp, "request_tlv_hex": body,
                     "before": before, "after_ack": acknowledged,
                     "a4_after_ack": a4_ack.hex(), "after_off_task": after,
                     "a4_after_off_task": a4_task.hex(),
                     "after_zero_restore": restored,
                     "dsp_descriptors": m.dsp_descriptors})

    # The same actual output-mask check takes the on branch and is stopped
    # before its initialization/policy path. This establishes branch selection,
    # not on-task timing, countdown progress, or hardware behavior.
    for kind, seconds, timestamp in itertools.product(
            CONFIG, (0, 1, 2, 3600, 0xffffffff), TIMESTAMP):
        m = ReadinessMachine()
        m.setting(kind, 0xa3, b"\x03" + seconds.to_bytes(4, "little"), timestamp)
        before = m.snapshot()
        assert m.dispatch(kind) == DISPATCH[kind][1]
        assert m.snapshot() == before and not m.dsp_descriptors
        rows.append({"group": "timer_on_dispatch_boundary", "kind": kind,
                     "seconds": seconds, "timestamp": timestamp,
                     "before": before, "after": m.snapshot(),
                     "full_on_task_executed": False})

    # AC Smart preference itself has lossless saved-byte restoration while AC
    # stays logically off. The off task resets its independent volatile counter.
    for saved, target, counter, timestamp in itertools.product(
            (0, 1), (0, 1), (0, 900, 28800, 65535), TIMESTAMP):
        m = ReadinessMachine()
        m.put(OUTPUT, m.get(OUTPUT) & ~DOMAIN_MASK["ac"])
        m.put(CONFIG["ac"]["smart"], saved, "B")
        m.put(CONFIG["ac"]["counter"], counter, "H")
        before_saved, before_a4, before = m.saved(), m.a4(), m.snapshot()
        body = m.setting("ac", 0xa6, bytes((1, target)), timestamp)
        expected_saved = bytearray(before_saved)
        expected_saved[0x1f] = target
        expected_a4 = bytearray(before_a4)
        expected_a4[8] = target
        assert m.saved() == bytes(expected_saved)
        assert m.a4() == bytes(expected_a4)
        assert m.get(CONFIG["ac"]["counter"], "H") == counter
        assert m.dispatch("ac") == DISPATCH["ac"][2]
        assert m.get(CONFIG["ac"]["counter"], "H") == 0
        assert m.saved() == bytes(expected_saved)
        assert m.a4() == bytes(expected_a4)
        m.setting("ac", 0xa6, bytes((1, saved)), timestamp)
        assert m.saved() == before_saved and m.a4() == before_a4
        after = m.snapshot()
        assert after["output_word"] == before["output_word"]
        assert after["stop_flags"] == after["event_pending"] == 0
        assert not m.dsp_descriptors
        rows.append({"group": "ac_smart_off_restore", "saved": saved,
                     "target": target, "counter_before": counter,
                     "counter_after_off_task": 0, "timestamp": timestamp,
                     "request_tlv_hex": body, "a4_before": before_a4.hex(),
                     "a4_after_restore": m.a4().hex(),
                     "saved_settings_restored_exactly": True,
                     "before": before, "after": after})

    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
              "cases": len(rows), "hardware_access": False, "results": rows}
    output = ROOT / "gen2-native-output-readiness-results.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.chmod(0o600)
    names = (Path(__file__).name, "emulate_gen2_output_stop_worker.py",
             "emulate_gen2_output_policy.py", "emulate_preference_candidates.py",
             "emulate_charging_followup.py", "emulate_general_settings.py",
             "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows),
                "hardware_access": False,
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in names},
                "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "boundaries": ["native transport envelope and response delivery",
                               "logging/libc", "LCD and diagnostic/report events",
                               "allocation/free and final DSP queue",
                               "persistence/refresh transports"],
                "excluded": ["full output tasks and scheduler", "on-task body",
                             "radio/MQTT and physical timestamp processing",
                             "DSP/BMS/relay execution", "device hardware",
                             "physical low-load or timer shutdown"]}
    path = ROOT / "gen2-native-output-readiness-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "hardware_access": False,
                      "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
