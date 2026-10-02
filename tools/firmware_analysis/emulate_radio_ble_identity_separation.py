#!/usr/bin/env python3
"""Replay separate BLE allowlist and native identity paths in A1763 radio 0.3.3.0.

Actual BLE allowlist lookup/add/clear, application authorization/security getter,
Prime registration handler, native activation parsing/copy and configuration
setter execute. Activation stops immediately after its configuration setter;
WLAN credential parsing/copy stops at its application callback entry. Their
asynchronous networking, later activation effects, physical confirmation and
nonvolatile writes are not executed. Core registration callback pointers are
reconstructed from the actual application startup instructions.
OS mutex/state effects, logging, MAC/status providers, session-ready predicate,
authorization-port writes, registration reply and flash writes are recording or
success stubs. ROM strcmp/strncpy are host substitutes; inherited substitutes
are documented in the storage/native-route harnesses. Synthetic identities and
credentials only, no networking or device access. This A1763 binary does not
establish original-C1000 or C2000 binary equivalence or hardware rollback.
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

from emulate_radio_identity_storage import Machine as StorageMachine
from emulate_radio_identity_storage import ACCOUNT_CONFIG, CONFIG
from emulate_radio_local_identity import LOCAL, PREVIOUS, activation_fields
from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256

os.umask(0o077)
BLE_ACCOUNTS = 0x3FC8B5BC
BLE_SECURITY = 0x3FC906AC
CORE_CALLBACKS = 0x3FC8BD94
OTHER_PRIME = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class PrefixComplete(Exception):
    pass


class Machine(StorageMachine):
    def __init__(self, image, *, mode=2, accounts=(PREVIOUS, OTHER_PRIME)):
        self.ble_writes = []
        self.port_authorizations = []
        self.registration_replies = []
        self.pairing_prompts = []
        self.wlan_callback = None
        self.activation_prefix_complete = False
        super().__init__(image)
        self.seed_allowlist(accounts)
        self.uc.mem_write(BLE_SECURITY, bytes((mode, 0, 0)))
        self.uc.mem_write(0x3FC906A7, b"\1")  # Initialized synthetic mutex.
        # Pointer values come from application setup 42048632..420487e4.
        for offset, target in ((0x10, 0x4203D9D0), (0x14, 0x4204366A),
                (0x18, 0x420435D6), (0x1C, 0x420434AE), (0x20, 0x42042ABE)):
            self.uc.mem_write(CORE_CALLBACKS + offset, struct.pack("<I", target))
        # Synthetic check-code provider and actual activation callback.
        self.uc.mem_write(0x3FC8BE04, struct.pack("<I", 0x40000900))
        self.uc.mem_write(0x3FC8C2CC, struct.pack("<I", 0x42049092))
        # Credential application handoff is deliberately a boundary substitute.
        self.uc.mem_write(0x3FC8C2C8, struct.pack("<I", 0x40000904))

    def seed_allowlist(self, accounts):
        assert len(accounts) <= 16 and all(len(value) <= 47 for value in accounts)
        record = bytearray(0x310)
        struct.pack_into("<II", record, 0, 0x12345678, 1)
        record[8:10] = bytes((len(accounts), len(accounts) % 16))
        for index, account in enumerate(accounts):
            value = account.encode()
            offset = 0xE + index * 0x30
            record[offset:offset+len(value)] = value
        self.uc.mem_write(BLE_ACCOUNTS, bytes(record))

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address + size <= hi for lo, hi in (
                (BLE_ACCOUNTS, BLE_ACCOUNTS+0x310),
                (0x3FC8BD18, 0x3FC8BD58),
                (0x3FC8C278, 0x3FC8C29C), (0x3FC8C2D8, 0x3FC8C5C4),
                (0x3FC906E0, 0x3FC906E8))):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address == 0x4000036C:
            left, right = self.string(a0), self.string(a1)
            result = (left > right) - (left < right)
        elif address == 0x40000368:
            value = self.string(a1)[:a2]
            uc.mem_write(a0, value.ljust(a2, b"\0"))
            result = a0
        elif address in (0x4202D6F8, 0x4202D71C, 0x4202D3B0, 0x4204A074,
                0x42029228, 0x4201881E, 0x4201A85C, 0x42028E10):
            result = 0  # Mutex, logs, state/status and provisioning activity.
        elif address == 0x42030EE2:
            uc.mem_write(a0, b"SYNTHETIC-MAC\0")
            uc.mem_write(a1, struct.pack("<I", 13))
            result = 1
        elif address == 0x42051E3C:
            result = 1  # Synthetic established encrypted Prime session.
        elif address == 0x42051D4E:
            self.port_authorizations.append({"port": a0, "authorized": bool(a1)})
            result = 0
        elif address == 0x4204366A:
            self.pairing_prompts.append({"port": a0, "physical_effect_executed": False})
            result = 1
        elif address == 0x420434AE:
            result = 0  # Physical confirmation window closed.
        elif address == 0x4204FA68:
            self.registration_replies.append(self.read(a1, a2).hex())
            result = 0
        elif address == 0x4204E0A0:
            self.registration_replies.append(bytes((a1,)).hex())
            result = 0
        elif address == 0x42014EE6 and self.string(a0) == b"ble_user_accounts":
            assert a1 == BLE_ACCOUNTS and a2 == 0x310
            self.ble_writes.append({"key": "ble_user_accounts", "bytes": a2,
                "entry_count": self.read(BLE_ACCOUNTS+8, 1)[0]})
            result = 0
        elif address == 0x40000900:
            uc.mem_write(a0, b"synthetic-check\0")
            uc.mem_write(a1, b"\x0f")
            result = 1
        elif address == 0x40000904:
            self.wlan_callback = self.read(a0, 0x78)
            raise PrefixComplete
        elif address == 0x420491C6:
            self.activation_prefix_complete = True
            raise PrefixComplete
        elif any(lo <= address < hi for lo, hi in (
                (0x4203D9D0, 0x4203DA2C), (0x420435D6, 0x4204366A),
                (0x42042ABE, 0x42042B24), (0x4204A332, 0x4204A3E8),
                (0x4204A4F8, 0x4204A728), (0x4204E57A, 0x4204E7FC),
                (0x4204F95C, 0x4204FA68), (0x42049092, 0x420491C6),
                (0x42053684, 0x420539F2), (0x42052D16, 0x42053050))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def ble_accepts(self, identity):
        pointer = 0 if identity is None else self.alloc(identity.encode()+b"\0")
        self.run(0x420435D6, 0, pointer)
        return bool(self.uc.reg_read(R.UC_RISCV_REG_A0))

    def context(self, fields, *, encrypted=True):
        context = self.alloc(0x700)
        body = b"".join(bytes((tag, len(value)))+value for tag, value in fields)
        frame = bytearray(10)
        frame[7] = 0x40 if encrypted else 0
        self.uc.mem_write(context+4, struct.pack("<I", self.alloc(bytes(frame))))
        self.run(0x4204F9A6, self.alloc(body), len(body), context+0x18)
        return context

    def register(self, identity, *, encrypted=True):
        before = len(self.registration_replies)
        self.run(0x4204E57A, self.context([(0xA2, identity.encode())], encrypted=encrypted))
        assert len(self.registration_replies) == before+1
        return bytes.fromhex(self.registration_replies[-1])[0]

    def activate_prefix(self, identity):
        self.activation_prefix_complete = False
        try:
            self.run(0x420537D8, self.context(activation_fields(identity)))
        except PrefixComplete:
            pass
        assert self.activation_prefix_complete
        # Reset synthetic stack after the deliberate in-function boundary stop.
        self.uc.reg_write(R.UC_RISCV_REG_SP, 0x3FD0F000)

    def credentials_prefix(self):
        self.wlan_callback = None
        fields = [(0xA3, b"SYNTHETIC-AP"), (0xA4, b"\1"),
            (0xA5, b"\0"), (0xA6, b"synthetic-password")]
        try:
            self.run(0x42053684, self.context(fields))
        except PrefixComplete:
            pass
        assert self.wlan_callback is not None
        assert self.wlan_callback[:12] == b"SYNTHETIC-AP"
        assert self.wlan_callback[0x24:0x36] == b"synthetic-password"
        self.uc.reg_write(R.UC_RISCV_REG_SP, 0x3FD0F000)


def suite(image):
    rows = []
    for mode in (1, 2):
        for label, identity, expected in (("retained_prime", PREVIOUS, True),
                ("second_retained_prime", OTHER_PRIME, True),
                ("generated_native_only", LOCAL, False),
                ("case_mismatch", PREVIOUS.upper(), False),
                ("prefix", PREVIOUS[:-1], False), ("empty", "", False),
                ("null", None, False)):
            machine = Machine(image, mode=mode)
            baseline = machine.read(BLE_ACCOUNTS, 0x310)
            assert machine.ble_accepts(identity) is expected
            assert machine.read(BLE_ACCOUNTS, 0x310) == baseline
            assert not machine.ble_writes
            rows.append({"case": "actual_ble_account_checker", "security_mode": mode,
                "identity_shape": label, "accepted": expected,
                "complete_allowlist_unchanged": True, "ble_flash_write_count": 0})

    for mode in (1, 2):
        for label, identity in (("retained_prime", PREVIOUS), ("generated_native_only", LOCAL)):
            machine = Machine(image, mode=mode)
            baseline = machine.read(BLE_ACCOUNTS, 0x310)
            status = machine.register(identity)
            expected = 0 if identity == PREVIOUS else (1 if mode == 1 else 9)
            assert status == expected
            assert bool(machine.port_authorizations) == (identity == PREVIOUS)
            assert len(machine.pairing_prompts) == int(mode == 2 and identity != PREVIOUS)
            assert machine.read(BLE_ACCOUNTS, 0x310) == baseline and not machine.ble_writes
            rows.append({"case": "actual_prime_registration", "security_mode": mode,
                "identity_shape": label, "reply_status": status,
                "port_authorization_recorded": bool(machine.port_authorizations),
                "physical_prompt_substituted": bool(machine.pairing_prompts),
                "complete_allowlist_unchanged": True})

    for mode in (1, 2):
        machine = Machine(image, mode=mode)
        assert machine.register(PREVIOUS, encrypted=False) == 1
        assert not machine.port_authorizations
        rows.append({"case": "unencrypted_registration_rejected", "security_mode": mode,
            "reply_status": 1, "allowlist_check_executed": False})

    for route in ("configuration_setter", "normal_activation_prefix"):
        machine = Machine(image)
        baseline = machine.read(BLE_ACCOUNTS, 0x310)
        baseline_config = machine.read(CONFIG, 0x368)
        phases = []
        for name, identity, reload in (("baseline", PREVIOUS, False),
                ("after_native_account_change", LOCAL, False),
                ("after_native_parameter_reload", LOCAL, True),
                ("after_native_account_restore", PREVIOUS, False),
                ("after_native_parameter_restore", PREVIOUS, True)):
            if name in ("after_native_account_change", "after_native_account_restore"):
                if route == "configuration_setter":
                    machine.store_account(identity, persist=True)
                else:
                    machine.activate_prefix(identity)
            elif reload:
                machine.copy_mqtt_parameters(identity)
            assert machine.provider() == identity
            assert machine.ble_accepts(PREVIOUS) and machine.ble_accepts(OTHER_PRIME)
            assert not machine.ble_accepts(LOCAL)
            assert machine.register(PREVIOUS) == 0 and machine.register(LOCAL) == 9
            assert machine.read(BLE_ACCOUNTS, 0x310) == baseline
            assert not machine.ble_writes
            phases.append({"phase": name, "native_provider_is_generated": identity == LOCAL,
                "retained_prime_registration_status": 0, "generated_registration_status": 9,
                "complete_ble_allowlist_unchanged": True})
        # Activation also supplies API/timezone/service, so only account restoration
        # is required there; direct account-only setter restores the entire block.
        assert machine.string(ACCOUNT_CONFIG).decode() == PREVIOUS
        if route == "configuration_setter":
            assert machine.read(CONFIG, 0x368) == baseline_config
        assert len(machine.flash_writes) == 2
        rows.append({"case": "reversible_native_identity_cycle", "route": route,
            "phases": phases, "native_account_restored": True,
            "configuration_flash_writes": 2, "ble_flash_write_count": 0,
            "later_activation_effects_executed": False})

    machine = Machine(image)
    baseline = machine.read(BLE_ACCOUNTS, 0x310)
    machine.credentials_prefix()
    assert machine.read(BLE_ACCOUNTS, 0x310) == baseline
    assert machine.ble_accepts(PREVIOUS) and not machine.ble_accepts(LOCAL)
    assert not machine.ble_writes and not machine.flash_writes
    rows.append({"case": "normal_wlan_credentials_prefix", "application_callback_executed": False,
        "parsed_credentials_copied": True, "complete_allowlist_unchanged": True})

    for mode in (1, 2, 3, 4):
        machine = Machine(image, mode=mode)
        machine.run(0x42042ABE, 0, machine.alloc(LOCAL.encode()+b"\0"))
        assert machine.ble_accepts(LOCAL)
        preserved = mode in (1, 2)
        assert machine.ble_accepts(PREVIOUS) is preserved
        assert machine.ble_accepts(OTHER_PRIME) is preserved
        assert len(machine.ble_writes) == (1 if preserved else 2)
        assert machine.provider() == PREVIOUS
        rows.append({"case": "explicit_save_auth_callback", "security_mode": mode,
            "old_accounts_preserved": preserved, "new_prime_account_added": True,
            "native_account_unchanged": True, "ble_flash_write_count": len(machine.ble_writes),
            "callback_invoked_directly": True})

    for mode in (2, 3):
        for identity in (None, ""):
            machine = Machine(image, mode=mode)
            baseline = machine.read(BLE_ACCOUNTS, 0x310)
            pointer = 0 if identity is None else machine.alloc(b"\0")
            machine.run(0x42042ABE, 0, pointer)
            assert machine.uc.reg_read(R.UC_RISCV_REG_A0) == 1
            assert machine.read(BLE_ACCOUNTS, 0x310) == baseline and not machine.ble_writes
            rows.append({"case": "empty_save_auth_callback", "security_mode": mode,
                "account_pointer_null": identity is None, "reply_return": 1,
                "complete_allowlist_unchanged": True})

    full = (PREVIOUS,)+tuple(f"{value:040x}" for value in range(1, 16))
    machine = Machine(image, accounts=full)
    machine.run(0x42042ABE, 0, machine.alloc(LOCAL.encode()+b"\0"))
    assert machine.ble_accepts(LOCAL) and not machine.ble_accepts(PREVIOUS)
    assert all(machine.ble_accepts(identity) for identity in full[1:])
    assert machine.read(BLE_ACCOUNTS+8, 2) == b"\x10\1"
    assert len(machine.ble_writes) == 1 and machine.provider() == PREVIOUS
    rows.append({"case": "full_allowlist_explicit_save", "security_mode": 2,
        "entry_count": 16, "oldest_prime_evicted": True, "other_15_preserved": True,
        "new_prime_added": True, "native_account_unchanged": True,
        "callback_invoked_directly": True})

    assert len(rows) == 32
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
    image = (args.image or directory/IMAGE_NAME).read_bytes()
    assert len(image) == 1482800 and hashlib.sha256(image).hexdigest() == IMAGE_SHA256
    results = suite(image)
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output/"radio-ble-identity-separation-results.json"
    result_path.write_text(json.dumps(results, indent=2)+"\n"); result_path.chmod(0o600)
    package = Path(__file__).resolve().parent
    sources = (Path(__file__).name, "emulate_radio_identity_storage.py",
        "emulate_radio_local_identity.py", "emulate_radio_rssi_routes.py",
        "emulate_radio_factory_routes.py")
    manifest = {"firmware": {"filename": IMAGE_NAME, "sha256": IMAGE_SHA256, "bytes": len(image)},
        "cases_passed": results["cases_passed"], "hardware_access": False, "cloud_access": False,
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
            "cryptography": cryptography.__version__},
        "source_sha256": {name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in sources},
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "substitution_limits": __doc__.strip()}
    path = args.output/"radio-ble-identity-separation-manifest.json"
    path.write_text(json.dumps(manifest, indent=2)+"\n"); path.chmod(0o600)
    print(json.dumps({"cases_passed": results["cases_passed"], "hardware_access": False,
        "cloud_access": False, "result_sha256": manifest["result_sha256"]}))


if __name__ == "__main__":
    main()
