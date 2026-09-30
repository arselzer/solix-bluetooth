"""Device Timeout UI contracts use fake transports and private temporary configs."""

import argparse
import asyncio
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from solix_link import cli, interactive
from solix_link.ap_service_config import APServiceConfig, private_write
from solix_link.config import DeviceConfig, save_config
from solix_link.protocol import DEVICE_TIMEOUT_MINUTES, Model
from solix_link.tui import Target, TuiBackend, controls_for, parse_device_timeout


class FakeMonitor:
    def __init__(self, *_args, **kwargs):
        self.callback = kwargs.get("on_update")
        self.connected = False
        self.metrics = {"device_timeout_minutes": 720, "battery_percentage": 80}
        self.calls = []

    async def connect(self, **_kwargs):
        self.connected = True
        if self.callback:
            self.callback(self.metrics)

    async def wait_for_update(self, **_kwargs):
        return self.metrics.copy()

    async def set_device_timeout(self, minutes):
        self.calls.append(minutes)
        self.metrics["device_timeout_minutes"] = minutes
        if self.callback:
            self.callback(self.metrics)
        return self.metrics.copy()

    async def disconnect(self):
        self.connected = False


@pytest.mark.parametrize("model", [Model.C1000, Model.C1000_GEN2])
def test_cli_device_timeout_delegates_and_reports_fresh_readback(tmp_path, monkeypatch, capsys, model):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, "a" * 40 if model == Model.C1000_GEN2 else None)], path)
    monitor = FakeMonitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    args = cli.parser().parse_args(["set-device-timeout", "--name", "ups", "--minutes", "0", "--config", str(path)])
    asyncio.run(cli._set(args))
    output = capsys.readouterr()
    assert json.loads(output.out)["confirmed"] == {"device_timeout_minutes": 0}
    assert "other sleep behavior" in output.err
    assert monitor.calls == [0] and not monitor.connected


@pytest.mark.parametrize("model,protocol", [(Model.C300, "legacy"), (Model.C2000_GEN2, "prime"),
                                           (Model.C1000_GEN2, "legacy")])
def test_cli_timeout_rejects_unsupported_profiles_before_connect(tmp_path, monkeypatch, model, protocol):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, protocol=protocol)], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: pytest.fail("No connection for unsupported profile"))
    with pytest.raises(ValueError, match="not verified"):
        asyncio.run(cli._set(argparse.Namespace(command="set-device-timeout", name="ups", minutes=0, config=path)))


@pytest.mark.parametrize("value", [True, 30.0, "30", -1, 1, 90, 1441])
def test_cli_timeout_rejects_non_integer_or_unknown_minutes_before_connect(tmp_path, monkeypatch, value):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: pytest.fail("No connection for invalid timeout"))
    with pytest.raises(ValueError, match="Device Timeout"):
        asyncio.run(cli._set(argparse.Namespace(command="set-device-timeout", name="ups", minutes=value, config=path)))


@pytest.mark.parametrize("minutes", DEVICE_TIMEOUT_MINUTES)
def test_tui_accepts_only_documented_timeout_values(minutes):
    assert parse_device_timeout(str(minutes)) == minutes


@pytest.mark.parametrize("text", ["", "true", "30.0", "-1", "90", "1441", "Never"])
def test_tui_rejects_invalid_timeout_text(text):
    with pytest.raises(ValueError, match="Device Timeout"):
        parse_device_timeout(text)


@pytest.mark.parametrize("model,native,expected", [(Model.C1000, False, True), (Model.C1000_GEN2, False, True),
                                                 (Model.C1000_GEN2, True, True), (Model.C2000_GEN2, False, False),
                                                 (Model.C2000_GEN2, True, False), (Model.C300, False, False)])
def test_tui_timeout_profile_controls_and_caveats(model, native, expected):
    controls = {control.key: control for control in controls_for(Target("test", "Station", model, native))}
    assert ("device-timeout" in controls) is expected
    if expected:
        assert "0 = Never" in controls["device-timeout"].hint
        assert "other sleep behavior" in controls["device-timeout"].hint


def test_tui_device_timeout_ble_and_native_handoffs(tmp_path):
    async def run():
        ble = TuiBackend([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)], monitor_factory=FakeMonitor)
        await ble.connect("ble:ups")
        result = await ble.control("device-timeout", "0")
        assert result["metrics"]["device_timeout_minutes"] == 0
        assert ble.monitor.calls == [0]
        with pytest.raises(ValueError):
            await ble.control("device-timeout", "90")
        assert ble.monitor.calls == [0]
        await ble.disconnect()

        config = APServiceConfig("ups", "wlan_unused", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40, model=Model.C1000_GEN2)
        private_write(tmp_path / "ap_service.json", json.dumps(asdict(config)))
        calls = []
        async def request(_directory, command, **fields):
            calls.append((command, fields))
            return {"control_enabled": True, "metrics": {"device_timeout_minutes": fields.get("minutes", 720)}}
        native = TuiBackend([], tmp_path, requester=request)
        await native.connect("native")
        await native.control("device-timeout", "30")
        assert calls == [("status", {}), ("set-device-timeout", {"minutes": 30})]
        with pytest.raises(ValueError):
            await native.control("device-timeout", "90")
        assert len(calls) == 2
        await native.disconnect()
    asyncio.run(run())


def test_line_menu_timeout_requires_confirmation_and_keeps_never_explicit(monkeypatch, tmp_path, capsys):
    called = []
    async def set_command(args):
        called.append(args.minutes)
    monkeypatch.setattr(cli, "_set", set_command)
    replies = iter(["1", "0", "1", "1", "2", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    device = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)
    interactive.device_timeout_menu(device, tmp_path)
    assert called == []
    interactive.device_timeout_menu(device, tmp_path)
    interactive.device_timeout_menu(device, tmp_path)
    assert called == [0, 30]
    output = capsys.readouterr().out
    assert "Never" in output and "other sleep behavior" in output and "interrupting remote access" in output


def test_line_menu_native_timeout_handoff(monkeypatch, tmp_path):
    called = []
    async def request(directory, command, **fields):
        called.append((directory, command, fields))
        return {"metrics": {"device_timeout_minutes": 0}}
    monkeypatch.setattr(interactive, "ap_service_request", request)
    replies = iter(["1", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    interactive.device_timeout_menu(SimpleNamespace(name="ups"), tmp_path, tmp_path / "ap")
    assert called == [(tmp_path / "ap", "set-device-timeout", {"name": "ups", "minutes": 0})]
