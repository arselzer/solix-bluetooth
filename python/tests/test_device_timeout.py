"""Timeout commands must preserve outputs and reject stale confirmations."""

import asyncio

import pytest

from solix_link.client import SolixMonitor
from solix_link.commands import validate_command
from solix_link.config import DeviceConfig
from solix_link.manager import MonitorService
from solix_link.mqtt_bridge import _supports_operation, decode_setting
from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import DATA_RESPONSE, DEVICE_TIMEOUT_MINUTES, Model, Session, build_packet, parse_packet, parse_tlvs, tlv
from test_native_boolean_settings import service, unpack


@pytest.mark.parametrize("minutes", DEVICE_TIMEOUT_MINUTES)
@pytest.mark.parametrize("model", [Model.C1000, Model.C1000_GEN2])
def test_ble_timeout_packet_changes_only_timeout(model, minutes):
    session = Session(model)
    session.ready = True
    session._secret = bytes(range(32))
    packet = parse_packet(session.device_timeout_packet(minutes))
    fields = parse_tlvs(session._crypt(packet.payload, False))
    target = 0xA2 if model == Model.C1000 else 0xA6
    assert packet.command.hex() == ("4045" if model == Model.C1000 else "4103")
    assert fields[target] == b"\x02" + minutes.to_bytes(2, "little")
    assert set(fields) == {0xA1, target, 0xFE if model == Model.C1000 else 0xFD}
    assert fields[0xA1] == b"\x21"


@pytest.mark.parametrize("minutes", [False, True, -1, 1, 31, 65535, "0", None])
def test_invalid_values_rejected_before_transport(tmp_path, minutes):
    server, station = service(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(server.set_device_timeout(minutes))
    with pytest.raises(ValueError):
        validate_command("set-device-timeout", {"minutes": minutes})
    assert station.requests == []


def test_native_packet_and_confirmed_round_trip_preserve_settings(tmp_path):
    request = NativeMqttCommands("SYNTHETIC", "a" * 40, model=Model.C1000_GEN2).device_timeout(720)
    command, fields = unpack(request)
    assert command == "0103" and set(fields) == {0xA1, 0xA6, 0xFD}
    assert fields[0xA6] == b"\x02\xd0\x02"
    async def run():
        server, station = service(tmp_path)
        original = bytes(station.a4), bytes(station.d9), bytes(station.a7)
        result = await server.set_device_timeout(720)
        assert result["settings_confirmed"] and result["metrics"]["device_timeout_minutes"] == 720
        result = await server.set_device_timeout(0)
        assert result["metrics"]["device_timeout_minutes"] == 0
        assert (bytes(station.a4), bytes(station.d9), bytes(station.a7)) == original
    asyncio.run(run())


@pytest.mark.parametrize("problem", ["ignored", "ac_changed", "schedule_changed", "lost_ack", "missing_baseline"])
def test_failed_confirmation_is_not_retried(tmp_path, problem):
    server, station = service(tmp_path)
    if problem == "ignored":
        station.ignore = True
    elif problem == "ac_changed":
        station.mutate = lambda: station.a7.__setitem__(1, 0)
    elif problem == "schedule_changed":
        station.mutate = lambda: station.d9.__setitem__(3, 15)
    elif problem == "lost_ack":
        station.fail_write = True
    else:
        station.missing.add(0xA4)
    with pytest.raises((RuntimeError, TimeoutError)):
        asyncio.run(server.set_device_timeout(720))
    assert sum(command == "0103" for command, _ in station.requests) == (0 if problem == "missing_baseline" else 1)


@pytest.mark.parametrize("model", [Model.C300, Model.C2000_GEN2])
def test_unsupported_models_expose_no_timeout_control(model, tmp_path):
    device = DeviceConfig("station", "AA:BB:CC:DD:EE:01", model,
                          "a" * 40 if model == Model.C2000_GEN2 else None)
    assert "set-device-timeout" not in MonitorService([device]).supported_commands("station")
    assert not _supports_operation(device, "device_timeout")
    session = Session(model)
    session.ready = True
    with pytest.raises(RuntimeError):
        session.device_timeout_packet(0)
    if model == Model.C2000_GEN2:
        server, station = service(tmp_path, model=model)
        with pytest.raises(ValueError):
            asyncio.run(server.set_device_timeout(0))
        assert station.requests == []


@pytest.mark.parametrize("fresh", ["missing_baseline", "stale_after_write", "matching", "output_changed"])
def test_ble_requires_fresh_baseline_and_fresh_protected_readback(monkeypatch, fresh):
    async def run():
        monitor = SolixMonitor("AA:BB:CC:DD:EE:01", model=Model.C1000)
        monitor._session.ready = True
        monitor._session._secret = bytes(range(32))
        monitor._ready.set()
        monitor.metrics = {"device_timeout_minutes": 0, "ac_output_enabled": 1,
                           "dc_output_enabled": 0, "ac_charging_power_limit_w": 1000}
        writes = []
        class Client:
            is_connected = True
            async def write_gatt_char(self, _uuid, packet, **_kwargs):
                command = parse_packet(packet).command.hex()
                writes.append(command)
                if command == "4045":
                    monitor._responses.put_nowait(("4845", b"\x00"))
                elif command == "4040":
                    if fresh == "missing_baseline" or ("4045" in writes and fresh == "stale_after_write"):
                        payload = tlv(0xC1, b"\x01\x64")
                    else:
                        ac = 0 if fresh == "output_changed" and "4045" in writes else 1
                        payload = (tlv(0xD2, b"\x02\x00\x00") + tlv(0xD7, bytes((1, ac)))
                                   + tlv(0xD8, b"\x01\x00") + tlv(0xD1, b"\x02\xe8\x03"))
                    await monitor._handle(build_packet(DATA_RESPONSE, bytes.fromhex("4840"),
                                                      monitor._session._crypt(payload, True)))
        async def no_delay(_seconds):
            pass
        async def update(timeout=None):
            if monitor._updates.empty():
                raise TimeoutError
            return monitor._updates.get_nowait()
        monkeypatch.setattr("solix_link.client.asyncio.sleep", no_delay)
        monitor.wait_for_update = update
        monitor._client = Client()
        if fresh == "matching":
            assert (await monitor.set_device_timeout(0))["ac_output_enabled"] == 1
        else:
            with pytest.raises(TimeoutError):
                await monitor.set_device_timeout(0)
        assert writes.count("4045") == (0 if fresh == "missing_baseline" else 1)
    asyncio.run(run())


def test_bridge_payload_and_cli_keep_integer_zero(tmp_path, monkeypatch):
    from solix_link import ap_service_cli, cli
    assert decode_setting("device_timeout", b'{"minutes":0}') == {"minutes": 0}
    with pytest.raises(ValueError):
        decode_setting("device_timeout", b'{"minutes":false}')
    calls = []
    async def request(directory, action, **values):
        calls.append((directory, action, values))
        return {}
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    assert cli.main(["ap-service-set-device-timeout", "--directory", str(tmp_path), "--minutes", "0"]) == 0
    assert calls == [(tmp_path, "set-device-timeout", {"minutes": 0})]


@pytest.mark.parametrize("problem", ["none", "missing_d9", "short_a4", "missing_after", "mode", "schedule",
                                    "unknown_a4", "timer_increase", "output", "tariff_boundary", "countdown", "ac_type"])
def test_gen2_ble_preserves_complete_fresh_configuration(tmp_path, monkeypatch, problem):
    async def run():
        _server, station = service(tmp_path)
        monitor = SolixMonitor("AA:BB:CC:DD:EE:01", model=Model.C1000_GEN2)
        monitor._session.ready = True
        monitor._session._secret = bytes(range(32))
        monitor._ready.set()
        station.d9 = bytearray([4, 0, 0, 10, 100, 1, 1, 3, 0, 24]) + bytearray(19)
        if problem == "short_a4":
            station.a4 = station.a4[:-1]
        if problem == "ac_type":
            station.a7[0] = 3
        if problem == "countdown":
            station.a4[1:5] = (10).to_bytes(4, "little")
            station.a4[9:13] = (10).to_bytes(4, "little")
        writes = []
        class Client:
            is_connected = True
            async def write_gatt_char(self, _uuid, packet, **_kwargs):
                command = parse_packet(packet).command.hex()
                writes.append(command)
                if command == "4103":
                    station.a4[14:16] = (720).to_bytes(2, "little")
                    if problem == "mode":
                        station.d9[2] = 1
                    elif problem == "schedule":
                        station.d9[7] = 1  # Same count, mode and limits; changed tariff slot.
                    elif problem == "unknown_a4":
                        station.a4[33] ^= 1
                    elif problem == "timer_increase":
                        station.a4[1] = 10
                    elif problem == "output":
                        station.a7[1] = 0
                    elif problem == "tariff_boundary":
                        station.d9[1] = 1
                    elif problem == "countdown":
                        station.a4[1:5] = (9).to_bytes(4, "little")
                        station.a4[9:13] = (8).to_bytes(4, "little")
                    monitor._responses.put_nowait(("4903", b"\x00"))
                elif command == "4100":
                    payload = b"".join(tlv(tag, block) for tag, block in (
                        (0xA4, station.a4), (0xD9, station.d9), (0xA7, station.a7), (0xB2, station.b2))
                        if not (tag == 0xD9 and (problem == "missing_d9"
                                                or (problem == "missing_after" and "4103" in writes))))
                    await monitor._handle(build_packet(DATA_RESPONSE, bytes.fromhex("4900"),
                                                      monitor._session._crypt(payload, True)))
        async def no_delay(_seconds):
            pass
        async def update(timeout=None):
            if monitor._updates.empty():
                raise TimeoutError
            return monitor._updates.get_nowait()
        monkeypatch.setattr("solix_link.client.asyncio.sleep", no_delay)
        monitor._client = Client()
        monitor.wait_for_update = update
        if problem in ("none", "tariff_boundary", "countdown"):
            result = await monitor.set_device_timeout(720)
            assert result["device_timeout_minutes"] == 720 and result["ac_output_enabled"] == 1
        else:
            with pytest.raises((RuntimeError, ValueError, TimeoutError)):
                await monitor.set_device_timeout(720)
        assert writes.count("4103") == (0 if problem in ("missing_d9", "short_a4", "ac_type") else 1)
    asyncio.run(run())
