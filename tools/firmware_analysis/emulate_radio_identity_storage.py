#!/usr/bin/env python3
"""Replay A1763 radio 0.3.3.0 identity storage and MQTT cache selection offline.

Actual configuration setter, printable-ASCII validator, persistence decision,
configuration getter, MQTT parameter validation/copy, native JSON admission and
binding-check callback execute. Unbind/reset/error-effect callbacks are recording
stubs. MQTT init stops at its flash-write call after copying cached parameters;
no TLS, tasks, BLE pairing, Wi-Fi or controller code runs.
Flash writes and mutexes are recording/success stubs; ROM libc, cJSON, allocation,
session keys, identity-refresh effects, RSSI, crypto and TX inherit host substitutes
from the radio-route replay. Synthetic identities only; no device/network access.
This A1763 image does not establish original-C1000 or C2000 binary equivalence.
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

from emulate_radio_rssi_routes import NativeMachine
from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256, packet
from emulate_radio_local_identity import LOCAL, PREVIOUS

os.umask(0o077)
CONFIG = 0x3FC86FA8
RUNTIME_CONFIG = 0x3FC86C40
ACCOUNT_CONFIG = 0x3FC86FF0
ACCOUNT_RUNTIME = 0x3FC86C88
ACCOUNT_MQTT = 0x3FC89F08


class ParametersCopied(Exception):
    pass


class Machine(NativeMachine):
    def __init__(self, image):
        self.flash_writes = []
        self.parameter_copy = None
        super().__init__(image)
        self.uc.mem_write(CONFIG, struct.pack("<I", 0x20221223))
        self.uc.mem_write(RUNTIME_CONFIG, struct.pack("<I", 0x20221223))
        for address, value, width in (
            (0x3FC87070, b"http://192.0.2.1/", 128),
            (0x3FC86D08, b"http://192.0.2.1/", 128),
            (ACCOUNT_CONFIG, PREVIOUS.encode(), 128),
            (ACCOUNT_RUNTIME, PREVIOUS.encode(), 128),
            (ACCOUNT_MQTT, PREVIOUS.encode(), 128),
            (0x3FC87170, b"UTC0", 64), (0x3FC86E08, b"UTC0", 64),
            (0x3FC872B0, b"anker_power", 32), (0x3FC86F48, b"anker_power", 32)):
            self.uc.mem_write(address, (value + b"\0").ljust(width, b"\0"))
        self.account = PREVIOUS.encode()
        self.account_pointer = self.alloc(self.account+b"\0")

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address + size <= hi for lo, hi in (
            (RUNTIME_CONFIG, CONFIG + 0x368),
            (0x3FC89EB4, 0x3FC8A00C), (0x3FC902C8, 0x3FC902CA))):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address in (0x42011606, 0x4201162E):
            result = 0  # Synthetic successful configuration mutex operations.
        elif address == 0x40000370:
            left = self.read(a0, a2).split(b"\0", 1)[0]
            right = self.read(a1, a2).split(b"\0", 1)[0]
            result = (left > right) - (left < right)
        elif address == 0x42014EE6:
            self.flash_writes.append({"key": self.string(a0).decode(),
                "bytes": a2, "account": self.string(ACCOUNT_CONFIG).decode()})
            result = 0
        elif address in (0x42026878, 0x42026918):
            result = 0  # Synthetic MQTT parameter lock/load; RAM seeded directly.
        elif address == 0x420285F2:
            self.parameter_copy = {"account": self.string(ACCOUNT_MQTT).decode(),
                "magic": self.u32(0x3FC89EB4)}
            raise ParametersCopied
        elif address == 0x420122F4 or any(lo <= address < hi for lo, hi in (
            (0x42011EDA, 0x4201221E), (0x42012242, 0x4201232E),
            (0x4201B27E, 0x4201B2BC), (0x420115A4, 0x420115E8),
            (0x42028484, 0x420285F2))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def store_account(self, identity, *, persist):
        pointer = 0 if identity is None else self.alloc(identity.encode()+b"\0")
        self.run(0x42011EDA, 0, pointer, 0, 0, int(persist))

    def provider(self):
        self.run(0x420122F4)
        return self.string(self.uc.reg_read(R.UC_RISCV_REG_A0)).decode()

    def copy_mqtt_parameters(self, identity):
        # Actual parameter-copy routine input; upstream builder is not replayed.
        parameters = bytearray(0x154)
        for offset, value in ((4, b"anker_power"), (0x24, b"A1763"),
            (0x34, self.serial), (0x54, identity.encode())):
            parameters[offset:offset+len(value)] = value
        self.parameter_copy = None
        try:
            self.run(0x42028484, self.alloc(bytes(parameters)))
        except ParametersCopied:
            pass
        return self.parameter_copy

    def admission(self, identity):
        before = self.ap_info_calls
        envelope = self.envelope(packet(b"\xa1\1\x22", 0x22, function=16))
        payload = json.loads(envelope["payload"])
        payload["account_id"] = identity
        envelope["payload"] = json.dumps(payload)
        self.receive_json(envelope)
        return self.ap_info_calls > before


class BindingMachine(Machine):
    def __init__(self, image):
        self.binding_effects = []
        super().__init__(image)

    def step(self, uc, address, size, data):
        if address in (0x42011654, 0x42013B82, 0x4201FA80, 0x4202BF3A):
            self.binding_effects.append({"callback": f"{address:08x}",
                "argument0": uc.reg_read(R.UC_RISCV_REG_A0)})
            uc.reg_write(R.UC_RISCV_REG_A0, 0)
            uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))
            return
        if any(lo <= address < hi for lo, hi in (
            (0x4201B984, 0x4201BAFC), (0x4201FB34, 0x4201FD58))):
            return
        return super().step(uc, address, size, data)

    def check(self, response):
        raw = json.dumps(response, separators=(",", ":")).encode()
        self.run(0x4201FB34, self.alloc(raw + b"\0"), len(raw), 0, 0)
        result = self.uc.reg_read(R.UC_RISCV_REG_A0)
        return result if result < 0x80000000 else result - 0x100000000


def suite(image):
    rows = []
    identities = {"new_lower_hex": LOCAL, "new_upper_hex": LOCAL.upper(),
        "printable_nonhex": "z"*40, "printable_short": "local-only",
        "empty": "", "control_byte": "\x01" + LOCAL[1:],
        "nonascii": "\u00e9" + LOCAL[1:]}
    for label, identity in identities.items():
        for persist in (False, True):
            machine = Machine(image)
            before = machine.string(ACCOUNT_MQTT)
            machine.store_account(identity, persist=persist)
            valid = label not in ("control_byte", "nonascii")
            expected = identity if valid else PREVIOUS
            assert machine.provider() == expected
            assert machine.string(ACCOUNT_CONFIG).decode() == expected
            assert machine.string(ACCOUNT_MQTT) == before
            changed = valid and identity != PREVIOUS
            assert len(machine.flash_writes) == int(changed and persist)
            assert bool(machine.read(0x3FC902C8, 1)[0]) == bool(changed and not persist)
            rows.append({"case": "configuration_account_storage", "identity_shape": label,
                "persist_requested": persist, "accepted": valid,
                "account_provider_matches_new_value": valid,
                "mqtt_cached_identity_unchanged": True,
                "flash_write_count": len(machine.flash_writes),
                "deferred_dirty_flag": bool(machine.read(0x3FC902C8, 1)[0])})

    for persist in (False, True):
        for identity in (None, PREVIOUS):
            machine = Machine(image)
            machine.store_account(identity, persist=persist)
            assert machine.provider() == PREVIOUS and not machine.flash_writes
            assert machine.read(0x3FC902C8, 1) == b"\0"
            rows.append({"case": "unchanged_configuration", "account_pointer_null": identity is None,
                "persist_requested": persist, "flash_write_count": 0})

    for label, identity in (("new_local_hex", LOCAL), ("old_hex", PREVIOUS),
        ("empty_rejected_before_copy", "")):
        machine = Machine(image)
        copied = machine.copy_mqtt_parameters(identity)
        if identity == LOCAL:
            assert copied == {"account": identity, "magic": 0x20221224}
            assert machine.string(ACCOUNT_MQTT).decode() == identity
        elif identity:
            assert copied is None
            assert machine.uc.reg_read(R.UC_RISCV_REG_A0) == 0
            assert machine.string(ACCOUNT_MQTT).decode() == PREVIOUS
        else:
            assert copied is None
            assert machine.uc.reg_read(R.UC_RISCV_REG_A0) == 0x90004
            assert machine.string(ACCOUNT_MQTT).decode() == PREVIOUS
        rows.append({"case": "mqtt_parameter_copy", "identity_shape": label,
            "copied": copied is not None, "empty_identity_startup_error": 0x90004 if not identity else None,
            "unchanged_parameters_skipped": identity == PREVIOUS,
            "flash_or_tasks_after_copy_executed": False})

    machine = Machine(image)
    baseline_config = machine.read(CONFIG, 0x368)
    phases = []
    for name, stored, cached in (("before_change", PREVIOUS, PREVIOUS),
        ("after_store_before_mqtt_reload", LOCAL, PREVIOUS),
        ("after_mqtt_reload", LOCAL, LOCAL),
        ("after_restore_before_mqtt_reload", PREVIOUS, LOCAL),
        ("after_restored_mqtt_reload", PREVIOUS, PREVIOUS)):
        if name in ("after_store_before_mqtt_reload", "after_restore_before_mqtt_reload"):
            machine.store_account(stored, persist=True)
        elif "reload" in name and name not in (
            "after_store_before_mqtt_reload", "after_restore_before_mqtt_reload"):
            machine.copy_mqtt_parameters(stored)
        assert machine.provider() == stored
        old_accepted = machine.admission(PREVIOUS)
        new_accepted = machine.admission(LOCAL)
        assert old_accepted == (cached == PREVIOUS or stored == PREVIOUS)
        assert new_accepted == (cached == LOCAL or stored == LOCAL)
        phases.append({"phase": name, "provider_is_new": stored == LOCAL,
            "mqtt_cache_is_new": cached == LOCAL, "old_native_identity_accepted": old_accepted,
            "new_native_identity_accepted": new_accepted})
    assert machine.read(CONFIG, 0x368) == baseline_config
    assert len(machine.flash_writes) == 2
    rows.append({"case": "synthetic_reversible_identity_cycle", "phases": phases,
        "complete_configuration_restored": True, "configuration_flash_writes": 2,
        "radio_observations": machine.ap_info_calls,
        "refresh_calls_are_substituted": True, "pairing_state_executed": False})

    for bind in (0, 1, 2):
        for relate in (0, 1, 2):
            machine = BindingMachine(image)
            result = machine.check({"code": 0, "msg": "success",
                "data": {"bind_state": bind, "relate_state": relate}})
            assert result == -1  # Callback's success path retains its sentinel.
            effects = [row["callback"] for row in machine.binding_effects]
            expected = (["4201fa80"] if bind == 1 and relate == 0 else
                ["4202bf3a"] if bind == relate == 0 else [])
            assert effects == expected
            rows.append({"case": "binding_check_state", "bind_state": bind,
                "relate_state": relate, "return": result,
                "effect_callbacks_are_substituted": True,
                "effect_callbacks": machine.binding_effects})

    for label, response, expected in (
        ("missing_data", {"code": 0}, -6),
        ("missing_relate", {"code": 0, "data": {"bind_state": 1}}, -6),
        ("missing_bind", {"code": 0, "data": {"relate_state": 1}}, -7),
        ("ordinary_error_with_good_states", {"code": 10000,
            "data": {"relate_state": 1, "bind_state": 1}}, -1),
        ("binding_account_missing", {"code": 0,
            "data": {"relate_state": 1, "bind_state": 1}}, -1),
        ("binding_account_matches", {"code": 0,
            "data": {"relate_state": 1, "bind_state": 1, "account": PREVIOUS}}, -1),
        ("binding_account_differs", {"code": 0,
            "data": {"relate_state": 1, "bind_state": 1, "account": LOCAL}}, -1)):
        machine = BindingMachine(image)
        result = machine.check({"msg": "success", **response})
        assert result == expected
        effects = [row["callback"] for row in machine.binding_effects]
        assert effects == (["42011654", "42011654"] if response["code"] else [])
        rows.append({"case": "binding_check_response", "mutation": label,
            "return": result, "effect_callbacks_are_substituted": True,
            "effect_callbacks": machine.binding_effects})
    assert len(rows) == 38
    return {"cases_passed": len(rows), "cases": rows, "hardware_access": False,
        "cloud_access": False, "firmware_model": "A1763", "limitations": __doc__.strip()}


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
    result_path = args.output / "radio-identity-storage-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n"); result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_radio_local_identity.py",
        "emulate_radio_rssi_routes.py", "emulate_radio_factory_routes.py")
    manifest = {"firmware": {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256, "bytes": len(image)},
        "cases_passed": results["cases_passed"], "hardware_access": False, "cloud_access": False,
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
            "cryptography": cryptography.__version__},
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in sources},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "substitution_limits": __doc__.strip()}
    path = args.output / "radio-identity-storage-manifest.json"
    path.write_text(json.dumps(manifest, indent=2)+"\n"); path.chmod(0o600)
    print(json.dumps({"cases_passed": results["cases_passed"], "hardware_access": False,
        "cloud_access": False, "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
