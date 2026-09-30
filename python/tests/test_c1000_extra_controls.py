"""Original-model controls and Gen 2 native fast charge use fake transports."""
import asyncio

import pytest

from solix_link.c1000 import c1000_setting
from solix_link.client import SolixMonitor
from solix_link.config import DeviceConfig
from solix_link.manager import MonitorService
from solix_link.mqtt_bridge import _supports_operation, decode_setting
from solix_link.protocol import DATA_RESPONSE, Model, Session, build_packet, decode_telemetry, parse_packet, parse_tlvs, tlv
from test_native_boolean_settings import service, unpack


CONTROLS = [
    ("temperature_unit_fahrenheit", "set_temperature_unit", "4050", "temperature_unit_fahrenheit", 0xDD),
    ("fast_charge_enabled", "set_fast_charge_enabled", "405e", "ac_fast_charge_enabled", 0xE5),
    ("ac_power_saving_mode_enabled", "set_ac_power_saving_enabled", "4077", "ac_power_saving_mode_enabled", 0xF8),
    ("dc_power_saving_mode_enabled", "set_dc_power_saving_enabled", "4076", "dc_power_saving_mode_enabled", 0xF8),
]


@pytest.mark.parametrize("setting,method,command,metric,tag", CONTROLS)
@pytest.mark.parametrize("value", [False, True])
def test_legacy_boolean_wire_shape_and_live_smart_modes(setting, method, command, metric, tag, value):
    session = Session(Model.C1000)
    session.ready, session._secret = True, bytes(range(32))
    packet = parse_packet(session.c1000_control_packet(setting, value))
    fields = parse_tlvs(session._crypt(packet.payload, False))
    assert packet.command.hex() == command
    assert set(fields) == {0xA1, 0xA2, 0xFE} and fields[0xA1] == b"\x21"
    assert fields[0xA2] == bytes((1, value))
    assert c1000_setting(setting, value)[2] == {metric: int(value)}


@pytest.mark.parametrize("setting,method,command,metric,tag", CONTROLS)
@pytest.mark.parametrize("value", [0, 1, "on", None])
def test_boolean_controls_reject_integer_and_other_shapes(setting, method, command, metric, tag, value):
    with pytest.raises(ValueError):
        c1000_setting(setting, value)


@pytest.mark.parametrize("dc,ac", [(1, 1), (1, 2), (2, 1), (2, 2)])
@pytest.mark.parametrize("expanded", [False, True])
def test_smart_status_is_packed_and_uses_one_normal_two_smart(dc, ac, expanded):
    modes = bytes((4 if expanded else 1, dc, ac)) + (bytes(range(18)) if expanded else b"")
    metrics, raw = decode_telemetry(tlv(0xDD, b"\x01\x01") + tlv(0xF8, modes), Model.C1000)
    assert metrics["temperature_unit_fahrenheit"] == 1
    assert metrics["dc_power_saving_mode_enabled"] == int(dc == 2)
    assert metrics["ac_power_saving_mode_enabled"] == int(ac == 2)
    assert raw[0xF8] == modes


@pytest.mark.parametrize("data", [b"", b"\x01\x02", b"\x04\x02\x02", b"\x01\x00\x03", b"\x01\x02\x02\x00",
                                  b"\x04\x02\x02" + bytes(17), b"\x04\x02\x02" + bytes(19),
                                  b"\x01\x02\x02" + bytes(18), b"\x04\x00\x03" + bytes(18)])
def test_malformed_smart_status_is_omitted(data):
    metrics, _ = decode_telemetry(tlv(0xF8, data), Model.C1000)
    assert "ac_power_saving_mode_enabled" not in metrics
    assert "dc_power_saving_mode_enabled" not in metrics


@pytest.mark.parametrize("problem", ["matching", "missing_baseline", "stale_after", "output_changed", "ignored"])
def test_original_full_fresh_baseline_and_protected_confirmation(monkeypatch, problem):
    async def run():
        monitor = SolixMonitor("AA:BB:CC:DD:EE:01", model=Model.C1000)
        monitor._session.ready, monitor._session._secret = True, bytes(range(32))
        monitor._ready.set()
        writes = []
        fahrenheit = 0
        class Client:
            is_connected = True
            async def write_gatt_char(self, _uuid, packet, **_kwargs):
                nonlocal fahrenheit
                command = parse_packet(packet).command.hex()
                writes.append(command)
                if command == "4050":
                    if problem != "ignored":
                        fahrenheit = 1
                    monitor._responses.put_nowait(("4850", b"\x00"))
                elif command == "4040":
                    if problem == "missing_baseline" or (problem == "stale_after" and "4050" in writes):
                        payload = tlv(0xC1, b"\x01\x64")
                    else:
                        ac = 0 if problem == "output_changed" and "4050" in writes else 1
                        payload = b"".join(tlv(tag, bytes.fromhex(value)) for tag, value in (
                            (0xD1, "02e803"), (0xD2, "02d002"), (0xD3, "021e00"),
                            (0xD9, "0103"), (0xD7, f"01{ac:02x}"), (0xD8, "0100"),
                            (0xDC, "0100"), (0xDD, f"01{fahrenheit:02x}"), (0xE5, "0100"), (0xF8, "010202")))
                    await monitor._handle(build_packet(DATA_RESPONSE, bytes.fromhex("4840"), monitor._session._crypt(payload, True)))
        async def no_delay(_seconds):
            pass
        async def update(timeout=None):
            if monitor._updates.empty():
                raise TimeoutError
            return monitor._updates.get_nowait()
        monkeypatch.setattr("solix_link.client.asyncio.sleep", no_delay)
        monitor.wait_for_update, monitor._client = update, Client()
        if problem == "matching":
            result = await monitor.set_temperature_unit(True)
            assert result["temperature_unit_fahrenheit"] == 1 and result["ac_output_enabled"] == 1
        else:
            with pytest.raises(TimeoutError):
                await monitor.set_temperature_unit(True)
        assert writes.count("4050") == (0 if problem == "missing_baseline" else 1)
    asyncio.run(run())


@pytest.mark.parametrize("operation,key", [("temperature_unit", "fahrenheit"), ("fast_charge", "enabled"),
                                          ("ac_power_saving", "enabled"), ("dc_power_saving", "enabled")])
def test_bridge_and_service_model_specific_boolean_capabilities(operation, key):
    original = DeviceConfig("original", "AA:BB:CC:DD:EE:01", Model.C1000)
    assert _supports_operation(original, operation)
    command = "set-" + operation.replace("_", "-")
    assert command in MonitorService([original]).supported_commands("original")
    assert decode_setting(operation, ('{"' + key + '":true}').encode()) == {key: True}
    with pytest.raises(ValueError):
        decode_setting(operation, ('{"' + key + '":1}').encode())
    for model in (Model.C300, Model.C2000_GEN2):
        device = DeviceConfig("other", "AA:BB:CC:DD:EE:02", model, "a" * 40 if model == Model.C2000_GEN2 else None)
        assert not _supports_operation(device, operation)
        assert command not in MonitorService([device]).supported_commands("other")


def fast_station(tmp_path):
    server, station = service(tmp_path)
    original_request = station.request
    station.fast_writes = 0
    station.auto_clear = False
    station.fast_statuses = 0
    async def request(packet):
        command, fields = unpack(packet)
        if command == "0101":
            station.requests.append((command, fields))
            station.fast_writes += 1
            assert set(fields) == {0xA1, 0xA7, 0xFD} and fields[0xA7][0] == 1
            if not station.ignore:
                station.a4[21] = fields[0xA7][1]
            station.mutate()
            if station.fail_write:
                raise TimeoutError
            return b"\x00"
        if station.fast_writes:
            station.fast_statuses += 1
            if station.auto_clear and station.fast_statuses > 1:
                station.a4[21] = 0
        return await original_request(packet)
    station.request = request
    return server, station


def test_native_fast_charge_retained_round_trip_and_no_other_changes(tmp_path, monkeypatch):
    async def no_delay(_seconds):
        pass
    monkeypatch.setattr("solix_link.mqtt_intercept.asyncio.sleep", no_delay)
    async def run():
        server, station = fast_station(tmp_path)
        before = bytes(station.a4), bytes(station.d9), bytes(station.a7)
        changed = await server.set_fast_charge_enabled(True)
        assert changed["settings_confirmed"] and changed["metrics"]["ac_fast_charge_enabled"] == 1
        restored = await server.set_fast_charge_enabled(False)
        assert restored["metrics"]["ac_fast_charge_enabled"] == 0
        assert (bytes(station.a4), bytes(station.d9), bytes(station.a7)) == before
        assert [command for command, _ in station.requests] == ["0100", "0101", "0100", "0100"] * 2
    asyncio.run(run())


def test_fast_charge_display_wake_is_runtime_but_brightness_timeout_and_memory_are_protected(tmp_path, monkeypatch):
    async def no_delay(_seconds):
        pass
    monkeypatch.setattr("solix_link.mqtt_intercept.asyncio.sleep", no_delay)
    server, station = fast_station(tmp_path)
    station.a4[22] = 0
    station.mutate = lambda: station.a4.__setitem__(22, 1)
    result = asyncio.run(server.set_fast_charge_enabled(True))
    assert result["settings_confirmed"] and result["metrics"]["display_enabled"] == 1
    for offset in (16, 18, 23):
        server, station = fast_station(tmp_path)
        station.mutate = lambda offset=offset: station.a4.__setitem__(offset, station.a4[offset] ^ 1)
        with pytest.raises(RuntimeError, match="Protected setting"):
            asyncio.run(server.set_fast_charge_enabled(True))
        assert station.fast_writes == 1


@pytest.mark.parametrize("problem", ["ignored", "auto_clear", "protected", "lost_ack", "fast_invalid", "mains_absent", "tou"])
def test_native_fast_charge_ignoring_clearing_or_unsafe_baseline_is_not_success(tmp_path, monkeypatch, problem):
    async def no_delay(_seconds):
        pass
    monkeypatch.setattr("solix_link.mqtt_intercept.asyncio.sleep", no_delay)
    server, station = fast_station(tmp_path)
    if problem == "ignored":
        station.ignore = True
    elif problem == "auto_clear":
        station.auto_clear = True
    elif problem == "protected":
        station.mutate = lambda: station.a7.__setitem__(1, 0)
    elif problem == "lost_ack":
        station.fail_write = True
    elif problem == "fast_invalid":
        station.a4[21] = 2
    elif problem == "mains_absent":
        station.a7[4] = 0
    else:
        station.d9[1], station.d9[2] = 1, 1
    with pytest.raises((ValueError, RuntimeError, TimeoutError)):
        asyncio.run(server.set_fast_charge_enabled(True))
    assert station.fast_writes == (0 if problem in ("fast_invalid", "mains_absent", "tou") else 1)


@pytest.mark.parametrize("value", [1, 0, None, "on"])
def test_native_fast_charge_bad_type_before_io(tmp_path, value):
    server, station = fast_station(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(server.set_fast_charge_enabled(value))
    assert station.requests == []


@pytest.mark.parametrize("model,clean_session,accepted", [
    (Model.C1000_GEN2, True, True), (Model.C1000_GEN2, False, False),
    (Model.C2000_GEN2, True, False),
])
def test_observed_zero_client_id_quirk_is_model_and_clean_session_specific(tmp_path, model, clean_session, accepted):
    from types import SimpleNamespace
    from solix_link.mqtt_intercept import _Connection, mqtt_packet
    async def run():
        server, _ = service(tmp_path, model=model)
        server.connection = None
        reader = asyncio.StreamReader()
        body = b"\x00\x04MQTT\x04" + bytes((2 if clean_session else 0,)) + b"\x00\x28\x00\x11" + bytes(17)
        reader.feed_data(mqtt_packet(0x10, body) + mqtt_packet(0xE0, b""))
        reader.feed_eof()
        sent = []
        async def drain():
            pass
        writer = SimpleNamespace(is_closing=lambda: False, write=sent.append, drain=drain)
        connection = _Connection(server, reader, writer)
        if accepted:
            await connection.run()
            assert sent == [b"\x20\x02\x00\x00"]
            assert connection.connected and not connection.subscribed
            assert server.connection is None  # CONNECT alone never enables commands.
        else:
            with pytest.raises(ValueError):
                await connection.run()
            assert not sent and server.connection is None
    asyncio.run(run())


@pytest.mark.parametrize("raw", [bytes(16), bytes(18), b"a" + bytes(16), bytes(16) + b"a", b"abc\x00def"])
def test_zero_id_compatibility_does_not_accept_other_nul_strings(raw):
    from solix_link.mqtt_intercept import mqtt_string
    with pytest.raises(ValueError):
        mqtt_string(len(raw).to_bytes(2, "big") + raw, 0, allow_zero_client_id=True)
