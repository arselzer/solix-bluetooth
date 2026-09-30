"""Offline C1000 Gen 2 1.1.4.9 preference candidates; no device access.

Execute the real TLV parser, 0103/0101/0102 handlers, setters, getters, A4
serializer and brightness duty lookup. Language copying to the LCD state is
also real code. Inherited persistence, transport, LCD event delivery and timer
substitutes remain. The display-off path is only a recorded boundary; neither
LCD hardware nor asynchronous low-load output policy executes.
"""

import json
import os

from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_charging_followup import NativeAcSettingsMachine
from emulate_clock_semantics import PAYLOAD, ROOT
from emulate_general_settings import EVENT, REQUEST, SETTINGS, tlv
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, firmware_image

os.umask(0o077)

LCD_LANGUAGE = 0x20004383


class PreferenceMachine(NativeAcSettingsMachine):
    def __init__(self, *, common_timer=0, display_on=1):
        super().__init__()
        # Override the inherited synthetic software timer state only.
        self.uc.mem_write(0x20007344 + 4 * 20, bytes((2 if common_timer else 3,)))
        self.uc.mem_write(0x20007344 + 5 * 20, bytes((2 if display_on else 3,)))

    def step(self, uc, address, size, data):
        if any(lo <= address < hi for lo, hi in (
            (0x0802b318, 0x0802b31a),  # Ambient-light no-op.
            (0x0802b4f4, 0x0802b4fe),  # Language raw-byte store.
            (0x0801a55c, 0x0801a56c),  # Brightness duty lookup.
            (0x08023a50, 0x08023a58),  # Language copy to LCD state.
            (0x0802b30c, 0x0802b314),  # AC power saving store.
            (0x0802b35c, 0x0802b366),  # DC power saving store.
            (0x0800bf94, 0x0800c04c),  # Native DC settings handler.
        )):
            return
        super().step(uc, address, size, data)

    def dc_saving(self, value):
        self.acked = 0
        body = tlv(0xa1, b"\x22") + tlv(0xa4, bytes((1, value))) + tlv(0xfe, bytes.fromhex("0300f15365"))
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST + 0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800bf94, REQUEST)
        assert self.acked == 1
        assert int.from_bytes(self.uc.mem_read(0x20000164, 4), "little") == 0x30
        assert self.refresh[-1] == {"delay": 50, "command": "2104", "function": 15}
        return self.a4()


def main():
    firmware_image()
    results = {"ambient": [], "language": [], "brightness": [], "restoration": [], "power_saving_storage": []}
    cases = 0
    for tag, key in ((0xa7, "ambient"), (0xa9, "language")):
        for common_timer in (0, 1):
            readbacks = []
            for value in range(256):
                machine = PreferenceMachine(common_timer=common_timer)
                before = bytes(machine.uc.mem_read(SETTINGS, 0x190))
                a4_before = machine.a4()
                machine.command(tag, value)
                after = bytes(machine.uc.mem_read(SETTINGS, 0x190))
                a4_after = machine.a4()
                expected = bytearray(before)
                expected_a4 = bytearray(a4_before)
                if tag == 0xa9:
                    expected[0x15f] = value
                    expected_a4[26] = value
                    assert machine.scheduled == 1
                    assert machine.run(0x0801a654, 0) == value
                    machine.uc.mem_write(LCD_LANGUAGE, b"\xff")
                    machine.run(0x08023a50, value)
                    assert machine.uc.mem_read(LCD_LANGUAGE, 1) == bytes((value,))
                else:
                    assert machine.scheduled == 0
                assert after == bytes(expected)
                assert a4_after == bytes(expected_a4)
                assert machine.calls == []
                assert bool(machine.uc.mem_read(EVENT + 1, 1)[0] & 1) == bool(common_timer)
                assert machine.run(0x0802c890, 0) == 0
                readbacks.append(a4_after[26])
                cases += 1
            results[key].append({
                "common_timer": common_timer, "input_values": list(range(256)),
                "language_readbacks": readbacks,
                "persistent_setting_bytes_changed": [] if tag == 0xa7 else [0x15f],
                "persistence_requests_each": int(tag == 0xa9),
                "lcd_delivery_executed": False,
                "settings_validator_rejects_any_input": False,
            })
    for common_timer in (0, 1):
        for display_on in (0, 1):
            for value in (0, 1, 2, 3, 4, 255):
                machine = PreferenceMachine(common_timer=common_timer, display_on=display_on)
                before = bytearray(machine.uc.mem_read(SETTINGS, 0x190))
                a4_before = bytearray(machine.a4())
                machine.command(0xa3, value)
                stored = value or 3
                before[0x24] = stored
                a4_before[18] = stored
                assert machine.uc.mem_read(SETTINGS, 0x190) == bytes(before)
                assert machine.a4() == bytes(a4_before)
                duty = machine.run(0x0801a55c, 0)
                assert duty == (10, 20, 60, 100)[stored & 3]
                assert machine.scheduled == int(value != 0)
                assert ("display_off_callback_boundary" in machine.calls) == (value == 0)
                assert machine.run(0x0802c890, 0) == 0
                results["brightness"].append({
                    "common_timer": common_timer, "display_on": display_on,
                    "input": value, "stored_and_a4_byte18": stored,
                    "duty_lookup": duty, "persistence_requests": machine.scheduled,
                    "recorded_boundaries": machine.calls,
                    "settings_validator_result": 0,
                })
                cases += 1
    for tag, initial, changed in ((0xa3, 1, 3), (0xa3, 3, 2), (0xa9, 0, 4), (0xa9, 4, 1)):
        machine = PreferenceMachine()
        offset = 0x24 if tag == 0xa3 else 0x15f
        machine.uc.mem_write(SETTINGS + offset, bytes((initial,)))
        before = bytes(machine.uc.mem_read(SETTINGS, 0x190))
        a4_before = machine.a4()
        machine.command(tag, changed)
        machine.command(tag, initial)
        assert machine.uc.mem_read(SETTINGS, 0x190) == before
        assert machine.a4() == a4_before
        results["restoration"].append({"tag": f"{tag:02x}", "initial": initial, "temporary": changed, "full_settings_and_a4_restored": True})
        cases += 1
    for ac in (True, False):
        offset, readback = (0x1f, 8) if ac else (0x20, 13)
        for initial in (0, 1):
            for value in (0, 1):
                machine = PreferenceMachine()
                machine.uc.mem_write(SETTINGS + offset, bytes((initial,)))
                before = bytearray(machine.uc.mem_read(SETTINGS, 0x190))
                a4_before = bytearray(machine.a4())
                a4_after = machine.ac_command(0xa6, value) if ac else machine.dc_saving(value)
                before[offset] = value
                a4_before[readback] = value
                assert machine.uc.mem_read(SETTINGS, 0x190) == bytes(before)
                assert a4_after == bytes(a4_before)
                assert machine.scheduled == 1
                results["power_saving_storage"].append({
                    "output": "ac" if ac else "dc", "initial": initial, "value": value,
                    "a4_offset": readback, "readback": a4_after[readback],
                    "persistent_setting_offset": offset, "output_flags_unchanged_during_handler": True,
                    "asynchronous_output_policy_executed": False,
                })
                cases += 1
    manifest = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
                "cases": cases, "hardware_access": False, "results": results}
    path = ROOT / "preference-candidates-results.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"cases": cases, "hardware_access": False, "output": str(path)}))


if __name__ == "__main__":
    main()
