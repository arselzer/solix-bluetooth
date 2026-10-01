#!/usr/bin/env python3
"""Replay local activation identities and native admission in A1763 radio 0.3.3.0.

Actual RISC-V activation TLV parsing, activation copying, business callback and
native JSON admission execute. Activation stops at the persistent configuration
setter entry; it does not execute persistence, binding, Wi-Fi or MQTT startup.
OS/libc/allocation, cJSON, session keys, identity/check-code providers, refresh
providers, RSSI, GCM, BLE TX and final MQTT publish are host substitutes inherited
from the route replay. Synthetic data only; no network or device access.
Original A1761/C2000 binary equivalence and generated-ID onboarding are unproved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import cryptography
import unicorn
from unicorn import riscv_const as R

from emulate_radio_rssi_routes import NativeMachine, assert_response
from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256, packet

os.umask(0o077)
LOCAL = "0123456789abcdef0123456789abcdef01234567"
PREVIOUS = "fedcba9876543210fedcba9876543210fedcba98"


class ReachedStore(Exception):
    pass


class ActivationMachine(NativeMachine):
    def __init__(self, image):
        self.activation = None
        self.rejected = []
        super().__init__(image)
        # Synthetic core identity provider and actual business activation callback.
        self.uc.mem_write(0x3FC8BE04, struct.pack("<I", 0x40000900))
        self.uc.mem_write(0x3FC8C2CC, struct.pack("<I", 0x42049092))

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address + size <= hi for lo, hi in (
            (0x3FC8BE04, 0x3FC8BE08), (0x3FC8C278, 0x3FC8C29C),
            (0x3FC8C2CC, 0x3FC8C2D0), (0x3FC8C2D8, 0x3FC8C54C),
            (0x3FC906E0, 0x3FC906E4))):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1 = args[:2]
        if address == 0x40000900:
            uc.mem_write(a0, b"synthetic-check\0")
            uc.mem_write(a1, b"\x0f")
            result = 1
        elif address in (0x4201881E, 0x4201A85C):
            result = 0  # Synthetic inactive provisioning task.
        elif address == 0x4204E0A0:
            self.rejected.append(a1)
            result = 0
        elif address == 0x42011EDA:
            self.activation = dict(zip(("api", "account", "timezone", "app_id"),
                (self.string(pointer).decode() for pointer in args[:4])))
            self.activation["persist_requested"] = bool(args[4])
            raise ReachedStore
        elif any(lo <= address < hi for lo, hi in (
            (0x4204F95C, 0x4204FA68),
            (0x420537D8, 0x420539F2), (0x42052E48, 0x42053050),
            (0x42049092, 0x420491C6))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def activate(self, fields):
        context = self.alloc(0x700)
        body = b"".join(bytes((tag, len(value))) + value for tag, value in fields)
        self.run(0x4204F9A6, self.alloc(body), len(body), context + 0x18)
        try:
            self.run(0x420537D8, context)
        except ReachedStore:
            pass


def activation_fields(identity):
    return [(0xA1, bytes(4)), (0xA2, identity.encode()),
        (0xA3, b"http://192.0.2.1/"), (0xA4, b"UTC0"),
        (0xA6, b"anker_power"), (0xA7, b"A1763"), (0xA8, b"Etc/UTC"),
        (0xC3, b"A2")]


def set_native_identity(machine, identity):
    machine.account = identity.encode()
    machine.account_pointer = machine.alloc(machine.account + b"\0")
    machine.uc.mem_write(0x3FC89F08, (machine.account + b"\0").ljust(128, b"\0"))


def suite(image):
    rows = []
    identities = {"generated_shape_lowercase": LOCAL,
        "generated_shape_uppercase": LOCAL.upper(), "zero_hex": "0"*40,
        "different_local_hex": PREVIOUS, "opaque_short": "local-only",
        "nonhex_40": "z"*40, "maximum_stored_127": "x"*127}
    for label, identity in identities.items():
        machine = ActivationMachine(image)
        machine.activate(activation_fields(identity))
        assert machine.activation is not None and not machine.rejected
        assert machine.activation["account"] == identity
        assert machine.activation["api"] == "http://192.0.2.1/"
        assert machine.activation["app_id"] == "anker_power"
        assert machine.activation["persist_requested"] is True
        rows.append({"case": "activation_identity", "identity_shape": label,
            "identity_bytes": len(identity), "account_unchanged_at_store_entry": True,
            "persistence_executed": False})

    for tag in (0xA2, 0xA3, 0xA4):
        machine = ActivationMachine(image)
        machine.activate([(key, value) for key, value in activation_fields(LOCAL) if key != tag])
        assert machine.activation is None and machine.rejected == [4]
        rows.append({"case": "missing_required_activation_field", "tag": f"{tag:02x}",
            "rejection_status": 4, "configuration_setter_reached": False})

    fields = activation_fields(LOCAL)
    for label, ordered, expected_service in (
        ("country_field_inserted", sorted(fields + [(0xA5, b"AT")]), "anker_power"),
        ("early_c3_hides_service", fields[:4] + [fields[-1]] + fields[4:-1], ""),
        ("empty_account_present", [(tag, b"" if tag == 0xA2 else value) for tag, value in fields], "")):
        machine = ActivationMachine(image)
        machine.activate(ordered)
        if label == "empty_account_present":
            assert machine.activation is not None and not machine.rejected
            assert machine.activation["account"] == ""
            rows.append({"case": label, "account_bytes": 0,
                "configuration_setter_reached": True, "persistence_executed": False})
        else:
            assert machine.activation is not None and not machine.rejected
            assert machine.activation["account"] == LOCAL
            assert machine.activation["app_id"] == expected_service
            rows.append({"case": label, "account_unchanged_at_store_entry": True,
                "app_id": expected_service, "persistence_executed": False})

    mutations = ("matching_lowercase", "matching_uppercase", "case_mismatch",
        "different_prior_identity", "last_hex_changed", "account_prefix",
        "account_suffix", "missing_account", "missing_serial", "different_serial",
        "head_account_irrelevant", "sign_code_absent", "matching_zero_hex",
        "matching_empty", "matching_nonhex_40", "matching_opaque_short")
    for label in mutations:
        machine = NativeMachine(image)
        stored = {"matching_uppercase": LOCAL.upper(), "matching_zero_hex": "0"*40,
            "matching_empty": "", "matching_nonhex_40": "z"*40,
            "matching_opaque_short": "local-only"}.get(label, LOCAL)
        set_native_identity(machine, stored)
        envelope = machine.envelope(packet(b"\xa1\1\x22", 0x22, function=16))
        payload = json.loads(envelope["payload"])
        if label == "case_mismatch":
            payload["account_id"] = stored.upper()
        elif label == "different_prior_identity":
            payload["account_id"] = PREVIOUS
        elif label == "last_hex_changed":
            payload["account_id"] = stored[:-1] + "8"
        elif label == "account_prefix":
            payload["account_id"] = stored[:-1]
        elif label == "account_suffix":
            payload["account_id"] = stored + "8"
        elif label == "missing_account":
            del payload["account_id"]
        elif label == "missing_serial":
            del payload["device_sn"]
        elif label == "different_serial":
            payload["device_sn"] = "DIFFERENT0000001"
        elif label == "head_account_irrelevant":
            envelope["head"]["account_id"] = PREVIOUS
        elif label == "sign_code_absent":
            del envelope["head"]["sign_code"]
        envelope["payload"] = json.dumps(payload)
        machine.receive_json(envelope)
        accepted = label in ("matching_lowercase", "matching_uppercase",
            "matching_zero_hex", "head_account_irrelevant", "sign_code_absent",
            "matching_empty", "matching_nonhex_40", "matching_opaque_short")
        assert machine.ap_info_calls == int(accepted)
        assert len(machine.reply_frames) == len(machine.publications) == int(accepted)
        if accepted:
            assert not machine.refresh_requests
            assert_response(machine, encrypted=False, mqtt=True, rssi=-70, error=0)
        rows.append({"case": "native_identity_admission", "mutation": label,
            "accepted": accepted, "radio_observations": machine.ap_info_calls,
            "substituted_identity_refresh_calls": machine.refresh_requests})

    assert len(rows) == 29
    return {"cases_passed": len(rows), "cases": rows,
        "hardware_access": False, "cloud_access": False,
        "firmware_model": "A1763", "limitations": __doc__.strip()}


def main():
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = Path(os.environ.get("SOLIX_FIRMWARE_DIR", Path(__file__).resolve().parents[2]
        / "firmware/c1000_gen2/1.1.4.9"))
    image = (args.image or directory / IMAGE_NAME).read_bytes()
    assert len(image) == 1482800 and hashlib.sha256(image).hexdigest() == IMAGE_SHA256
    results = suite(image)
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "radio-local-identity-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n")
    result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_radio_rssi_routes.py", "emulate_radio_factory_routes.py")
    manifest = {"firmware": {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256, "bytes": len(image)},
        "cases_passed": results["cases_passed"], "hardware_access": False, "cloud_access": False,
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
            "cryptography": cryptography.__version__},
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in sources},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "substitution_limits": __doc__.strip()}
    path = args.output / "radio-local-identity-manifest.json"
    path.write_text(json.dumps(manifest, indent=2)+"\n")
    path.chmod(0o600)
    print(json.dumps({"cases_passed": results["cases_passed"], "hardware_access": False,
        "cloud_access": False, "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
