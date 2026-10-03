"""Setup inspection stays read-only in the fixed dashboard and line menu."""

import asyncio
from types import SimpleNamespace

import pytest

from solix_link import ap_service_check, interactive
from solix_link.protocol import Model
from solix_link.tui import TuiBackend, create_app


def report():
    return {
        "schema": 1, "ok": True, "read_only": True, "live_confirmation_required": True,
        "profiles": [{"profile": "primary", "model": "c1000_gen2", "configuration_valid": True}],
        "findings": [{"profile": "primary", "severity": "warning", "code": "paired_config_not_supplied",
                      "message": "A live station connection still needs confirmation."}],
    }


def test_backend_only_reads_selected_setup_without_touching_connections(tmp_path, monkeypatch):
    calls = []
    def check(directory, paired):
        calls.append((directory, paired))
        return report()
    monkeypatch.setattr(ap_service_check, "check_ap_service", check)
    def no_monitor(*args, **kwargs):
        pytest.fail("Setup check must not open Bluetooth")
    async def no_request(*args, **kwargs):
        pytest.fail("Setup check must not contact the AP worker")

    async def run():
        paired = tmp_path / "paired.json"
        backend = TuiBackend([], tmp_path, config_path=paired,
                             monitor_factory=no_monitor, requester=no_request)
        backend.native_snapshot = {"preserved": True}
        assert (await backend.check_ap_setup())["read_only"]
        assert backend.native_snapshot == {"preserved": True}
        assert backend.target is None
        assert calls == [(tmp_path, paired)]
        with pytest.raises(ValueError, match="AP directory"):
            await TuiBackend([]).check_ap_setup()

    asyncio.run(run())


def test_f8_opens_read_only_modal_and_returns_without_resetting_readings(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    from textual.widgets import Button, DataTable
    calls = []
    monkeypatch.setattr(ap_service_check, "check_ap_service", lambda *args: calls.append(args) or report())

    async def run():
        app = create_app(backend=TuiBackend([], tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            app.render_snapshot({"connected": False, "available": False, "metrics": {"battery_percentage": 72}})
            await pilot.press("f8")
            async with asyncio.timeout(3):
                while app.screen.__class__.__name__ != "SetupCheckScreen":
                    await pilot.pause(.01)
            assert app.screen.query_one("#setup-check-close", Button)
            assert calls == [(tmp_path, None)]
            await pilot.press("f8")
            await pilot.pause()
            assert len(calls) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen.__class__.__name__ != "SetupCheckScreen"
            assert app.query_one("#readings", DataTable).get_cell("battery_percentage", "value") == "72"
            await app.action_quit()

    asyncio.run(run())


def test_line_menu_checker_does_not_spawn_or_request_station(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(ap_service_check, "check_ap_service", lambda *args: calls.append(args) or report())
    def forbidden(*args, **kwargs):
        pytest.fail("Setup check must not start a service or request station status")
    monkeypatch.setattr(interactive.subprocess, "Popen", forbidden)
    monkeypatch.setattr(interactive, "ap_service_request", forbidden)
    answers = iter(["7", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    config = tmp_path / "paired.json"
    interactive.mqtt_menu(SimpleNamespace(model=Model.C1000_GEN2, protocol="prime"), config, tmp_path)
    assert calls == [(tmp_path, config)]
    assert '"read_only": true' in capsys.readouterr().out
