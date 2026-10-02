#!/usr/bin/env python3
"""Audit the named SDK action boundary in exact A1763 main/radio images.

Actual RISC-V numeric-head selection and native head-17 JSON admission, data
decoding, raw routing, and the existing RSSI query execute. Every other head
stops before its selected branch; the controller-forwarding wrapper is a
recording substitute. cJSON/libc/base64, synthetic identities/session state,
AP-info, crypto, OS allocation/queue and final transports are substitutes.
Main firmware is inspected only for its fixed ordinary command table. No
controller setter, device, cloud, SDK implementation or persistent backend.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import cryptography
import unicorn
from unicorn import riscv_const as R

from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256, packet
from emulate_radio_rssi_routes import NativeMachine


RADIO_SIZE = 1_482_800
MAIN_NAME = "MainMcu-decoded.bin"
MAIN_SHA256 = "21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9"
MAIN_SIZE = 198_656
MAIN_BASE = 0x08005000
DISPATCH = 0x42026F4E
UNHANDLED = 0x42026F92
NO_ACTION_RETURN = 0x4202724E
BRANCHES = {
    2: 0x42027182, 3: 0x42027242, 4: 0x42027270,
    5: 0x4202748A, 6: 0x42026FA6, 9: 0x42027282,
    12: 0x42027406, 15: 0x42027042, 17: 0x42027364,
    25: 0x42027524, 26: 0x42027534, 27: 0x420270FC,
    28: 0x420278A0, 31: 0x4202791A,
}
NEEDLES = ("action_set_ac_params", "acInputDisableSwitch", "supportAcInputDisable")
PROTECTED = ((0x3FC86FA8, 0x368), (0x3FC86C40, 0x368),
             (0x3FC89EB4, 0x154), (0x3FC8B5BC, 0x310))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def image_read(image: bytes, address: int, size: int) -> bytes:
    offset = 24
    for _ in range(image[1]):
        start, length = struct.unpack_from("<II", image, offset)
        offset += 8
        if start <= address and address + size <= start + length:
            return image[offset + address - start:offset + address - start + size]
        offset += length
    raise AssertionError(f"No image segment for {address:08x}/{size}")


class Machine(NativeMachine):
    def __init__(self, image: bytes):
        self.selection_only = False
        self.forward_attempts: list[dict] = []
        self.raw_calls = 0
        self.raw_source_length = 0
        self.raw_source_pointer = 0
        self.dispatch_calls = 0
        super().__init__(image, rssi=-70)

    def step(self, uc, address, size, data):
        if address == self.stop_at:
            uc.emu_stop()
            return
        if address == DISPATCH:
            self.dispatch_calls += 1
        if self.selection_only:
            assert DISPATCH <= address < 0x42027110, (
                f"Selected branch executed unexpectedly at {address:08x}")
            return
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        if address == 0x42043A18:
            self.raw_calls += 1
            self.raw_source_pointer, self.raw_source_length = args[:2]
        if address == 0x420439CC:
            assert args[2] == self.raw_source_pointer+9
            available = max(0, self.raw_source_length-9)
            captured = min(args[3], available, 32)
            self.forward_attempts.append({"function": args[0],
                "command": f"{args[1]:04x}",
                "claimed_body_length": args[3],
                "available_source_body_bytes": available,
                "body_prefix_hex": self.read(args[2], captured).hex(),
                "source_length": self.raw_source_length})
            uc.reg_write(R.UC_RISCV_REG_A0, 0)
            uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))
            return
        return super().step(uc, address, size, data)

    def protected(self) -> list[bytes]:
        return [self.read(address, size) for address, size in PROTECTED]

    def select_head(self, command: int) -> dict:
        before = self.protected()
        self.selection_only = True
        target = NO_ACTION_RETURN if command in (10, 11) else BRANCHES.get(command, UNHANDLED)
        self.run(DISPATCH, command, 2, 1700000000, self.node({}), 0, 0, 0, 0,
                 stop_at=target)
        self.selection_only = False
        assert self.protected() == before
        assert not self.raw_calls and not self.forward_attempts
        return {"case": "numeric_head_selection", "head_command": command,
                "selected_branch": f"{target:08x}",
                "handled_in_selector": command in BRANCHES,
                "no_action_success_return": command in (10, 11),
                "stopped_before_branch": True}

    def send_payload(self, payload: dict) -> dict:
        before = self.protected()
        envelope = self.envelope(packet(b"", 0x22, function=0x10))
        envelope["payload"] = json.dumps({"account_id": self.account.decode(),
            "device_sn": self.serial.decode(), **payload})
        self.receive_json(envelope)
        assert self.protected() == before
        assert not self.refresh_requests
        assert not self.builders and not self.egress
        assert not set(self.json_keys).intersection(NEEDLES)
        assert "id" not in self.json_keys and "param" not in self.json_keys
        return {"json_keys_requested": sorted(set(self.json_keys)),
                "raw_callback_calls": self.raw_calls,
                "rssi_observations": self.ap_info_calls,
                "controller_forwarding_boundary": self.forward_attempts,
                "reply_frames": [row["wire"].hex() for row in self.reply_frames],
                "protected_regions_unchanged": True,
                "controller_builder_not_executed": True}


def fixed_inventory(radio: bytes, main: bytes) -> dict:
    radio_table = [struct.unpack("<II", image_read(radio, 0x3C147BE4+i*8, 8))
                   for i in range(42)]
    main_table = [struct.unpack_from("<II", main, 0x08032E60-MAIN_BASE+i*8)
                  for i in range(17)]
    assert len({command for command, _ in radio_table}) == 42
    assert len({command for command, _ in main_table}) == 17
    assert dict(radio_table)[0x22] == 0x4203CB26
    assert dict(radio_table)[0x24] == 0x4203D056
    assert dict(main_table)[0x101] == 0x0800BD1D
    assert dict(main_table)[0x102] == 0x0800BF95
    assert dict(main_table)[0x103] == 0x0800C531
    assert all(handler & 1 and MAIN_BASE <= handler-1 < MAIN_BASE+len(main)
               for _, handler in main_table)
    assert all(0x42000020 <= handler < 0x4212874C for _, handler in radio_table)
    counts = {name: {needle: image.count(needle.encode("ascii")) for needle in NEEDLES}
              for name, image in ((IMAGE_NAME, radio), (MAIN_NAME, main))}
    assert all(count == 0 for image in counts.values() for count in image.values())
    return {
        "exact_ascii_name_counts": counts,
        "ordinary_main_function": "0f",
        "ordinary_main_table": {"address": "08032e60", "count": 17,
            "entries": [{"command": f"{command:04x}",
                         "thumb_handler": f"{handler:08x}"}
                        for command, handler in main_table]},
        "radio_function": "10",
        "radio_table": {"address": "3c147be4", "count": 42,
            "entries": [{"command": f"{command:04x}",
                         "riscv_handler": f"{handler:08x}",
                         "native_raw_radio_parser_route": command <= 0x3F}
                        for command, handler in radio_table]},
    }


def suite(radio: bytes, main: bytes) -> dict:
    inventory = fixed_inventory(radio, main)
    rows = [Machine(radio).select_head(command) for command in range(32)]
    rows.extend(Machine(radio).select_head(command) for command in (32, 255, 65535))
    rssi_wire = packet(b"\xa1\1\x22", 0x22, function=0x10)
    controller_wire = packet(b"\xa1\1\x22", 0x100, function=0x0F)
    b64_rssi = base64.b64encode(rssi_wire).decode()
    b64_controller = base64.b64encode(controller_wire).decode()
    action = {"id": "action_set_ac_params", "param": {"acInputDisableSwitch": 1}}
    cases = [
        ("rssi_reference", {"data": b64_rssi}, "rssi"),
        ("named_disable_zero_with_rssi", {"data": b64_rssi, "id": "action_set_ac_params",
             "param": {"acInputDisableSwitch": 0}}, "rssi"),
        ("named_disable_one_with_rssi", {"data": b64_rssi, **action}, "rssi"),
        ("flat_capability_with_rssi", {"data": b64_rssi, "acInputDisableSwitch": 1,
             "supportAcInputDisable": 1}, "rssi"),
        ("named_action_without_data", action, "rejected"),
        ("flat_property_without_data", {"acInputDisableSwitch": 1}, "rejected"),
        ("sdk_arguments_without_data", {"deviceSn": "SYNTHETIC00000001",
             "bleMac": "02:00:00:00:00:01", "openTransaction": True, **action}, "rejected"),
        ("nonstring_data", {"data": 1, **action}, "rejected"),
        ("sdk_json_encoded_as_data", {"data": base64.b64encode(
             json.dumps(action).encode()).decode()}, "malformed_forward_boundary"),
        ("controller_query_reference", {"data": b64_controller}, "forward_boundary"),
        ("named_disable_zero_with_controller_query", {"data": b64_controller,
             "id": "action_set_ac_params", "param": {"acInputDisableSwitch": 0}},
             "forward_boundary"),
        ("named_disable_one_with_controller_query", {"data": b64_controller, **action},
             "forward_boundary"),
        ("unresolved_action_with_controller_query", {"data": b64_controller,
             "id": "synthetic_unresolved_action", "param": {"acInputDisableSwitch": 1}},
             "forward_boundary"),
    ]
    for name, payload, expected in cases:
        machine = Machine(radio)
        row = machine.send_payload(payload)
        assert machine.dispatch_calls == 1
        assert "data" in row["json_keys_requested"]
        if expected == "rssi":
            assert machine.raw_calls == machine.ap_info_calls == len(machine.reply_frames) == 1
            assert not machine.forward_attempts
            assert machine.reply_frames[0]["wire"] == packet(
                b"\0\xa1\4"+struct.pack("<i", -70), 0x822, function=0x10)
        elif expected == "forward_boundary":
            assert machine.raw_calls == 1 and not machine.ap_info_calls
            assert machine.forward_attempts == [{"function": 15,
                "command": "0100", "claimed_body_length": 3,
                "available_source_body_bytes": 4, "body_prefix_hex": "a10122",
                "source_length": 13}]
            assert not machine.reply_frames
        elif expected == "malformed_forward_boundary":
            assert machine.raw_calls == 1 and not machine.ap_info_calls
            raw = json.dumps(action).encode()
            assert machine.forward_attempts == [{"function": raw[6],
                "command": f"{int.from_bytes(raw[7:9], 'big') & 0x0FFF:04x}",
                "claimed_body_length": int.from_bytes(raw[2:4], "little")-10,
                "available_source_body_bytes": len(raw)-9,
                "body_prefix_hex": raw[9:41].hex(), "source_length": len(raw)}]
            assert machine.forward_attempts[0]["claimed_body_length"] > len(raw)
            assert not machine.reply_frames
        else:
            assert not machine.raw_calls and not machine.ap_info_calls
            assert not machine.forward_attempts and not machine.reply_frames
        rows.append({"case": name, "head_command": 17,
                     "expected_boundary": expected, **row})
    return {"schema": 1, "product": "C1000 Gen 2 A1763",
        "firmware": {"main_version": "1.1.4.9", "main_sha256": MAIN_SHA256,
                     "radio_version": "0.3.3.0", "radio_sha256": IMAGE_SHA256},
        "cases_passed": len(rows), "cases": rows, "inventory": inventory,
        "limits": __doc__.strip(),
        "unresolved": [
            "Flutter action-to-wire transformation inside AnkerIoT SDK is not replayed.",
            "No numeric charging-pause or AC-input-disable field is inferred from name absence.",
            "Main table inspection does not enumerate every protocol namespace or later installer.",
            "Other head command branches stop before their body and can have disruptive effects.",
            "Controller forwarding is captured before its wrapper executes; no main handler runs.",
            "Original C1000 and C2000 radio firmware have not been recovered or replayed.",
        ]}


def main() -> None:
    if not __debug__:
        raise SystemExit("Assertions are required; do not run with python -O.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radio-image", type=Path)
    parser.add_argument("--main-image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    default = Path(os.environ.get("SOLIX_FIRMWARE_DIR", repo/"firmware/c1000_gen2/1.1.4.9"))
    radio = (args.radio_image or default/IMAGE_NAME).read_bytes()
    main_image = (args.main_image or default/MAIN_NAME).read_bytes()
    assert len(radio) == RADIO_SIZE and digest(radio) == IMAGE_SHA256
    assert len(main_image) == MAIN_SIZE and digest(main_image) == MAIN_SHA256
    result = suite(radio, main_image)
    source = Path(__file__).resolve()
    dependencies = (source, source.with_name("emulate_radio_rssi_routes.py"),
                    source.with_name("emulate_radio_factory_routes.py"))
    manifest = {"schema": 1, "firmware": result["firmware"],
        "image_sizes": {IMAGE_NAME: len(radio), MAIN_NAME: len(main_image)},
        "cases_passed": result["cases_passed"],
        "sources": {path.name: digest(path.read_bytes()) for path in dependencies},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
                    "cryptography": cryptography.__version__}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (("gen2-iot-action-boundary-results.json", result),
                       ("gen2-iot-action-boundary-manifest.json", manifest)):
        (args.output_dir/name).write_text(json.dumps(body, indent=2, sort_keys=True)+"\n")
    print(f"{result['cases_passed']} synthetic action-boundary cases passed")


if __name__ == "__main__":
    os.umask(0o077)
    main()
