"""Lower C1000 Gen 2 limits preserve configuration, unlike a stop command."""

import asyncio

import pytest

from solix_link.client import SolixMonitor
from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model, Session, parse_packet, parse_tlvs
from test_native_boolean_settings import service, unpack


@pytest.mark.parametrize('watts', [100, 200])
def test_c1000_low_power_builders_and_c2000_minimum_stay_separate(watts):
    session = Session(Model.C1000_GEN2)
    session.ready, session._secret = True, bytes(range(32))
    packet = parse_packet(session.ac_charging_power_packet(watts))
    fields = parse_tlvs(session._crypt(packet.payload, False))
    assert packet.command.hex() == '4101' and fields[0xA4] == b'\x02' + watts.to_bytes(2, 'little')
    assert set(fields) == {0xA1, 0xA4, 0xAB, 0xFD}
    request = NativeMqttCommands('SYNTHETIC', 'a' * 40, model=Model.C1000_GEN2).ac_charging_power(watts)
    command, fields = unpack(request)
    assert command == '0101' and set(fields) == {0xA1, 0xA4, 0xFD}
    c2000 = Session(Model.C2000_GEN2)
    c2000.ready = True
    with pytest.raises(ValueError):
        c2000.ac_charging_power_packet(watts)
    with pytest.raises(ValueError):
        NativeMqttCommands('SYNTHETIC', 'a' * 40, model=Model.C2000_GEN2).ac_charging_power(watts)


@pytest.mark.parametrize('problem', ['none', 'display_wake', 'brightness', 'timeout', 'memory',
                                    'output', 'schedule', 'ignored', 'missing_baseline', 'lost_ack'])
def test_native_power_requires_complete_fresh_protected_readback(tmp_path, problem):
    server, station = service(tmp_path)
    station.a4[22] = 0
    original_request = station.request
    writes = []
    async def request(packet):
        command, fields = unpack(packet)
        if command == '0101':
            writes.append(fields)
            assert set(fields) == {0xA1, 0xA4, 0xFD}
            if problem != 'ignored':
                station.a4[5:7] = fields[0xA4][1:3]
            offset = {'display_wake': 22, 'brightness': 18, 'timeout': 16, 'memory': 23}.get(problem)
            if offset is not None:
                station.a4[offset] ^= 1
            if problem == 'output': station.a7[1] = 0
            if problem == 'schedule': station.d9[3] = 15
            if problem == 'lost_ack': raise TimeoutError
            return b'\x00'
        return await original_request(packet)
    station.request = request
    if problem == 'missing_baseline': station.missing.add(0xD9)
    if problem in ('none', 'display_wake'):
        result = asyncio.run(server.set_ac_charging_power(100))
        assert result['settings_confirmed'] and result['metrics']['ac_charging_power_limit_w'] == 100
        assert result['metrics']['ac_output_enabled'] == 1
    else:
        with pytest.raises((ValueError, RuntimeError, TimeoutError)):
            asyncio.run(server.set_ac_charging_power(100))
    assert len(writes) == (0 if problem == 'missing_baseline' else 1)


@pytest.mark.parametrize('problem', ['none', 'display_wake', 'brightness', 'output', 'schedule', 'timer'])
def test_ble_power_protects_the_same_complete_configuration(tmp_path, monkeypatch, problem):
    async def run():
        _server, station = service(tmp_path)
        monitor = SolixMonitor('AA:BB:CC:DD:EE:01', model=Model.C1000_GEN2)
        monitor._session.ready, monitor._session._secret = True, bytes(range(32))
        monitor._ready.set()
        monitor._client = type('Client', (), {'is_connected': True})()
        # Use the real decoder for both distinct fresh snapshots.
        async def fresh():
            return await _server._fresh_c1000_settings(station)
        monitor._fresh_gen2_configuration = fresh
        async def write(packet, expected):
            station.a4[5:7] = (200).to_bytes(2, 'little')
            if problem == 'display_wake': station.a4[22] ^= 1
            if problem == 'brightness': station.a4[18] ^= 1
            if problem == 'output': station.a7[1] = 0
            if problem == 'schedule': station.d9[3] = 15
            if problem == 'timer': station.a4[1] = 10
            _, _, monitor.metrics = await fresh()
            return monitor.metrics.copy()
        monitor._write_setting = write
        if problem in ('none', 'display_wake'):
            assert (await monitor.set_ac_charging_power(200))['ac_charging_power_limit_w'] == 200
        else:
            with pytest.raises(RuntimeError): await monitor.set_ac_charging_power(200)
    asyncio.run(run())
