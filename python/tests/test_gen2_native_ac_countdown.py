"""Private operator countdown: synthetic framing, gates and uncertain results."""
import asyncio

import pytest

from solix_link.commands import native_commands_for_model, validate_command
from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model
from test_gen2_native_dc_smart import DcStation
from test_native_boolean_settings import service, unpack


class CountdownStation(DcStation):
    advance = 0
    dc_advance = 0

    async def request(self, request):
        command, fields = unpack(request)
        if command == "0101":
            self.requests.append((command, fields))
            if not self.ignore:
                self.a4[1:5] = fields[0xA3][1:5]
            self.mutate()
            if self.fail_write:
                raise TimeoutError("Synthetic lost ACK")
            return b"\x00"
        if command == "0100":
            for offset, step in ((1, self.advance), (9, self.dc_advance)):
                remaining = int.from_bytes(self.a4[offset:offset + 4], "little")
                self.a4[offset:offset + 4] = max(0, remaining - step).to_bytes(4, "little")
        return await super().request(request)


def setup(tmp_path, **options):
    server, _ = service(tmp_path, **options)
    server.connection = station = CountdownStation(server)
    return server, station


@pytest.mark.parametrize("seconds", [0, 600, 3600, 47100, 86400])
def test_exact_countdown_packet_omits_output_and_charging_fields(seconds):
    request = NativeMqttCommands("SYNTHETIC", "a" * 40, Model.C1000_GEN2).ac_countdown(seconds)
    command, fields = unpack(request)
    assert command == "0101" and request.response_command == "0901"
    assert set(fields) == {0xA1, 0xA3, 0xFD}
    assert fields[0xA1] == b"\x22"
    assert fields[0xA3] == b"\x03" + seconds.to_bytes(4, "little")


@pytest.mark.parametrize("seconds", [True, False, None, "600", 600.0, -1, 1, 599, 86401])
def test_invalid_duration_before_io(tmp_path, seconds):
    server, station = setup(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(server.set_ac_countdown(seconds))
    assert not station.requests


@pytest.mark.parametrize("model", [Model.C2000_GEN2, Model.C1000, Model.C300])
def test_no_other_model_or_generic_http_capability(tmp_path, model):
    with pytest.raises(ValueError):
        NativeMqttCommands("SYNTHETIC", "a" * 40, model).ac_countdown(600)
    assert "set-ac-countdown" not in native_commands_for_model(model)
    with pytest.raises(ValueError):
        validate_command("set-ac-countdown", {"seconds": 600})


def test_readonly_and_c2000_worker_before_io(tmp_path):
    server, station = setup(tmp_path, allow_control=False)
    with pytest.raises(PermissionError):
        asyncio.run(server.set_ac_countdown(600))
    assert not station.requests
    server, station = setup(tmp_path, model=Model.C2000_GEN2)
    with pytest.raises(ValueError):
        asyncio.run(server.set_ac_countdown(0))
    assert not station.requests


@pytest.mark.parametrize("problem", ["ac_off", "ac_timer", "dc_timer", "smart", "version", "tariff"])
def test_new_timer_requires_safe_baseline_before_write(tmp_path, problem):
    server, station = setup(tmp_path)
    if problem == "ac_off": station.a7[1] = 0
    elif problem == "ac_timer": station.a4[1] = 1
    elif problem == "dc_timer": station.a4[9] = 1
    elif problem == "smart": station.a4[8] = 1
    elif problem == "tariff": station.d9[1] = 1
    else: station.version = b"\x04" + bytes((10, 4, 1, 1)) + bytes(24)
    with pytest.raises(ValueError, match="no write sent"):
        asyncio.run(server.set_ac_countdown(600))
    assert [c for c, _ in station.requests] == ["0100"]


def test_countdown_advances_and_zero_restores_configuration(tmp_path):
    async def run():
        server, station = setup(tmp_path)
        before = bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)
        station.advance = 2
        changed = await server.set_ac_countdown(600)
        assert changed["settings_confirmed"]
        assert changed["metrics"]["ac_output_timeout_seconds"] == 596
        assert changed["metrics"]["ac_output_enabled"] == 1
        restored = await server.set_ac_countdown(0)
        assert restored["metrics"]["ac_output_timeout_seconds"] == 0
        assert (bytes(station.a4), bytes(station.d9), bytes(station.a7), bytes(station.b2)) == before
        assert [c for c, _ in station.requests] == ["0100", "0101", "0100", "0100"] * 2
    asyncio.run(run())


def test_cancel_allows_existing_timer_smart_tariff_and_output_off(tmp_path):
    server, station = setup(tmp_path)
    station.a4[1:5] = (30).to_bytes(4, "little")
    station.a4[8] = 1
    station.d9[1] = 1
    station.a7[1] = 0
    assert asyncio.run(server.set_ac_countdown(0))["metrics"]["ac_output_timeout_seconds"] == 0
    assert station.a7[1] == 0 and station.a4[8] == 1 and station.d9[1] == 1


def test_cancel_preserves_another_decreasing_countdown(tmp_path):
    server, station = setup(tmp_path)
    station.a4[1:5] = (30).to_bytes(4, "little")
    station.a4[9:13] = (60).to_bytes(4, "little")
    station.dc_advance = 2
    result = asyncio.run(server.set_ac_countdown(0))
    assert result["metrics"]["ac_output_timeout_seconds"] == 0
    assert result["metrics"]["dc_output_timeout_seconds"] == 54


@pytest.mark.parametrize("block,offset", [("a4", 5), ("a4", 8), ("a4", 23), ("a4", 9),
                                         ("d9", 3), ("a7", 1), ("b2", 1)])
def test_protected_mutation_is_failure_not_retry(tmp_path, block, offset):
    server, station = setup(tmp_path)
    def mutate(): getattr(station, block)[offset] ^= 1
    station.mutate = mutate
    with pytest.raises(RuntimeError):
        asyncio.run(server.set_ac_countdown(600))
    assert [c for c, _ in station.requests] == ["0100", "0101", "0100"]


@pytest.mark.parametrize("problem", ["ignore", "lost_ack", "too_fast", "increase"])
def test_uncertain_write_does_not_retry_or_cancel_automatically(tmp_path, problem):
    server, station = setup(tmp_path)
    if problem == "ignore": station.ignore = True
    elif problem == "lost_ack": station.fail_write = True
    elif problem == "too_fast": station.advance = 40
    else:
        def mutate(): station.a4[1:5] = (601).to_bytes(4, "little")
        station.mutate = mutate
    with pytest.raises((RuntimeError, TimeoutError)):
        asyncio.run(server.set_ac_countdown(600))
    assert sum(c == "0101" for c, _ in station.requests) == 1
