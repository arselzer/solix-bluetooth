#!/usr/bin/env python3
"""Offline A1763 first BLE initialization and activation callback boundaries.

Executes actual native 0024 ingress/dispatch, first SDK BLE timer/default setup,
BLE connection/status callbacks and the normal activation application callback.
Physical radio, OS timers, networking/binding lifecycle operations and selected
providers/storage interfaces are explicit recording substitutes. No live
device, network, credentials or filesystem persistence is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import struct

import capstone
from capstone import riscv as C
import cryptography
import unicorn
from unicorn import riscv_const as R

from emulate_radio_ble_advertising import Machine as AdvertisingMachine
from emulate_radio_ble_advertising import APP_BLE, APP_WIFI
from emulate_radio_ble_identity_separation import Machine as IdentityMachine, BLE_ACCOUNTS
from emulate_radio_identity_storage import ACCOUNT_CONFIG, ACCOUNT_MQTT
from emulate_radio_local_identity import activation_fields, LOCAL, PREVIOUS
from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256

os.umask(0o077)
DIRECT_WRITER_CALLS = (0x42044186, 0x4204428C, 0x420442D6, 0x4204434A,
    0x420444B2, 0x42045640, 0x42045650, 0x42048CB4, 0x42048CC2)


def direct_census(image):
    """Reproduce direct branches in the XIP segment, without inferring reachability.

    This linear instruction census excludes indirect callers and is followed by
    bounded execution of the two identified selector-0 producer bodies.
    """
    decoder = capstone.Cs(capstone.CS_ARCH_RISCV,
        capstone.CS_MODE_RISCV32 | capstone.CS_MODE_RISCVC)
    decoder.detail = True
    decoder.skipdata = True
    offset = 24
    xip = b""
    calls = {0x42028FA4: [], 0x42043FD8: [], 0x42049092: []}
    for _ in range(image[1]):
        address, size = struct.unpack_from("<II", image, offset)
        offset += 8
        segment = image[offset:offset+size]
        offset += size
        if address != 0x42000020:
            continue
        xip = segment
        for ins in decoder.disasm(segment, address):
            if ins.mnemonic not in ("jal", "j", "c.j", "c.jal"):
                continue
            target = (ins.address+ins.operands[-1].imm) & 0xFFFFFFFF
            if target in calls:
                calls[target].append(ins.address)
    assert calls[0x42028FA4] == [0x4202906A, 0x42029154]
    assert calls[0x42043FD8] == list(DIRECT_WRITER_CALLS)
    assert calls[0x42049092] == [0x420496F8, 0x42049EFA]
    selectors = (1, 1, 1, 3, 4, 0, 3, 1, 0)
    selector_sites = (0x42044184, 0x4204428A, 0x420442D4, 0x42044348,
        0x420444B0, 0x4204563A, 0x4204564E, 0x42048CB2, 0x42048CC0)
    for site, expected in zip(selector_sites, selectors):
        ins = next(decoder.disasm(xip[site-0x42000020:site-0x42000020+2], site))
        assert ins.mnemonic == "c.li" and ins.operands[0].reg == C.RISCV_REG_A0
        assert ins.operands[1].imm == expected
    return {"scan": "linear XIP direct branches; indirect callers are not enumerated",
        "first_sdk_initialization": [f"{value:08x}" for value in calls[0x42028FA4]],
        "normal_activation_application": [f"{value:08x}" for value in calls[0x42049092]],
        "application_wireless_writer": [{"call_site": f"{value:08x}",
            "selector": selector} for value, selector in zip(DIRECT_WRITER_CALLS, selectors)]}


class InitializationMachine(AdvertisingMachine):
    def __init__(self, image, *, ble_state=1):
        self.created_timers = []
        self.network_boundaries = []
        self.flash_boundaries = []
        self.status_emissions = []
        self.status_deferral = False
        self.synthetic_update_gate = False
        self.update_check_boundaries = []
        super().__init__(image, ble_state=ble_state)
        self.uc.mem_write(0x3FC903FE, b"\0")

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address+size <= hi for lo, hi in (
                (0x3FC8A1A0, 0x3FC8A1D0),  # SDK advertising/link timers.
                (0x3FC82A06, 0x3FC82A08),  # Advertising interval in 0.625 ms units.
                (0x3FC82F64, 0x3FC82F6C),  # Link interval/latency/timeout defaults.
                (0x3FC90644, 0x3FC90649),  # Application wireless flags/status latch.
                (0x3FC90650, 0x3FC90654),  # Last BLE disconnect reason.
                (0x3FC90660, 0x3FC90670))):  # BLE authorization/pairing state.
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1 = args[:2]
        if address == 0x4202DC94:
            self.created_timers.append({"timer": f"{a0:08x}",
                "callback": f"{self.u32(a0+8):08x}",
                "period_ms": self.u32(a0+0x14),
                "flags": [self.u32(a0+0xC), self.u32(a0+0x10)]})
            # Synthetic OS handle, callback registration fields execute above.
            uc.mem_write(a0, struct.pack("<I", self.alloc(16)))
            result = 0
        elif address == 0x42037C20:
            self.effects.append({"effect": "physical_ble_tx_power_boundary", "argument": a0})
            result = 0
        elif address == 0x420499E0:
            result = int(self.status_deferral)  # Synthetic application lifecycle provider.
        elif address == 0x42042B24:
            self.status_emissions.append({"ble": a0, "wifi": a1})
            result = 0  # MCU wireless-status report builder boundary, not output control.
        elif address == 0x42051CD4:
            self.effects.append({"effect": "protocol_port_connection_boundary", "port": a0,
                                 "connected": a1})
            result = 0
        elif address == 0x4204A0DA:
            self.effects.append({"effect": "ble_disconnect_cleanup_boundary"})
            result = 0  # Security/session cleanup implementation not executed.
        elif address == 0x4202DF98:
            result = 1700000000
        elif address == 0x42028D5A:
            self.effects.append({"effect": "physical_ble_disconnect_boundary"})
            result = 0
        elif address in (0x4202A230, 0x4201FD58, 0x42043782):
            self.update_check_boundaries.append({"boundary": f"{address:08x}", "argument0": a0})
            result = 0  # OTA state, update request and MCU-notification boundaries.
        elif address == 0x42029F60:
            result = int(self.synthetic_update_gate)
        elif address in (0x42014EE6, 0x42014EEC):
            self.flash_boundaries.append(f"{address:08x}")
            raise AssertionError("Unexpected persistence in BLE initialization/callback")
        elif address in (0x42011EDA, 0x4201FA80, 0x4202BF3A, 0x4202C24E,
                         0x42020DEE, 0x42020F34, 0x4202013E):
            self.network_boundaries.append(f"{address:08x}")
            raise AssertionError("Unexpected identity/network mutation in BLE path")
        elif any(lo <= address < hi for lo, hi in (
                (0x42028FA4, 0x42029052), (0x42028F9C, 0x42028FA4),
                (0x420379F4, 0x42037A28), (0x42037AC0, 0x42037B48),
                (0x42043FD8, 0x420440F2), (0x4204562C, 0x42045792),
                (0x42043864, 0x4204393C), (0x42048CB8, 0x42048CC6),
                (0x42028CE8, 0x42028CF2))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))


class ActivationMachine(IdentityMachine):
    """Run whole application activation body with explicit lifecycle boundaries."""
    def __init__(self, image, *, provisioning_busy=False, allocation_failure=False):
        self.lifecycle = []
        self.provisioning_busy = provisioning_busy
        self.allocation_failure = allocation_failure
        super().__init__(image)

    def write_guard(self, uc, access, address, size, value, data):
        if 0x3FC8B51C <= address < address+size <= 0x3FC8B558:
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address == 0x420491C6:
            return  # Remove only inherited deliberate prefix stop.
        if address == 0x4201A85C:
            result = int(self.provisioning_busy)
        elif address == 0x42017E00:
            result = 0 if self.allocation_failure else self.alloc(a0)
        elif address == 0x42030EB2:
            uc.mem_write(a0, bytes.fromhex("020000000001"))
            result = 0
        elif address == 0x4204BC48:
            value = b"020000000001"
            assert len(value)+1 <= a1
            uc.mem_write(a0, value+b"\0")
            result = len(value)
        elif address in (0x4202E2FA, 0x4202E20C):
            result = self.alloc(b"SYNTHETIC-AP\0")
        elif address == 0x4202C362:
            result = 0  # No synthetic extra WLAN/password provider.
        elif address in (0x420387F2, 0x42011D30, 0x420119CC, 0x420118DA,
                0x42013666, 0x4201221E, 0x4201F766, 0x4201F810,
                0x4201F96A, 0x420385D6, 0x4203B958, 0x42020DEE,
                0x42020F34, 0x4202013E):
            self.lifecycle.append({"boundary": f"{address:08x}", "argument0": a0})
            result = 0  # Explicit config/network/binding lifecycle boundary.
        elif address == 0x4202DF98:
            result = 1700000000
        elif any(lo <= address < hi for lo, hi in (
                (0x42049092, 0x4204956C), (0x4201221E, 0x42012242))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def activate(self, identity):
        self.run(0x420537D8, self.context(activation_fields(identity)))


def suite(image):
    census = direct_census(image)
    cases = []
    for state in (0, 1, 2, 3):
        machine = InitializationMachine(image, ble_state=state)
        baseline = machine.protected()
        machine.send_native(0x24, b"\xa1\1\1")
        assert machine.read(0x3FC903FE, 1) == b"\1"
        assert machine.created_timers == [
            {"timer": "3fc8a1b8", "callback": "42028c14", "period_ms": 1000, "flags": [1, 1]},
            {"timer": "3fc8a1a0", "callback": "420291f0", "period_ms": 1000, "flags": [1, 1]}]
        assert machine.protected() == baseline
        assert not machine.flash_boundaries and not machine.network_boundaries
        assert machine.read(APP_BLE, 2) == b"\0\1"
        assert machine.read(0x3FC82A06, 2) == struct.pack("<H", 160)
        assert machine.read(0x3FC82F64, 8) == struct.pack("<4H", 80, 96, 1, 400)
        assert machine.handlers == ["4203d056"] and not machine.forward_attempts
        cases.append({"case": "native_first_sdk_ble_enable", "synthetic_pal_state": state,
            "created_timers": machine.created_timers, "effects": machine.effects,
            "advertisement_interval_units": int.from_bytes(machine.read(0x3FC82A06, 2), "little"),
            "link_default_hex": machine.read(0x3FC82F64, 8).hex(),
            "protected_identity_regions_unchanged": True, "persistence_boundaries": 0,
            "network_mutation_boundaries": 0, **machine.response(0x24, b"\0")})
    machine = InitializationMachine(image)
    baseline = machine.protected()
    machine.send_native(0x24, b"\xa1\1\1")
    first_timers = list(machine.created_timers)
    machine.reply_frames.clear()
    machine.publications.clear()
    machine.send_native(0x24, b"\xa1\1\1")
    assert machine.created_timers == first_timers and len(first_timers) == 2
    assert sum(row["effect"] == "physical_ble_tx_power_boundary" for row in machine.effects) == 1
    assert sum(row["effect"] == "os_timer_start" for row in machine.effects) == 2
    assert machine.protected() == baseline
    cases.append({"case": "first_sdk_initialization_is_not_repeated",
        "requests": 2, "created_timers": 2, "physical_ble_tx_power_boundaries": 1,
        "advertising_timer_start_boundaries": 2,
        "protected_identity_regions_unchanged": True, **machine.response(0x24, b"\0")})
    for state, deferred in itertools.product((0, 1), (False, True)):
        machine = InitializationMachine(image)
        machine.status_deferral = deferred
        machine.uc.mem_write(0x3FC90648, struct.pack("<I", 2))
        baseline = machine.protected()
        machine.run(0x4204562C, state, 0)
        assert machine.read(APP_BLE, 1) == bytes((state,))
        assert machine.protected() == baseline
        assert machine.status_emissions == ([{"ble": 1, "wifi": 1}]
            if state == 1 and not deferred else [])
        cases.append({"case": "actual_ble_connection_state_callback", "connection_state": state,
            "synthetic_defer_status": deferred, "application_ble_flag": state,
            "status_emissions": machine.status_emissions, "effects": machine.effects,
            "protected_identity_regions_unchanged": True})
    for selector, value, deferred in itertools.product((0, 1, 2, 3, 4), (0, 1), (False, True)):
        machine = InitializationMachine(image)
        machine.status_deferral = deferred
        machine.uc.mem_write(0x3FC90648, struct.pack("<I", 2))
        machine.uc.mem_write(0x3FC90644, b"\1")
        baseline = machine.protected()
        machine.run(0x42043FD8, selector, value)
        assert machine.protected() == baseline
        assert machine.read(APP_BLE, 1) == bytes((value if selector == 0 else 0,))
        assert machine.read(APP_WIFI, 1) == bytes((value if selector == 1 else 1,))
        cases.append({"case": "actual_application_wireless_writer", "selector": selector,
            "value": value, "synthetic_defer_status": deferred,
            "application_ble_flag": machine.read(APP_BLE, 1)[0],
            "application_wifi_flag": machine.read(APP_WIFI, 1)[0],
            "status_emissions": machine.status_emissions})
    for connected in (0, 1):
        machine = InitializationMachine(image)
        baseline = machine.protected()
        machine.uc.mem_write(0x3FC90404, bytes((connected,)))
        machine.run(0x42048CB8, stop_at=0x42048CC6)
        assert machine.read(APP_BLE, 1) == bytes((connected,))
        assert machine.protected() == baseline
        cases.append({"case": "actual_startup_ble_connection_flag", "sdk_connected_flag": connected,
            "application_ble_flag": machine.read(APP_BLE, 1)[0],
            "status_emissions": machine.status_emissions})
    for mode, notify, update_gate in itertools.product((0, 1), (0, 1), (False, True)):
        machine = InitializationMachine(image)
        baseline = machine.protected()
        machine.uc.mem_write(0x3FC90676, bytes((mode, notify)))
        machine.synthetic_update_gate = update_gate
        machine.run(0x42043864)
        assert machine.protected() == baseline
        assert machine.read(APP_BLE, 2) == b"\0\1"
        assert not machine.effects and not machine.status_emissions
        expected = ([0x42043782] if mode else
            [0x4202A230]+([] if update_gate else [0x4201FD58])+([0x42043782] if notify else []))
        assert [int(row["boundary"], 16) for row in machine.update_check_boundaries] == expected
        cases.append({"case": "actual_registered_native_update_check_callback",
            "synthetic_mode_flag": mode, "synthetic_notify_flag": notify,
            "provider_29f60_synthetic_result": update_gate, "application_ble_flag_unchanged": True,
            "substituted_lifecycle_boundaries": machine.update_check_boundaries,
            "protected_identity_regions_unchanged": True})
    for identity, busy, failure in itertools.product((PREVIOUS, LOCAL), (False, True), (False, True)):
        machine = ActivationMachine(image, provisioning_busy=busy, allocation_failure=failure)
        before_ble = machine.read(BLE_ACCOUNTS, 0x310)
        before_cache = machine.string(ACCOUNT_MQTT)
        machine.activate(identity)
        assert machine.read(BLE_ACCOUNTS, 0x310) == before_ble and not machine.ble_writes
        assert machine.string(ACCOUNT_MQTT) == before_cache
        accepted = not busy and not failure
        assert machine.string(ACCOUNT_CONFIG).decode() == (identity if accepted else PREVIOUS)
        assert bool(machine.lifecycle) == accepted
        assert len(machine.flash_writes) == int(accepted and identity != PREVIOUS)
        assert machine.u32(0x3FC8B51C) == (5 if accepted else 0)
        cases.append({"case": "normal_activation_application_body", "identity_shape": "retained" if identity == PREVIOUS else "generated",
            "provisioning_busy": busy, "allocation_failure": failure,
            "application_callback_returned": True, "configuration_update_path_reached": accepted,
            "native_config_account_changed": accepted and identity != PREVIOUS,
            "volatile_activation_state": machine.u32(0x3FC8B51C),
            "configuration_flash_writes": len(machine.flash_writes),
            "ble_allowlist_unchanged": True, "mqtt_identity_cache_unchanged": True,
            "substituted_lifecycle_boundaries": machine.lifecycle})
    return {"model": "A1763 C1000 Gen 2", "main_version": "1.1.4.9", "radio_version": "0.3.3.0",
        "image_sha256": IMAGE_SHA256, "direct_branch_census": census,
        "case_count": len(cases), "cases": cases,
        "limits": ["Actual selected radio instructions; synthetic radio and identity state.",
            "NimBLE/OS and activation lifecycle/network/config boundaries are substituted.",
            "Completing the application activation body does not execute its async lifecycle effects.",
            "No physical advertising, cloud binding, hardware output or rollback proof.",
            "Original C1000 and C2000 radio image equivalence remains unproven."]}


def main():
    if not __debug__:
        raise SystemExit("Run without -O: this replay requires assertions.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    image = args.image.read_bytes()
    assert args.image.name == IMAGE_NAME and hashlib.sha256(image).hexdigest() == IMAGE_SHA256
    results = suite(image)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "radio-ble-activation-results.json"
    output.write_text(json.dumps(results, indent=2)+"\n")
    manifest = {"source": Path(__file__).name, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dependency_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("emulate_radio_ble_advertising.py", "emulate_radio_ble_identity_separation.py",
                "emulate_radio_identity_storage.py", "emulate_radio_local_identity.py",
                "emulate_radio_rssi_routes.py", "emulate_radio_factory_routes.py")},
        "firmware_sha256": IMAGE_SHA256, "results_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "case_count": results["case_count"], "python": platform.python_version(),
        "unicorn": unicorn.__version__, "cryptography": cryptography.__version__,
        "capstone": capstone.__version__}
    (args.output_dir / "radio-ble-activation-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({"case_count": results["case_count"], "results_sha256": manifest["results_sha256"]}))


if __name__ == "__main__":
    main()
