"""Native lower-limit confirmation uses synthetic C1000 packets only."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest

from solix_link.ap_service_config import APServiceConfig
from solix_link.commands import NATIVE_C1000_COMMANDS, validate_command
from solix_link.mqtt_intercept import LocalMqttServer
from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model, decode_telemetry, parse_packet, parse_tlvs, tlv


def unpack(request):
    inner = json.loads(json.loads(request.payload)["payload"])
    packet = parse_packet(base64.b64decode(inner["data"]))
    return packet.command.hex(), parse_tlvs(packet.payload)


class Station:
    def __init__(self, server):
        self.server = server
        self.writer = SimpleNamespace(is_closing=lambda: False)
        self.a4 = bytearray(34)
        self.a4[0] = 4
        self.a4[24], self.a4[25] = 100, 1
        self.a4[5:7] = (1200).to_bytes(2, "little")
        self.a4[7], self.a4[16], self.a4[18] = 50, 30, 3
        self.a4[22], self.a4[23], self.a4[32] = 1, 1, 2
        self.d9 = bytearray([4, 0, 0, 10, 100, 1, 0]) + bytearray(19)
        self.a7 = bytearray([4, 1, 45, 0, 1, 45, 0])
        self.b2 = bytearray([4, 0, 0, 0])
        self.missing = set()
        self.requests = []
        self.ignore = False
        self.fail_write = False
        self.mutate = lambda: None

    async def request(self, request):
        command, fields = unpack(request)
        self.requests.append((command, fields))
        if command == "0103":
            assert set(fields) == {0xA1, 0xAB, 0xFD}
            if not self.ignore:
                self.d9[5] = fields[0xAB][1]
                self.a4[25] = fields[0xAB][1]
                # Reproduce the firmware side effect so an unsafe implementation
                # cannot pass by relying on an unrealistically passive fixture.
                self.d9[3] = max(self.d9[3], self.d9[5] + 5)
            self.mutate()
            if self.fail_write:
                raise TimeoutError("Synthetic lost acknowledgement")
            return b"\x00"
        assert command == "0100"
        data = b"".join(tlv(tag, block) for tag, block in (
            (0xA4, self.a4), (0xD9, self.d9), (0xA7, self.a7), (0xB2, self.b2),
        ) if tag not in self.missing)
        self.server.metrics, _ = decode_telemetry(data, self.server.config.model)
        self.server.last_seen = time.time()
        return b"\x00" + data


def service(tmp_path, *, model=Model.C1000_GEN2, allow_control=True):
    config = APServiceConfig("test", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40,
                             model=model)
    server = LocalMqttServer(config, tmp_path, allow_control=allow_control)
    station = Station(server)
    server.connection = station
    return server, station


@pytest.mark.parametrize("lower", [1, 5, 10, 15, 20])
def test_builder_has_only_lower_field_and_validated_app_values(lower):
    commands = NativeMqttCommands("SYNTHETIC", "a" * 40, model=Model.C1000_GEN2)
    request = commands.discharge_floor(lower)
    command, fields = unpack(request)
    assert command == "0103" and request.response_command == "0903"
    assert fields[0xA1] == b"\x22" and fields[0xAB] == bytes((1, lower))
    assert set(fields) == {0xA1, 0xAB, 0xFD}
    assert fields[0xFD][:1] == b"\x00" and fields[0xFD][1:].isdigit()


@pytest.mark.parametrize("lower", [None, True, False, 5.0, "5", [], {}, 0, 2, 25, 95, -1, 255])
def test_invalid_type_or_domain_fails_before_io(tmp_path, lower):
    server, station = service(tmp_path)
    with pytest.raises(ValueError, match="Discharge floor"):
        asyncio.run(server.set_discharge_floor(lower))
    assert station.requests == []


def test_c2000_and_control_disabled_are_rejected_before_io(tmp_path):
    server, station = service(tmp_path, model=Model.C2000_GEN2)
    with pytest.raises(ValueError, match="C1000 Gen 2 only"):
        asyncio.run(server.set_discharge_floor(5))
    assert station.requests == []
    server, station = service(tmp_path, allow_control=False)
    with pytest.raises(PermissionError):
        asyncio.run(server.set_discharge_floor(5))
    assert station.requests == []


def test_round_trip_1_to_5_to_1_preserves_reserve_upper_and_never(tmp_path):
    async def run():
        server, station = service(tmp_path)
        before = bytes(station.a4), bytes(station.d9)
        for lower in (5, 1):
            result = await server.set_discharge_floor(lower)
            assert result["settings_confirmed"]
            assert result["metrics"]["min_charge_percentage"] == lower
            assert result["metrics"]["backup_reserve_percentage"] == 10
            assert result["metrics"]["max_charge_percentage"] == 100
            assert result["metrics"]["device_timeout_minutes"] == 0
        assert (bytes(station.a4), bytes(station.d9)) == before
        assert [command for command, _ in station.requests] == ["0100", "0103", "0100"] * 2
    asyncio.run(run())


@pytest.mark.parametrize("lower,reserve,upper", [(10, 10, 100), (1, 5, 100), (5, 85, 80), (20, 20, 100)])
def test_reserve_or_upper_conflict_never_sends_write(tmp_path, lower, reserve, upper):
    server, station = service(tmp_path)
    station.d9[3], station.d9[4] = reserve, upper
    station.a4[24] = upper
    with pytest.raises(ValueError, match="reserve|charge cap"):
        asyncio.run(server.set_discharge_floor(lower))
    assert [command for command, _ in station.requests] == ["0100"]


@pytest.mark.parametrize("lower,reserve", [(10, 15), (15, 20), (20, 25)])
def test_higher_valid_values_require_existing_reserve_headroom(tmp_path, lower, reserve):
    server, station = service(tmp_path)
    station.d9[3] = reserve
    result = asyncio.run(server.set_discharge_floor(lower))
    assert result["metrics"]["min_charge_percentage"] == lower
    assert result["metrics"]["backup_reserve_percentage"] == reserve


@pytest.mark.parametrize("problem", ["a4_absent", "a4_short", "a4_type", "d9_short", "d9_extra",
                                    "ac_absent", "dc_absent", "mains_invalid", "upper_invalid",
                                    "lower_invalid", "reserve_invalid", "schedule_invalid", "mirror_lower_conflict", "mirror_upper_conflict"])
def test_missing_or_invalid_fresh_baseline_sends_no_write(tmp_path, problem):
    server, station = service(tmp_path)
    server.metrics = {"min_charge_percentage": 1, "backup_reserve_percentage": 10,
                      "max_charge_percentage": 100, "ac_output_enabled": 1}
    if problem.endswith("absent"):
        station.missing.add({"a4_absent": 0xA4, "ac_absent": 0xA7, "dc_absent": 0xB2}[problem])
    elif problem == "a4_short":
        station.a4 = station.a4[:-1]
    elif problem == "a4_type":
        station.a4[0] = 3
    elif problem == "d9_short":
        station.d9 = station.d9[:-1]
    elif problem == "d9_extra":
        station.d9.append(0)
    elif problem == "mains_invalid":
        station.a7[4] = 2
    elif problem == "mirror_lower_conflict":
        station.a4[25] = 5
    elif problem == "mirror_upper_conflict":
        station.a4[24] = 90
    elif problem == "schedule_invalid":
        station.d9[6] = 1
    else:
        station.d9[{"upper_invalid": 4, "lower_invalid": 5, "reserve_invalid": 3}[problem]] = 2
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(server.set_discharge_floor(5))
    assert [command for command, _ in station.requests] == ["0100"]


def test_ack_without_readback_is_failure_and_is_not_retried(tmp_path):
    server, station = service(tmp_path)
    station.ignore = True
    with pytest.raises(RuntimeError, match="not confirmed"):
        asyncio.run(server.set_discharge_floor(5))
    assert [command for command, _ in station.requests] == ["0100", "0103", "0100"]


@pytest.mark.parametrize("block,offset", [
    ("a4", 5), ("a4", 7), ("a4", 8), ("a4", 13), ("a4", 14), ("a4", 16), ("a4", 18),
    ("a4", 24), ("a4", 25), ("a4", 20), ("a4", 21), ("a4", 22), ("a4", 23), ("a4", 32), ("a4", 33),
    ("d9", 2), ("d9", 3), ("d9", 4), ("d9", 15), ("a7", 1), ("a7", 4), ("b2", 1),
])
def test_unrelated_setting_or_output_change_fails_once(tmp_path, block, offset):
    server, station = service(tmp_path)
    def mutate():
        value = getattr(station, block)
        value[offset] ^= 1
    station.mutate = mutate
    with pytest.raises(RuntimeError, match="Protected setting"):
        asyncio.run(server.set_discharge_floor(5))
    assert [command for command, _ in station.requests] == ["0100", "0103", "0100"]


def test_tariff_boundary_and_countdown_decrease_preserve_config(tmp_path):
    server, station = service(tmp_path)
    station.d9 = bytearray([4, 1, 1, 10, 100, 1, 2, 1, 0, 12, 3, 12, 24]) + bytearray(19)
    station.a4[1:5] = (10).to_bytes(4, "little")
    station.a4[9:13] = (10).to_bytes(4, "little")
    def natural_change():
        station.d9[1] = 3
        station.a4[1:5] = (9).to_bytes(4, "little")
        station.a4[9:13] = (8).to_bytes(4, "little")
    station.mutate = natural_change
    result = asyncio.run(server.set_discharge_floor(5))
    assert result["settings_confirmed"] and result["metrics"]["active_tariff"] == "off_peak"


@pytest.mark.parametrize("start", [1, 9])
def test_timer_increase_is_failure(tmp_path, start):
    server, station = service(tmp_path)
    station.mutate = lambda: station.a4.__setitem__(start, 1)
    with pytest.raises(RuntimeError, match="Output timer changed"):
        asyncio.run(server.set_discharge_floor(5))
    assert sum(command == "0103" for command, _ in station.requests) == 1


def test_lost_ack_does_not_trigger_another_write_or_automatic_restore(tmp_path):
    server, station = service(tmp_path)
    station.fail_write = True
    with pytest.raises(TimeoutError):
        asyncio.run(server.set_discharge_floor(5))
    assert station.d9[5] == 5  # Failure is not proof the write had no effect.
    assert [command for command, _ in station.requests] == ["0100", "0103"]


def test_command_shape_is_strict_and_capability_is_c1000_only():
    assert "set-discharge-floor" in NATIVE_C1000_COMMANDS
    validate_command("set-discharge-floor", {"lower": 5})
    for values in ({}, {"lower": True}, {"lower": "5"}, {"upper": 5}, {"lower": 5, "upper": 100}):
        with pytest.raises(ValueError):
            validate_command("set-discharge-floor", values)
