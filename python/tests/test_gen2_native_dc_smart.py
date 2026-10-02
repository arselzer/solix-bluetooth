"""Gen 2 DC Smart safety and confirmation with synthetic station replies."""
import asyncio

import pytest

from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model, decode_telemetry, tlv
from test_native_boolean_settings import Station, service, unpack


class DcStation(Station):
    version = b"\x04" + bytes((9, 4, 1, 1)) + bytes(24)

    async def request(self, request):
        command, fields = unpack(request)
        if command == "0102":
            self.requests.append((command, fields))
            if not self.ignore:
                self.a4[13] = fields[0xA4][1]
            self.mutate()
            if self.fail_write:
                raise TimeoutError("Synthetic lost acknowledgement")
            return b"\x00"
        reply = await super().request(request)
        reply += tlv(0xF9, self.version)
        self.server.metrics, _ = decode_telemetry(reply[1:], self.server.config.model)
        return reply


def setup(tmp_path, **options):
    server, _ = service(tmp_path, **options)
    station = DcStation(server)
    server.connection = station
    return server, station


@pytest.mark.parametrize("enabled", [False, True])
def test_wire_has_only_dc_smart_and_source_timestamp(enabled):
    request = NativeMqttCommands("SYNTHETIC", "a" * 40, model=Model.C1000_GEN2).dc_power_saving(enabled)
    command, fields = unpack(request)
    assert command == "0102" and request.response_command == "0902"
    assert set(fields) == {0xA1, 0xA4, 0xFD}
    assert fields[0xA4] == bytes((1, enabled))


@pytest.mark.parametrize("value", [0, 1, "on", None, [], {}])
def test_boolean_required_before_io(tmp_path, value):
    server, station = setup(tmp_path)
    with pytest.raises(ValueError, match="boolean"):
        asyncio.run(server.set_dc_power_saving_enabled(value))
    assert not station.requests


def test_c2000_and_readonly_cannot_write(tmp_path):
    server, station = setup(tmp_path, model=Model.C2000_GEN2)
    with pytest.raises(ValueError):
        asyncio.run(server.set_dc_power_saving_enabled(True))
    assert not station.requests
    server, station = setup(tmp_path, allow_control=False)
    with pytest.raises(PermissionError):
        asyncio.run(server.set_dc_power_saving_enabled(True))
    assert not station.requests


@pytest.mark.parametrize("problem", ["dc_on", "ac_timer", "dc_timer", "unknown_smart", "version", "no_version"])
def test_unsafe_baseline_sends_only_status(tmp_path, problem):
    server, station = setup(tmp_path)
    if problem == "dc_on":
        station.b2[1] = 1
    elif problem in ("ac_timer", "dc_timer"):
        station.a4[1 if problem == "ac_timer" else 9] = 1
    elif problem == "unknown_smart":
        station.a4[13] = 2
    elif problem == "version":
        station.version = b"\x04" + bytes((10, 4, 1, 1)) + bytes(24)
    else:
        station.version = b""
    with pytest.raises(ValueError, match="no write sent"):
        asyncio.run(server.set_dc_power_saving_enabled(True))
    assert [command for command, _ in station.requests] == ["0100"]


def test_roundtrip_protects_complete_configuration_and_ac(tmp_path):
    async def run():
        server, station = setup(tmp_path)
        a4, d9 = bytes(station.a4), bytes(station.d9)
        changed = await server.set_dc_power_saving_enabled(True)
        assert changed["settings_confirmed"]
        assert changed["metrics"]["dc_power_saving_mode_enabled"] == 1
        assert changed["metrics"]["ac_output_enabled"] == 1
        assert changed["metrics"]["dc_output_enabled"] == 0
        restored = await server.set_dc_power_saving_enabled(False)
        assert restored["metrics"]["dc_power_saving_mode_enabled"] == 0
        assert bytes(station.a4) == a4 and bytes(station.d9) == d9
        assert [c for c, _ in station.requests] == ["0100", "0102", "0100", "0100"] * 2
    asyncio.run(run())


@pytest.mark.parametrize("block,offset", [("a4", 5), ("a4", 7), ("a4", 14), ("a4", 23),
                                         ("d9", 2), ("d9", 3), ("d9", 15), ("a7", 1), ("b2", 1)])
def test_ack_with_protected_change_is_failure_without_retry(tmp_path, block, offset):
    server, station = setup(tmp_path)
    def mutate():
        getattr(station, block)[offset] ^= 1
    station.mutate = mutate
    with pytest.raises(RuntimeError, match="Protected setting"):
        asyncio.run(server.set_dc_power_saving_enabled(True))
    assert [c for c, _ in station.requests] == ["0100", "0102", "0100"]


def test_ignored_write_and_lost_ack_are_not_success_or_retried(tmp_path):
    server, station = setup(tmp_path)
    station.ignore = True
    with pytest.raises(RuntimeError, match="not confirmed"):
        asyncio.run(server.set_dc_power_saving_enabled(True))
    assert [c for c, _ in station.requests] == ["0100", "0102", "0100"]
    server, station = setup(tmp_path)
    station.fail_write = True
    with pytest.raises(TimeoutError):
        asyncio.run(server.set_dc_power_saving_enabled(True))
    assert [c for c, _ in station.requests] == ["0100", "0102"]
