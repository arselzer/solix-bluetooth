#!/usr/bin/env python3
"""Offline A1763 radio network-field producers and MQTT notification routing.

Actual native 0003 framing, network getters, selected WLAN/IP event branches,
IPv4 cache formatting argument setup, MQTT dispatchers and application callback
bodies execute. Physical Wi-Fi, MQTT connection/transport, selected lifecycle
operations, C formatting and MCU status transmission are explicit substitutes.
The MQTT success tail has a synthetic caller register frame; WLAN disconnect
stops before config retrieval/reconnect policy. No hardware or network access.
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
import cryptography
import unicorn
from unicorn import riscv_const as R

from emulate_radio_ble_activation import InitializationMachine
from emulate_radio_ble_advertising import APP_BLE, APP_WIFI
from emulate_radio_factory_routes import IMAGE_NAME, IMAGE_SHA256

os.umask(0o077)
APP_ETH = 0x3FC90645
GOT_IP = 0x3FC904C5
IP_TEXT = 0x3FC8AD98
MQTT_CONNECTED = 0x3FC9036C
SDK_NOTIFY = 0x3FC90430
APP_NOTIFY = 0x3FC90438
WLAN_CALLBACKS = 0x3FC8ADA8
IP_WORDS = 0x3FC904CC
SYNTHETIC_IP = bytes((192, 0, 2, 37))


def direct_calls(image):
    decoder = capstone.Cs(capstone.CS_ARCH_RISCV,
        capstone.CS_MODE_RISCV32 | capstone.CS_MODE_RISCVC)
    decoder.detail = True
    decoder.skipdata = True
    expected = {0x4202D20C: [0x42019B1E, 0x42019E74, 0x4201A3C4, 0x4201A522, 0x4201A65E],
        0x4202D1EC: [0x4201984E, 0x4201A08A, 0x4201A2A8],
        0x42031410: [0x420264F4, 0x4202D0DE]}
    found = {target: [] for target in expected}
    offset = 24
    for _ in range(image[1]):
        address, size = struct.unpack_from("<II", image, offset)
        offset += 8
        segment = image[offset:offset+size]
        offset += size
        if address != 0x42000020:
            continue
        for ins in decoder.disasm(segment, address):
            if ins.mnemonic not in ("jal", "j", "c.j", "c.jal"):
                continue
            target = (ins.address+ins.operands[-1].imm) & 0xFFFFFFFF
            if target in found:
                found[target].append(ins.address)
    assert found == expected
    return {f"{target:08x}": [f"{value:08x}" for value in values]
        for target, values in found.items()}


class Machine(InitializationMachine):
    def __init__(self, image, *, mqtt_connected=0, got_ip=0, app_wifi=0):
        self.boundaries = []
        self.notifications = []
        self.format_arguments = []
        super().__init__(image)
        self.uc.mem_write(SDK_NOTIFY, bytes(8))
        # Actual application startup installs the normal callbacks at APP_NOTIFY.
        self.run(0x42048608, stop_at=0x42048626)
        assert [self.u32(APP_NOTIFY+i*4) for i in (0, 1)] == [0x420440F2, 0x4204429C]
        # Reconstructed from the actual SDK's constant default WLAN table;
        # physical esp_event registration and driver startup are not executed.
        table = self.read(0x3C13FBBC, 28)
        assert struct.unpack("<7I", table) == (0x4202D0D4, 0x4202CFB2, 0x4202D066,
            0x4202CFE8, 0x4202CFD2, 0x4202CFCE, 0x4202CFCA)
        self.uc.mem_write(WLAN_CALLBACKS, table)
        self.uc.mem_write(MQTT_CONNECTED, bytes((mqtt_connected,)))
        self.uc.mem_write(GOT_IP, bytes((got_ip,)))
        self.uc.mem_write(APP_WIFI, bytes((app_wifi,)))
        self.uc.mem_write(0x3FC9066C, bytes(4))  # Normal application path, factory bypass absent.
        self.uc.mem_write(0x3FC8AB70, b"\0")  # No synthetic reconnect/binding branch.
        self.uc.mem_write(IP_TEXT, b"192.0.2.37\0".ljust(16, b"\0"))
        self.uc.mem_write(IP_WORDS, bytes((192, 0, 2, 1, 255, 255, 255, 0))+SYNTHETIC_IP)

    def write_guard(self, uc, access, address, size, value, data):
        if any(lo <= address < address+size <= hi for lo, hi in (
                (SDK_NOTIFY, APP_NOTIFY+8), (IP_TEXT, IP_TEXT+16),
                (0x3FC904C5, 0x3FC904DA), (0x3FC9042C, 0x3FC9042D),
                (0x3FC90340, 0x3FC90341), (0x3FC90354, 0x3FC9035C),
                (MQTT_CONNECTED, MQTT_CONNECTED+1), (0x3FC90634, 0x3FC90639))):
            return
        return super().write_guard(uc, access, address, size, value, data)

    def step(self, uc, address, size, data):
        if address == self.stop_at:
            uc.emu_stop()
            return
        args = [uc.reg_read(R.UC_RISCV_REG_A0+i) for i in range(8)]
        a0, a1, a2 = args[:3]
        if address in (0x420440F2, 0x4204429C):
            self.notifications.append({"callback": f"{address:08x}",
                "mqtt_connected_at_entry": self.read(MQTT_CONNECTED, 1)[0],
                "got_ip_at_entry": self.read(GOT_IP, 1)[0]})
            return
        if address == 0x42115088:
            assert a0 == IP_TEXT and self.string(a1) == b"%d.%d.%d.%d"
            octets = args[2:6]
            assert all(0 <= value <= 255 for value in octets)
            text = ".".join(str(value) for value in octets).encode()
            assert len(text)+1 <= 16
            self.format_arguments.append(octets)
            uc.mem_write(a0, text+b"\0")
            result = len(text)  # Host substitute for C sprintf only.
        elif address in (0x40000908, 0x4000090C):
            self.notifications.append({"callback": "synthetic_primary_connect" if address == 0x40000908
                else "synthetic_primary_disconnect", "argument0": a0})
            result = 0
        elif address in (0x4203AFC0, 0x42012EBC, 0x42026E66, 0x42042C82,
                0x4202DF9C, 0x4202C24E, 0x4202013E, 0x42020DEE,
                0x420188B4, 0x4201890C, 0x420180D4, 0x420EB504, 0x42033DA0):
            self.boundaries.append({"entry": f"{address:08x}", "argument0": a0})
            result = 0  # Explicit lifecycle/physical/MCU/OS-delay boundaries.
        elif address == 0x4202D3D0:
            result = 0  # Synthetic IP notification does not request a MQTT reset.
        elif any(lo <= address < hi for lo, hi in (
                (0x42048608, 0x42048626), (0x4202D224, 0x4202D24A),
                (0x4202BD9A, 0x4202BD9E), (0x42030E8E, 0x42030E98),
                (0x420313A2, 0x420313C2), (0x42031406, 0x42031466),
                (0x42031E74, 0x42032342), (0x4202D0D4, 0x4202D224),
                (0x4202CFE8, 0x4202D066), (0x4201874C, 0x42018758),
                (0x420440F4, 0x42044344), (0x42043184, 0x42043196),
                (0x4202BEBA, 0x4202BED4), (0x42019AD2, 0x42019B22),
                (0x42019800, 0x420198B4), (0x420197F2, 0x42019800))):
            return
        else:
            return super().step(uc, address, size, data)
        uc.reg_write(R.UC_RISCV_REG_A0, result & 0xFFFFFFFF)
        uc.reg_write(R.UC_RISCV_REG_PC, uc.reg_read(R.UC_RISCV_REG_RA))

    def select_notifications(self, mode):
        assert mode in ("fallback", "primary", "none")
        if mode == "primary":
            self.uc.mem_write(SDK_NOTIFY, struct.pack("<II", 0x40000908, 0x4000090C))
        elif mode == "none":
            self.uc.mem_write(APP_NOTIFY, bytes(8))

    def query(self):
        self.reply_frames.clear()
        self.publications.clear()
        self.send_native(3)
        body = bytes.fromhex(self.response(3)["response_body_hex"])
        assert body[0] == 0
        fields = {}
        offset = 1
        while offset < len(body):
            tag, length = body[offset:offset+2]
            offset += 2
            value = body[offset:offset+length]
            assert len(value) == length and tag not in fields
            fields[tag] = value
            offset += length
        return {"a1_ble": fields[0xA1][0], "a2_wifi": fields[0xA2][0],
            "a5_eth": fields[0xA5][0], "a6_got_ip_boolean": fields[0xA6][0],
            "a8_cached_ipv4": fields.get(0xA8, b"").decode()}

    def event(self, kind, data, *, stop_at=None):
        base = self.u32(0x3C15C020 if kind == "got_ip" else 0x3C15C720)
        assert self.string(base) == (b"IP_EVENT" if kind == "got_ip" else b"WIFI_EVENT")
        event_id = {"got_ip": 0, "station_stop": 3, "station_disconnect": 5}[kind]
        options = {} if stop_at is None else {"stop_at": stop_at}
        self.run(0x42031E74, 0, base, event_id, self.alloc(data), **options)
        if stop_at is not None:
            self.uc.reg_write(R.UC_RISCV_REG_SP, 0x3FD0F000)


def suite(image):
    census = direct_calls(image)
    cases = []
    for value in (0, 1, 255):
        machine = Machine(image)
        before = machine.protected()
        machine.run(0x42043FD8, 2, value)
        fields = machine.query()
        assert fields["a5_eth"] == value and not machine.status_emissions
        assert machine.protected() == before and not machine.boundaries
        cases.append({"case": "application_eth_selector", "synthetic_value": value,
            "fields": fields, "no_status_emission": True})
    for raw in (0, 1, 255):
        machine = Machine(image, got_ip=raw)
        fields = machine.query()
        assert fields["a6_got_ip_boolean"] == int(bool(raw))
        cases.append({"case": "actual_got_ip_getter_boolean", "raw_byte": raw, "fields": fields})
    for octets in (SYNTHETIC_IP, bytes(4)):
        machine = Machine(image)
        machine.uc.mem_write(IP_WORDS+8, octets)
        outputs = [machine.alloc(4) for _ in range(3)]
        machine.run(0x42031410, *outputs)
        assert machine.read(outputs[0], 4) == octets
        assert machine.read(outputs[1], 4) == bytes((255, 255, 255, 0))
        assert machine.read(outputs[2], 4) == bytes((192, 0, 2, 1))
        assert machine.format_arguments == [list(octets)]
        fields = machine.query()
        assert fields["a8_cached_ipv4"] == ".".join(str(value) for value in octets)
        cases.append({"case": "actual_cached_ipv4_producer", "synthetic_ip_octets": list(octets),
            "format_arguments": machine.format_arguments, "fields": fields})
    for mqtt, octets in itertools.product((0, 1), (SYNTHETIC_IP, bytes(4))):
        machine = Machine(image, mqtt_connected=mqtt)
        before = machine.protected()
        data = bytes(4)+octets+bytes((255, 255, 255, 0, 192, 0, 2, 1))+bytes(4)
        machine.event("got_ip", data)
        fields = machine.query()
        assert fields["a6_got_ip_boolean"] == 1 and fields["a2_wifi"] == mqtt
        assert fields["a8_cached_ipv4"] == ".".join(str(value) for value in octets)
        assert machine.protected() == before and not machine.effects
        cases.append({"case": "actual_station_got_ip_event", "synthetic_mqtt_connected": mqtt,
            "synthetic_ip_octets": list(octets), "fields": fields,
            "boundaries": machine.boundaries, "notifications": machine.notifications,
            "protected_identity_regions_unchanged": True})
    for kind in ("station_stop", "station_disconnect"):
        machine = Machine(image, mqtt_connected=1, got_ip=1, app_wifi=1)
        before = machine.protected()
        machine.event(kind, bytes(0x27)+b"\x07",
            stop_at=0x42031F80 if kind == "station_disconnect" else None)
        fields = machine.query()
        assert fields["a6_got_ip_boolean"] == 0
        assert fields["a8_cached_ipv4"] == ("192.0.2.37" if kind == "station_stop" else "")
        assert fields["a2_wifi"] == int(kind == "station_stop")
        assert machine.protected() == before and not machine.effects
        cases.append({"case": "actual_wlan_loss_event", "event": kind, "fields": fields,
            "disconnect_prefix_stop_before_reconnect": kind == "station_disconnect",
            "notifications": machine.notifications, "protected_identity_regions_unchanged": True})
    machine = Machine(image, mqtt_connected=1, got_ip=1, app_wifi=1)
    machine.run(0x420313A2)
    fields = machine.query()
    assert fields["a6_got_ip_boolean"] == 0 and fields["a8_cached_ipv4"] == "192.0.2.37"
    assert fields["a2_wifi"] == 1
    cases.append({"case": "disconnect_helper_before_async_event", "fields": fields,
        "boundaries": machine.boundaries, "cached_ip_not_cleared_by_helper": True})
    for connected, mode in itertools.product((False, True), ("fallback", "primary", "none")):
        machine = Machine(image, mqtt_connected=1, got_ip=1, app_wifi=int(not connected))
        machine.select_notifications(mode)
        before = machine.protected()
        machine.run(0x4202D20C if connected else 0x4202D1EC)
        fields = machine.query()
        expected = int(connected) if mode == "fallback" else int(not connected)
        assert fields["a2_wifi"] == expected and fields["a6_got_ip_boolean"] == 1
        assert len(machine.notifications) == int(mode != "none")
        if mode == "primary" and not connected:
            assert machine.notifications[0]["argument0"] == 65535
        assert machine.protected() == before and not machine.effects
        cases.append({"case": "actual_mqtt_notification_dispatcher", "event": "connect" if connected else "disconnect",
            "callbacks": mode, "fields": fields, "notifications": machine.notifications,
            "boundaries": machine.boundaries, "protected_identity_regions_unchanged": True})
    for mqtt, got_ip in itertools.product((0, 1), (0, 1)):
        machine = Machine(image, mqtt_connected=mqtt, got_ip=got_ip)
        before = machine.protected()
        machine.run(0x420440F2)
        fields = machine.query()
        assert fields["a2_wifi"] == int(mqtt == got_ip == 1)
        assert machine.protected() == before and not machine.effects
        cases.append({"case": "actual_normal_connect_callback_gates", "mqtt_connected": mqtt,
            "got_ip": got_ip, "fields": fields, "boundaries": machine.boundaries})
    for got_ip, mode in itertools.product((0, 1), ("fallback", "primary")):
        machine = Machine(image, got_ip=got_ip)
        machine.select_notifications(mode)
        before = machine.protected()
        # Synthetic caller frame at the exact post-success branch, before flag assignment.
        for reg in (R.UC_RISCV_REG_S1, R.UC_RISCV_REG_S2, R.UC_RISCV_REG_S3, R.UC_RISCV_REG_A5):
            machine.uc.reg_write(reg, 0x3FC90000)
        machine.run(0x42019AD2, stop_at=0x42019B22)
        machine.uc.reg_write(R.UC_RISCV_REG_SP, 0x3FD0F000)
        fields = machine.query()
        assert machine.read(MQTT_CONNECTED, 1) == b"\1"
        assert fields["a2_wifi"] == int(got_ip == 1 and mode == "fallback")
        assert machine.protected() == before and not machine.effects
        cases.append({"case": "actual_mqtt_daemon_success_tail", "synthetic_got_ip": got_ip,
            "callbacks": mode, "synthetic_caller_register_frame": True, "fields": fields,
            "notifications": machine.notifications, "boundaries": machine.boundaries,
            "protected_identity_regions_unchanged": True})
    for got_ip in (0, 1):
        machine = Machine(image, mqtt_connected=1, got_ip=got_ip, app_wifi=1)
        before = machine.protected()
        machine.run(0x42019800, machine.alloc(4))
        fields = machine.query()
        assert machine.read(MQTT_CONNECTED, 1) == b"\0" and fields["a2_wifi"] == 0
        assert fields["a6_got_ip_boolean"] == got_ip
        assert fields["a8_cached_ipv4"] == "192.0.2.37"
        assert machine.notifications[0]["mqtt_connected_at_entry"] == 1
        assert machine.protected() == before and not machine.effects
        cases.append({"case": "actual_mqtt_disconnect_routine", "synthetic_got_ip": got_ip,
            "fields": fields, "notifications": machine.notifications, "boundaries": machine.boundaries,
            "mqtt_flag_cleared_after_application_notification": True,
            "protected_identity_regions_unchanged": True})
    return {"model": "A1763 C1000 Gen 2", "main_version": "1.1.4.9", "radio_version": "0.3.3.0",
        "firmware_sha256": IMAGE_SHA256, "direct_call_census": census,
        "case_count": len(cases), "cases": cases,
        "limits": ["Exact selected radio instructions with synthetic event/provider/caller state only.",
            "No Wi-Fi/MQTT transport, cloud/binding success, physical controller or hardware recovery proof.",
            "Primary notification callbacks are synthetic; full activation/network functions are substituted.",
            "WLAN disconnect stops before config retrieval and reconnect policy; session buffers are absent.",
            "IPv4 strings have no demonstrated TTL, validation or freshness guarantee.",
            "Direct-call census is not a whole-program or indirect-producer proof.",
            "Original C1000 and C2000 radio binary equivalence remains unproven."]}


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
    output = args.output_dir / "radio-network-producers-results.json"
    output.write_text(json.dumps(results, indent=2)+"\n")
    dependencies = ("emulate_radio_ble_activation.py", "emulate_radio_ble_advertising.py",
        "emulate_radio_ble_identity_separation.py", "emulate_radio_identity_storage.py",
        "emulate_radio_local_identity.py", "emulate_radio_rssi_routes.py", "emulate_radio_factory_routes.py")
    manifest = {"source": Path(__file__).name,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dependency_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in dependencies}, "firmware_sha256": IMAGE_SHA256,
        "results_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "case_count": results["case_count"], "python": platform.python_version(),
        "unicorn": unicorn.__version__, "cryptography": cryptography.__version__,
        "capstone": capstone.__version__}
    (args.output_dir / "radio-network-producers-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({"case_count": results["case_count"], "results_sha256": manifest["results_sha256"]}))


if __name__ == "__main__":
    main()
