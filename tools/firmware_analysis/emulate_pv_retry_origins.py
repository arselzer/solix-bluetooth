"""Trace known A1763 commands to PV retry state using synthetic ARM execution.

The command parser/0103 handler, brightness/display setters, weak-light helper,
timer and one debounce record execute. Synthetic MMIO records low-SOC register
effects. LCD delivery, flash, response/refresh transport, backup registers and
timer scheduling are substitutes. No hardware.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from emulate_general_settings import SettingsMachine, SETTINGS, EVENT, tlv
from emulate_additional_features import TelemetryMachine
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
FLAGS = 0x2000039a
CONTEXT = FLAGS-6
MMIO_MASK = 0x40010400
RECORDS = 0x200041a4


class RetryOriginMachine(SettingsMachine):
    def __init__(self, *, locked=True, soc=50, level=2, display_on=0):
        super().__init__(display_on=display_on)
        self.uc.mem_map(0x40010000, 0x1000)
        self.uc.mem_write(MMIO_MASK, struct.pack("<I", 0xffff))
        self.uc.mem_write(FLAGS, bytes((0x13 if locked else 0x10, 0)))
        self.uc.mem_write(0x2000041d, struct.pack("<H", soc))
        self.uc.mem_write(SETTINGS+0x24, bytes((level,)))
        self.backup = int.from_bytes(self.uc.mem_read(FLAGS, 2), "little")
        self.backup_writes = []
        self.low_soc_calls = 0
        self.scheduler_calls = []
        self.timer_start_calls = []
        for address, timer_id in ((0x200003e8, 6), (0x200003ec, 7), (0x200003a5, 8)):
            self.uc.mem_write(address, bytes((timer_id,)))

    def step(self, uc, address, size, data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        if address == 0x0800e2ec:
            self.low_soc_calls += 1
            return
        elif address == 0x08014278:
            assert r0 == 2
            self.back(self.backup)
        elif address in (0x080142a4, 0x080142cc):
            assert r0 == 2 and r1 <= 0xffff
            self.backup = r1
            self.backup_writes.append(r1)
            self.back()
        elif address == 0x080107f4:
            self.scheduler_calls.append({"period_argument": r0, "callback": f"{r1:08x}"})
            uc.mem_write(r3, b"\x09")
            self.back(1)
        elif address == 0x0801095c:
            self.timer_start_calls.append(r0)
            self.back()
        elif any(lo <= address < hi for lo, hi in (
            (0x08011730, 0x0801176e), (0x08018384, 0x08018546),
            (0x0800e2ec, 0x0800e302), (0x0800ddb8, 0x0800ddc6),
            (0x0801449c, 0x080144aa),
            (0x0802bc84, 0x0802bcf2), (0x080176b8, 0x080176c2),
            (0x0800d5d0, 0x0800d73e), (0x08030418, 0x080304c0),
            (0x0802af38, 0x0802af44), (0x0801bc7c, 0x0801bc82),
            (0x0800ecf8, 0x0800ed80), (0x080254e4, 0x080255a2),
            (0x08025ffc, 0x0802606e), (0x08026078, 0x08026092),
            (0x080198e8, 0x080198f2), (0x0800537a, 0x08005380),
            (0x08008114, 0x0800811c), (0x08008142, 0x0800814a),
            (0x0801a2a8, 0x0801a3a6), (0x080181cc, 0x080181da),
            (0x080182e4, 0x080182ec), (0x0801a598, 0x0801a5a0),
            (0x0801aab8, 0x0801aae0), (0x0801b03c, 0x0801b046),
            (0x0801a214, 0x0801a21c))):
            return
        else:
            super().step(uc, address, size, data)

    def state(self):
        flags = self.uc.mem_read(FLAGS, 1)[0]
        return {"lock": (flags >> 1) & 1, "user_action": (flags >> 3) & 1,
                "retry": (flags >> 2) & 1}

    def a3(self):
        return TelemetryMachine.serialize(self, 0xa3)

    def timer(self):
        self.run(0x08030418, 0)

    def setup_consumer(self, *, retry, module_bit, active):
        self.run(0x0800ecf8, 0)
        # Isolate the second real record. Other input debouncers are excluded;
        # they must not access unrelated physical module inputs.
        for index in (0, 2, 3, 4, 5):
            self.uc.mem_write(RECORDS+20*index, bytes(20))
            self.uc.mem_write(RECORDS+20*index+12, b"\x02")
        record = RECORDS+20
        raw = bytes(self.uc.mem_read(record, 20))
        assert struct.unpack("<III", raw[:12]) == (0x08025ffd, 0x08026079, 0x0802601d)
        assert raw[14] == 200
        self.uc.mem_write(record+12, bytes((active, 0)))
        self.uc.mem_write(record+16, struct.pack("<HH", 2 if active else 1, 2 if active else 1))
        self.uc.mem_write(FLAGS, bytes((0x10 | (retry << 2),)))
        self.uc.mem_write(0x20003f4c, bytes((module_bit << 5,)))
        self.uc.mem_write(0x200004be, bytes((active << 1,)))
        self.uc.mem_write(0x2000015b, b"\x03")


def main():
    image = firmware_image()
    entry = image[0x08032e78-0x08005000:0x08032e80-0x08005000]
    assert entry == bytes.fromhex("0301000031c50008")
    results = {"same_brightness_command": [], "display_switch": [],
               "unrelated_commands": [], "low_soc_boundary": [], "retry_consumer": []}
    for source in (0x21, 0x22):
        for level in (1, 2, 3):
            for soc in (2, 50, 100):
                for locked in (False, True):
                    m = RetryOriginMachine(level=level, soc=soc, locked=locked)
                    before = bytes(m.uc.mem_read(SETTINGS, 0x190))
                    payload = (tlv(0xa1, bytes((source,)))+tlv(0xa3, bytes((1, level)))
                               +tlv(0xfe, b"\x03\x00\xf1\x53\x65"))
                    m.body(payload)
                    command_state = m.state()
                    assert command_state == {"lock": int(locked), "user_action": int(locked), "retry": 0}
                    assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == before
                    assert m.low_soc_calls == 0 and int.from_bytes(m.uc.mem_read(MMIO_MASK, 4), "little") == 0xffff
                    assert m.scheduled == 1 and m.uc.mem_read(EVENT+1, 1)[0] == 1
                    m.timer()
                    assert m.state() == {"lock": 0, "user_action": 0, "retry": int(locked)}
                    assert m.a3()[13] == 0 and bytes(m.uc.mem_read(SETTINGS, 0x190)) == before
                    assert bytes(m.uc.mem_read(0x20000164, 4)) == struct.pack("<I", 0x30)
                    results["same_brightness_command"].append({"source": source, "saved_level": level,
                        "synthetic_soc": soc, "initial_lock": locked, "after_command": command_state,
                        "after_timer": m.state(), "all_saved_settings_unchanged": True,
                        "persistence_requests": m.scheduled, "display_event_marked": True})
    for on in (0, 1):
        for requested in (0, 1):
            for locked in (False, True):
                m = RetryOriginMachine(display_on=on, locked=locked)
                m.command(0xa2, requested)
                assert m.state()["user_action"] == int(locked and on != requested)
                assert m.low_soc_calls == 0 and m.scheduled == 0
                results["display_switch"].append({"display_initially_on": on, "requested": requested,
                    "initial_lock": locked, "state": m.state(),
                    "display_off_boundary_called": "display_off_callback_boundary" in m.calls,
                    "display_event_marked": bool(m.uc.mem_read(EVENT+1, 1)[0])})
    for tag, value in ((None, 0), (0xa5, 0), (0xb0, 1), (0xa6, 0)):
        m = RetryOriginMachine()
        m.command(tag, value)
        assert m.state() == {"lock": 1, "user_action": 0, "retry": 0}
        results["unrelated_commands"].append({"tag": tag, "value": value, "state": m.state()})
    for soc in (0, 1, 2):
        m = RetryOriginMachine(soc=soc)
        before = bytes(m.uc.mem_read(SETTINGS, 0x190))
        m.command(0xa3, 2)
        assert m.low_soc_calls == int(soc <= 1)
        mask = int.from_bytes(m.uc.mem_read(MMIO_MASK, 4), "little")
        assert mask == (0xffff & ~0x804 if soc <= 1 else 0xffff)
        assert m.timer_start_calls == ([6, 7, 8] if soc <= 1 else [])
        assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == before
        results["low_soc_boundary"].append({"synthetic_soc": soc,
            "actual_low_soc_service_calls": m.low_soc_calls,
            "substituted_timer_start_ids": m.timer_start_calls,
            "synthetic_mmio_initial": 0xffff, "synthetic_mmio_after": mask,
            "state": m.state(), "all_saved_settings_unchanged": True})
    for active in (0, 1):
        for module in (0, 1):
            for retry in (0, 1):
                m = RetryOriginMachine()
                m.setup_consumer(retry=retry, module_bit=module, active=active)
                observations = []
                for iteration in range(1, 405):
                    m.run(0x080254e4, 0)
                    if iteration in (1, 200, 201, 202, 203, 403, 404):
                        raw = bytes(m.uc.mem_read(RECORDS+20, 20))
                        observations.append({"callback": iteration, "debounced_state": raw[12],
                            "counter": raw[13], "retry": m.state()["retry"],
                            "derived_input_flag": (m.uc.mem_read(0x200004be, 1)[0] >> 1) & 1})
                by_count = {row["callback"]: row for row in observations}
                if active and retry and module:
                    assert by_count[201]["debounced_state"] == 1
                    assert by_count[202]["debounced_state"] == 0
                    assert by_count[202]["retry"] == 1 and by_count[203]["retry"] == 0
                    assert by_count[403]["debounced_state"] == 0
                    assert by_count[404]["debounced_state"] == 1
                else:
                    assert by_count[404]["debounced_state"] == module
                assert bytes(m.uc.mem_read(0x20000164, 4)) == struct.pack("<I", 0x30)
                results["retry_consumer"].append({"initial_debounced_state": active,
                    "module_bit5": module, "initial_retry": retry,
                    "observations": observations, "output_state_word_unchanged": True})
    counts = {key: len(value) for key, value in results.items()}
    assert counts == {"same_brightness_command": 36, "display_switch": 8,
                      "unrelated_commands": 4, "low_soc_boundary": 3, "retry_consumer": 8}
    out = ROOT/"pv-retry-origins-results.json"
    out.write_text(json.dumps(results, indent=2)+"\n")
    out.chmod(0o600)
    names = (Path(__file__).name, "emulate_general_settings.py", "emulate_additional_features.py",
             "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": {"filename": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in names},
        "result_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "substitutions": ["LCD delivery/off callback", "settings persistence", "command ACK",
            "telemetry refresh queue", "backup registers", "timer registration/start", "logging",
            "memcpy/memset", "five unrelated debounce records"],
        "not_executed": ["physical MPPT/DSP producer", "charging actuator", "PV hardware", "app/radio transport"]}
    out = ROOT/"pv-retry-origins-manifest.json"
    out.write_text(json.dumps(manifest, indent=2)+"\n")
    out.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
