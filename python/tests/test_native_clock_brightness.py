"""Clock scalar writes protect complete fresh DA/A4/D9; no assets or theme write."""
import asyncio

import pytest

from solix_link.commands import native_commands_for_model, validate_command
from solix_link.native_mqtt import NativeMqttCommands
from solix_link.protocol import Model, decode_telemetry, tlv
from test_gen2_native_dc_smart import DcStation
from test_native_boolean_settings import service, unpack


class ClockStation(DcStation):
    def __init__(self, server):
        super().__init__(server)
        self.da = bytearray([4, 0, 0] + [0] * 21)
        self.omit_da = False

    async def request(self, request):
        command, fields = unpack(request)
        if command == "0091":
            self.requests.append((command, fields))
            if not self.ignore:
                self.da[18 if 0xAC in fields else 19] = fields.get(0xAC, fields.get(0xAD))[1]
            self.mutate()
            if self.fail_write:
                raise TimeoutError("Synthetic lost ACK")
            return b"\0"
        reply = await super().request(request)
        if not self.omit_da:
            reply += tlv(0xDA, self.da)
        self.server.metrics, _ = decode_telemetry(reply[1:], self.server.config.model)
        return reply


def setup(tmp_path, **options):
    server, _ = service(tmp_path, **options)
    server.connection = station = ClockStation(server)
    return server, station


@pytest.mark.parametrize("window", [1, 2])
@pytest.mark.parametrize("high", [False, True])
def test_exact_scalar_wire_and_complete_restoration(tmp_path, window, high):
    async def run():
        server, station = setup(tmp_path)
        before = bytes(station.da), bytes(station.a4), bytes(station.d9)
        result = await server.set_clock_brightness(window, high)
        assert result["settings_confirmed"]
        assert station.da[17 + window] == int(high)
        assert len([r for r in station.requests if r[0] == "0100"]) == 3
        command, fields = next(r for r in station.requests if r[0] == "0091")
        assert command == "0091"
        assert set(fields) == {0xA1, 0xFD, 0xAC if window == 1 else 0xAD}
        await server.set_clock_brightness(window, False)
        assert (bytes(station.da), bytes(station.a4), bytes(station.d9)) == before
    asyncio.run(run())


@pytest.mark.parametrize("window,high", [(0, True), (3, False), (True, False), (1, 1), (1, None)])
def test_invalid_shape_before_io(tmp_path, window, high):
    server, station = setup(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(server.set_clock_brightness(window, high))
    assert not station.requests


@pytest.mark.parametrize("issue", ["missing_da", "short_da", "wrong_type", "clock_on", "pending", "failed_transfer",
                                  "first_selector", "second_selector", "firmware", "ac_timer", "dc_timer", "tou"])
def test_bad_baseline_never_sends_write(tmp_path, issue):
    server, station = setup(tmp_path)
    if issue == "missing_da": station.omit_da = True
    elif issue == "short_da": station.da = station.da[:-1]
    elif issue == "wrong_type": station.da[0] = 3
    elif issue == "clock_on": station.da[1] = 0x80
    elif issue == "pending": station.da[2] = 1
    elif issue == "failed_transfer": station.da[2] = 2
    elif issue == "first_selector": station.da[18] = 2
    elif issue == "second_selector": station.da[19] = 2
    elif issue == "firmware": station.version = b"\4" + bytes((10, 4, 1, 1)) + bytes(24)
    elif issue == "ac_timer": station.a4[1] = 1
    elif issue == "dc_timer": station.a4[9] = 1
    elif issue == "tou": station.d9[2] = 1
    with pytest.raises(ValueError):
        asyncio.run(server.set_clock_brightness(1, True))
    assert [r[0] for r in station.requests] == ["0100"]


@pytest.mark.parametrize("target,index", [("da", 3), ("a4", 18), ("d9", 3), ("a7", 1), ("b2", 1)])
def test_protected_change_fails_without_retry(tmp_path, target, index):
    server, station = setup(tmp_path)
    station.mutate = lambda: getattr(station, target).__setitem__(index, getattr(station, target)[index] ^ 1)
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(server.set_clock_brightness(1, True))
    assert len([r for r in station.requests if r[0] == "0091"]) == 1


def test_lost_ack_and_ignored_write_are_not_retried(tmp_path):
    server, station = setup(tmp_path)
    station.fail_write = True
    with pytest.raises(TimeoutError):
        asyncio.run(server.set_clock_brightness(1, True))
    assert station.da[18] == 1 and [r[0] for r in station.requests] == ["0100", "0091"]
    server, station = setup(tmp_path)
    station.ignore = True
    with pytest.raises(RuntimeError):
        asyncio.run(server.set_clock_brightness(1, True))
    assert len([r for r in station.requests if r[0] == "0091"]) == 1


def test_c2000_readonly_and_command_contract(tmp_path):
    assert "set-clock-brightness" not in native_commands_for_model(Model.C2000_GEN2)
    with pytest.raises(ValueError):
        NativeMqttCommands("SYNTHETIC", "a" * 40, model=Model.C2000_GEN2).clock_brightness(1, True)
    server, station = setup(tmp_path, allow_control=False)
    with pytest.raises(PermissionError):
        asyncio.run(server.set_clock_brightness(1, True))
    assert not station.requests
    validate_command("set-clock-brightness", {"window": 1, "high": True})
    with pytest.raises(ValueError):
        validate_command("set-clock-brightness", {"window": True, "high": True})
