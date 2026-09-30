"""C1000 setting writes use synthetic packets and fake transports only."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest

from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.ap_service_monitor import APServiceMonitor
from solix_link.commands import validate_command
from solix_link.mqtt_intercept import LocalMqttServer
from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model, decode_telemetry, parse_packet, parse_tlvs, tlv


SETTINGS = [
    ("temperature_unit", "set_temperature_unit", "temperature_unit_fahrenheit", 0xA5),
    ("off_grid_alert", "set_off_grid_alert", "ac_off_grid_alert_enabled", 0xB0),
]


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
            if not self.ignore:
                if 0xA5 in fields:
                    self.a4[20] = fields[0xA5][1]
                elif 0xA6 in fields:
                    self.a4[14:16] = fields[0xA6][1:3]
                else:
                    self.a4[32] = (self.a4[32] & ~2) | (fields[0xB0][1] << 1)
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


@pytest.mark.parametrize("builder,method,metric,tag", SETTINGS)
@pytest.mark.parametrize("value", [False, True])
def test_builder_has_only_target_setting_and_typed_timestamp(builder, method, metric, tag, value):
    commands = NativeMqttCommands("SYNTHETIC", "a" * 40, model=Model.C1000_GEN2)
    request = getattr(commands, builder)(value)
    command, fields = unpack(request)
    assert command == "0103" and request.response_command == "0903"
    assert set(fields) == {0xA1, tag, 0xFD}
    assert fields[0xA1] == b"\x22" and fields[tag] == bytes((1, value))
    assert fields[0xFD][:1] == b"\x00" and fields[0xFD][1:].isdigit()


@pytest.mark.parametrize("builder,method,metric,tag", SETTINGS)
@pytest.mark.parametrize("value", [0, 1, None, "true", [], {}])
def test_non_boolean_fails_before_any_io(tmp_path, builder, method, metric, tag, value):
    server, station = service(tmp_path)
    with pytest.raises(ValueError, match="boolean"):
        getattr(server.commands, builder)(value)
    with pytest.raises(ValueError, match="boolean"):
        asyncio.run(getattr(server, method)(value))
    assert station.requests == []


@pytest.mark.parametrize("builder,method,metric,tag", SETTINGS)
def test_c2000_and_disabled_controls_fail_before_any_io(tmp_path, builder, method, metric, tag):
    server, station = service(tmp_path, model=Model.C2000_GEN2)
    with pytest.raises(ValueError, match="C1000 Gen 2 only"):
        asyncio.run(getattr(server, method)(True))
    assert station.requests == []
    server, station = service(tmp_path, allow_control=False)
    with pytest.raises(PermissionError):
        asyncio.run(getattr(server, method)(True))
    assert station.requests == []


@pytest.mark.parametrize("builder,method,metric,tag", SETTINGS)
def test_confirmed_round_trip_preserves_other_settings_and_never(tmp_path, builder, method, metric, tag):
    async def run():
        server, station = service(tmp_path)
        original_a4, original_d9 = bytes(station.a4), bytes(station.d9)
        original = bool(station.a4[20]) if tag == 0xA5 else bool(station.a4[32] & 2)
        changed = await getattr(server, method)(not original)
        assert changed["settings_confirmed"] and changed["metrics"][metric] == int(not original)
        assert changed["metrics"]["device_timeout_minutes"] == 0
        restored = await getattr(server, method)(original)
        assert restored["metrics"][metric] == int(original)
        assert bytes(station.a4) == original_a4 and bytes(station.d9) == original_d9
        assert [command for command, _ in station.requests] == ["0100", "0103", "0100"] * 2
    asyncio.run(run())


@pytest.mark.parametrize("builder,method,metric,tag", SETTINGS)
def test_acknowledged_but_ignored_write_is_not_success(tmp_path, builder, method, metric, tag):
    server, station = service(tmp_path)
    station.ignore = True
    requested = tag == 0xA5
    with pytest.raises(RuntimeError, match="not confirmed"):
        asyncio.run(getattr(server, method)(requested))
    assert [command for command, _ in station.requests] == ["0100", "0103", "0100"]


@pytest.mark.parametrize("problem", ["a4_absent", "a4_short", "a4_type", "d9_short", "ac_absent",
                                    "dc_absent", "mains_invalid", "temperature_invalid", "ac_type", "dc_type"])
def test_incomplete_or_invalid_baseline_sends_no_write(tmp_path, problem):
    server, station = service(tmp_path)
    if problem.endswith("absent"):
        station.missing.add({"a4_absent": 0xA4, "ac_absent": 0xA7, "dc_absent": 0xB2}[problem])
    elif problem == "a4_short":
        station.a4 = station.a4[:-1]
    elif problem == "a4_type":
        station.a4[0] = 3
    elif problem == "d9_short":
        station.d9 = station.d9[:-1]
    elif problem == "mains_invalid":
        station.a7[4] = 2
    elif problem in ("ac_type", "dc_type"):
        getattr(station, "a7" if problem == "ac_type" else "b2")[0] = 3
    else:
        station.a4[20] = 2
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(server.set_temperature_unit(True))
    assert [command for command, _ in station.requests] == ["0100"]


@pytest.mark.parametrize("block,offset", [
    ("a4", 5), ("a4", 7), ("a4", 14), ("a4", 16), ("a4", 18), ("a4", 21),
    ("a4", 22), ("a4", 23), ("a4", 32), ("a4", 33), ("d9", 2), ("d9", 3),
    ("d9", 4), ("d9", 5), ("d9", 15), ("a7", 1), ("a7", 4), ("b2", 1),
])
def test_protected_mutation_fails_without_retry(tmp_path, block, offset):
    server, station = service(tmp_path)
    def mutate():
        data = getattr(station, block)
        data[offset] ^= 2 if (block, offset) == ("a4", 32) else 1
    station.mutate = mutate
    with pytest.raises(RuntimeError, match="Protected setting"):
        asyncio.run(server.set_temperature_unit(True))
    assert [command for command, _ in station.requests] == ["0100", "0103", "0100"]


def test_alert_preserves_temperature_and_other_alert_byte_bits(tmp_path):
    for offset, mask in ((20, 1), (32, 0x80)):
        server, station = service(tmp_path)
        station.mutate = lambda: station.a4.__setitem__(offset, station.a4[offset] ^ mask)
        with pytest.raises(RuntimeError, match="Protected setting"):
            asyncio.run(server.set_off_grid_alert(False))
        assert sum(command == "0103" for command, _ in station.requests) == 1


def test_tariff_boundary_and_natural_countdown_are_allowed_but_timer_increase_is_not(tmp_path):
    server, station = service(tmp_path)
    station.d9 = bytearray([4, 1, 1, 10, 100, 1, 2, 1, 0, 12, 3, 12, 24]) + bytearray(19)
    station.a4[1:5] = (10).to_bytes(4, "little")
    station.a4[9:13] = (10).to_bytes(4, "little")
    def natural_change():
        station.d9[1] = 3
        station.a4[1:5] = (9).to_bytes(4, "little")
        station.a4[9:13] = (8).to_bytes(4, "little")
    station.mutate = natural_change
    result = asyncio.run(server.set_temperature_unit(True))
    assert result["settings_confirmed"] and result["metrics"]["active_tariff"] == "off_peak"
    server, station = service(tmp_path)
    station.mutate = lambda: station.a4.__setitem__(9, 1)
    with pytest.raises(RuntimeError, match="Output timer changed"):
        asyncio.run(server.set_temperature_unit(True))


def test_schedule_mutation_and_lost_ack_are_not_retried(tmp_path):
    server, station = service(tmp_path)
    station.d9 = bytearray([4, 0, 0, 10, 100, 1, 1, 1, 0, 24]) + bytearray(19)
    station.mutate = lambda: station.d9.__setitem__(7, 3)
    with pytest.raises(RuntimeError, match="Protected setting"):
        asyncio.run(server.set_temperature_unit(True))
    assert sum(command == "0103" for command, _ in station.requests) == 1
    server, station = service(tmp_path)
    station.fail_write = True
    with pytest.raises(TimeoutError):
        asyncio.run(server.set_temperature_unit(True))
    assert [command for command, _ in station.requests] == ["0100", "0103"]
    assert station.a4[20] == 1  # Failure does not imply the setting stayed unchanged.


def test_alert_decode_requires_c1000_and_type_and_enough_data():
    for flags in (0, 1, 2, 3, 0xFE, 0xFF):
        a4 = bytearray(34)
        a4[0], a4[32] = 4, flags
        metrics, _ = decode_telemetry(tlv(0xA4, a4), Model.C1000_GEN2)
        assert metrics["ac_off_grid_alert_enabled"] == (flags >> 1) & 1
        assert "ac_off_grid_alert_enabled" not in decode_telemetry(tlv(0xA4, a4), Model.C2000_GEN2)[0]
    for data in (b"", b"\x04" + bytes(31), b"\x03" + bytes(33)):
        assert "ac_off_grid_alert_enabled" not in decode_telemetry(tlv(0xA4, data), Model.C1000_GEN2)[0]


@pytest.mark.parametrize("command,key", [("set-temperature-unit", "fahrenheit"), ("set-off-grid-alert", "enabled")])
def test_gateway_and_unix_commands_require_exact_boolean_shape(tmp_path, command, key):
    from solix_link.ap_service import APService
    async def run(fields):
        server, station = service(tmp_path)
        (tmp_path / "mqtt-response.json").write_text("{}")
        worker = APService(server.config, tmp_path, allow_control=True)
        worker.mqtt = server
        worker.stations[server.config.name] = server
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps({"command": command, **fields}).encode() + b"\n")
        reader.feed_eof()
        class Writer:
            def __init__(self):
                self.data = b""
            def write(self, value):
                self.data += value
            async def drain(self):
                pass
            def close(self):
                pass
        writer = Writer()
        await worker._control(reader, writer)
        return json.loads(writer.data), station.requests
    for fields in ({}, {key: 1}, {key: "true"}, {key: True, "extra": 1}):
        with pytest.raises(ValueError):
            validate_command(command, fields)
        response, requests = asyncio.run(run(fields))
        assert response == {"ok": False, "error": "ValueError"} and requests == []
    validate_command(command, {key: False})
    response, requests = asyncio.run(run({key: False}))
    assert response["ok"] and response["result"]["settings_confirmed"]
    assert [name for name, _ in requests] == ["0100", "0103", "0100"]


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_capabilities_are_c1000_only_and_require_control_enablement(tmp_path, model):
    server, _ = service(tmp_path, model=model)
    monitor = APServiceMonitor(server.config, tmp_path)
    for enabled in (False, True):
        private_write(tmp_path / "status.json", json.dumps({"control_enabled": enabled}))
        commands = monitor.supported_commands("test")
        assert ("set-temperature-unit" in commands) == (enabled and model == Model.C1000_GEN2)
        assert ("set-off-grid-alert" in commands) == (enabled and model == Model.C1000_GEN2)


@pytest.mark.parametrize("argv,command,fields", [
    (["ap-service-set-temperature-unit", "--unit", "fahrenheit"], "set-temperature-unit", {"fahrenheit": True}),
    (["ap-service-set-temperature-unit", "--unit", "celsius"], "set-temperature-unit", {"fahrenheit": False}),
    (["ap-service-set-off-grid-alert", "--state", "on"], "set-off-grid-alert", {"enabled": True}),
    (["ap-service-set-off-grid-alert", "--state", "off"], "set-off-grid-alert", {"enabled": False}),
])
def test_cli_uses_typed_private_socket_requests(tmp_path, monkeypatch, argv, command, fields):
    from solix_link import ap_service_cli, cli
    calls = []
    async def request(directory, action, **values):
        calls.append((directory, action, values))
        return {}
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    assert cli.main(argv + ["--directory", str(tmp_path)]) == 0
    assert calls == [(tmp_path, command, fields)]
