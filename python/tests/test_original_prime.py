"""Original C1000 Prime monitoring, saved transport choice and control exclusion."""

import argparse
import asyncio

import pytest

from solix_link import cli, interactive
from solix_link.config import DeviceConfig, load_config, protocol_choices, save_config
from solix_link.manager import MonitorService
from solix_link.mqtt_bridge import _supports_operation
from solix_link.protocol import Model, Session
from solix_link.tui import TuiBackend, controls_for, create_app


def original(*, protocol="prime", client_id="a" * 40):
    return DeviceConfig("original", "AA:BB:CC:DD:EE:04", Model.C1000, client_id,
                        protocol, "Europe/Vienna")


def test_explicit_original_prime_config_roundtrip_preserves_legacy_default(tmp_path):
    path = tmp_path / "config.json"
    legacy = DeviceConfig("legacy", "AA:BB:CC:DD:EE:01", Model.C1000)
    save_config([legacy, original()], path)
    assert load_config(path) == [legacy, original()]
    assert legacy.protocol == "legacy" and legacy.client_id is None
    assert path.stat().st_mode & 0o777 == 0o600
    assert protocol_choices(Model.C1000) == ("legacy", "prime")
    assert protocol_choices(Model.C1000_GEN2) == ("prime", "legacy")
    assert protocol_choices(Model.C300) == ("legacy",)
    assert protocol_choices(Model.C2000_GEN2) == ("prime",)
    with pytest.raises(ValueError, match="legacy"):
        DeviceConfig("wrong", legacy.address, Model.C300, protocol="prime")


def test_original_cli_add_accepts_prime_and_pair_parser_accepts_model(tmp_path):
    path = tmp_path / "config.json"
    assert cli.main(["add", "--name", "original", "--address", original().address,
                     "--model", "c1000", "--protocol", "prime", "--client-id", "a" * 40,
                     "--config", str(path)]) == 0
    assert load_config(path)[0].protocol == "prime"
    assert cli.parser().parse_args(["pair", "--name", "original", "--address", original().address,
                                   "--model", "c1000"]).model == "c1000"


def test_pair_saves_original_prime_client_id_without_reflecting_it(monkeypatch, tmp_path, capsys):
    path = tmp_path / "config.json"
    old = original(protocol="legacy")
    save_config([old], path)
    observed = []

    class Monitor:
        def __init__(self, _address, **kwargs):
            observed.append(kwargs)
            self.owner_user_id = kwargs["owner_user_id"]
            self.pairing_required = asyncio.Event()

        async def connect(self, **_kwargs):
            pass

        async def wait_for_update(self, **_kwargs):
            return {"battery_percentage": 98, "ac_output_enabled": 1}

        async def disconnect(self):
            pass

    monkeypatch.setattr(cli, "SolixMonitor", Monitor)
    asyncio.run(cli._pair(argparse.Namespace(name=old.name, address=old.address,
                                            model="c1000", client_id=None,
                                            timezone=None, config=path)))
    assert load_config(path) == [original()]
    assert observed[0]["protocol"] == "prime" and observed[0]["model"] == Model.C1000
    assert observed[0]["owner_user_id"] == old.client_id
    assert old.client_id not in capsys.readouterr().out


@pytest.mark.parametrize("command,values", [
    ("set-display-timeout", {"seconds": 60}),
    ("set-ac-output", {"enabled": "off"}), ("set-light", {"mode": "low"}),
    ("set-temperature-unit", {"unit": "fahrenheit"}),
    ("set-fast-charge", {"enabled": "on"}), ("set-ac-power-saving", {"enabled": "on"}),
])
def test_original_prime_cli_controls_fail_before_opening_transport(monkeypatch, tmp_path, command, values):
    path = tmp_path / "config.json"
    save_config([original()], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_a, **_k: pytest.fail("No connection for unverified controls"))
    with pytest.raises(ValueError, match="not verified for original C1000 Prime"):
        asyncio.run(cli._set(argparse.Namespace(command=command, name="original", config=path, **values)))


def test_original_prime_gateway_and_bridge_expose_only_verified_controls():
    service = MonitorService([original()])
    assert service.supported_commands("original") == ["set-charge-power", "set-device-timeout", "set-display-brightness"]
    for operation in ("ac_charging_power", "device_timeout", "display_brightness"):
        assert _supports_operation(original(), operation)
    for operation in ("display_timeout", "ac_output", "light_mode", "temperature_unit",
                      "fast_charge", "ac_power_saving", "dc_power_saving"):
        assert not _supports_operation(original(), operation)
        with pytest.raises(ValueError, match="not verified for original C1000 Prime"):
            asyncio.run(service.apply_setting("original", operation))
    session = Session(Model.C1000, protocol="prime", owner_user_id="a" * 40)
    session.ready, session._secret = True, bytes(range(32))
    with pytest.raises(RuntimeError, match="legacy"):
        session.c1000_control_packet("ac_output_enabled", False)


@pytest.mark.parametrize("command,values,expected", [
    ("set-charge-power", {"watts": 900}, ("power", 900)),
    ("set-display-brightness", {"level": 1}, ("display_brightness", 1)),
    ("set-device-timeout", {"minutes": 0}, ("timeout", 0)),
])
def test_original_prime_cli_routes_verified_controls_with_saved_id(monkeypatch, tmp_path, command, values, expected):
    path = tmp_path / "config.json"
    save_config([original()], path)
    calls, constructor = [], []

    class Monitor:
        def __init__(self, _address, **kwargs):
            constructor.append(kwargs)

        async def connect(self, **_kwargs):
            pass

        async def disconnect(self):
            pass

        async def set_ac_charging_power(self, watts):
            calls.append(("power", watts))
            return {"ac_charging_power_limit_w": watts}

        async def set_device_timeout(self, minutes):
            calls.append(("timeout", minutes))
            return {"device_timeout_minutes": minutes}

        async def set_c1000_setting(self, setting, value):
            calls.append((setting, value))
            return {"display_brightness": value}

    monkeypatch.setattr(cli, "SolixMonitor", Monitor)
    asyncio.run(cli._set(argparse.Namespace(command=command, name="original", config=path, **values)))
    assert calls == [expected]
    assert constructor[0]["owner_user_id"] == original().client_id and constructor[0]["protocol"] == "prime"


def test_line_menu_can_switch_original_protocol_without_pairing(monkeypatch, tmp_path, capsys):
    path = tmp_path / "config.json"
    legacy = original(protocol="legacy")
    save_config([legacy], path)
    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda _label: next(answers))
    changed = interactive.change_protocol(legacy, path)
    assert changed == original() and load_config(path) == [changed]
    output = capsys.readouterr().out
    assert "other controls remain unavailable" in output and legacy.client_id not in output


def test_line_main_protocol_choice_does_not_pair_before_configuration(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    legacy = original(protocol="legacy")
    save_config([legacy], path)
    monkeypatch.setattr(interactive, "select_device", lambda _path: legacy)
    monkeypatch.setattr(interactive, "ensure_paired", lambda *_args: pytest.fail("Configuration needs no station connection"))
    answers = iter(["7", "2", "0"])
    monkeypatch.setattr("builtins.input", lambda _label: next(answers))
    interactive.run_interactive(path)
    assert load_config(path) == [original()]


def test_terminal_backend_protocol_change_disconnects_and_retains_pairing(tmp_path):
    async def run():
        path = tmp_path / "config.json"
        legacy = original(protocol="legacy")
        save_config([legacy], path)
        backend = TuiBackend([legacy], config_path=path)
        disconnected = []

        class Monitor:
            async def disconnect(self):
                disconnected.append(True)

        backend.monitor, backend.target = Monitor(), backend.targets[0]
        changed = await backend.set_protocol("ble:original", "prime")
        assert backend.target is None and backend.monitor is None and disconnected == [True]
        assert load_config(path) == [original()] and changed.device == original()
        assert {control.key for control in controls_for(changed)} == {"charge-power", "display-brightness", "device-timeout"}
        assert "prime" in changed.label and legacy.client_id not in changed.label
    asyncio.run(run())


def test_terminal_original_prime_can_connect_readonly_and_reject_unpaired():
    async def run():
        calls = []

        class Monitor:
            def __init__(self, _address, **kwargs):
                calls.append(kwargs)
                self.connected = False
                self.metrics = {"battery_percentage": 98, "software_version": "1.7.1", "ac_output_enabled": 1}

            async def connect(self, **_kwargs):
                self.connected = True

            async def wait_for_update(self, **_kwargs):
                return self.metrics.copy()

            async def disconnect(self):
                self.connected = False

        backend = TuiBackend([original()], monitor_factory=Monitor)
        snapshot = await backend.connect("ble:original")
        assert snapshot["available"] and calls[0]["protocol"] == "prime"
        assert snapshot["power_flow"] == "unknown"
        with pytest.raises(ValueError, match="unavailable"):
            await backend.control("ac-output", "off")
        await backend.disconnect()
        backend = TuiBackend([original(client_id=None)], monitor_factory=Monitor)
        with pytest.raises(ValueError, match="Pair this station first"):
            await backend.connect("ble:original")
        assert len(calls) == 1
    asyncio.run(run())


def test_headless_original_protocol_selector_has_cancel_and_save(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Select, Static

    async def run():
        path = tmp_path / "config.json"
        legacy = original(protocol="legacy")
        save_config([legacy], path)
        backend = TuiBackend([legacy], config_path=path)
        app = create_app(path, backend=backend)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#station-protocol")
            await pilot.pause()
            app.screen.query_one("#protocol-choice", Select).value = "prime"
            await pilot.click("#protocol-cancel")
            assert load_config(path) == [legacy]
            await pilot.click("#station-protocol")
            await pilot.pause()
            app.screen.query_one("#protocol-choice", Select).value = "prime"
            await pilot.click("#protocol-save")
            await pilot.pause()
            assert load_config(path) == [original()]
            assert {control.key for control in controls_for(backend.targets[0])} == {"charge-power", "display-brightness", "device-timeout"}
            assert "Other controls remain unavailable" in str(app.query_one("#notice", Static).render())
            assert app.query_one("#apply-setting").disabled
            app.save_screenshot(filename="solix-original-prime-dashboard.svg", path="/tmp")
    asyncio.run(run())
