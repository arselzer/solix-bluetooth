"""Standalone gateway contract tests; these do not simulate the HA runtime."""

import asyncio
import importlib.util
import json
from pathlib import Path
import time

import aiohttp
from aiohttp import web
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("solix_ha_api", ROOT / "custom_components/solix_link/api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def status(**changes):
    result = {
        "name": "UPS / office", "model": "c2000_gen2", "protocol": "native_mqtt",
        "connected": True, "available": True, "last_seen_timestamp": time.time(),
        "power_flow": "grid", "controls": list(api.COMMANDS),
        "metrics": {"battery_percentage": 91, "ac_input_power_w": 700, "ac_output_power_w": 700,
                    "ac_charging_power_limit_w": 1800, "max_charge_percentage": 90,
                    "min_charge_percentage": 1, "backup_reserve_percentage": 10,
                    "ac_output_enabled": 1, "ac_input_connected": 1, "ac_fast_charge_enabled": 0},
    }
    result.update(changes)
    return result


def test_identity_and_secret_fields():
    assert api.gateway_id("HTTP://EXAMPLE.test:80/") == api.gateway_id("http://example.test")
    assert api.device_id("gateway", "a:b") != api.device_id("gateway:a", "b")
    raw = status()
    raw["serial_number"] = "private"
    raw["metrics"].update(serial_number="private", owner_id="private", raw_tlv="private")
    raw["controls"].append("set-ac-output")
    parsed = api.parse_snapshot(raw)
    assert "private" not in json.dumps(parsed)
    assert "set-ac-output" not in parsed["controls"]


def test_firmware_diagnostics_are_kept_raw_without_health_or_power_inferences():
    raw = status(model="c1000_gen2")
    raw["metrics"].update(dc_input_active=1, pv_weak_light_locked=1, dc_input_power_raw=123,
                          controller_error_code=27, battery_health_raw=100,
                          battery_health=100, named_controller_fault="not-verified")
    metrics = api.parse_snapshot(raw)["metrics"]
    assert {key: metrics[key] for key in ("dc_input_active", "pv_weak_light_locked", "dc_input_power_raw", "controller_error_code", "battery_health_raw")} == {
        "dc_input_active": 1, "pv_weak_light_locked": 1, "dc_input_power_raw": 123, "controller_error_code": 27, "battery_health_raw": 100}
    assert "battery_health" not in metrics and "named_controller_fault" not in metrics


@pytest.mark.parametrize("url", ["ftp://gateway", "http://user:secret@gateway", "http://gateway/?token=secret",
                                 "http://gateway/#fragment", "http://gateway:99999", "http://gateway\n"])
def test_url_rejects_credentials_parameters_and_invalid_values(url):
    with pytest.raises(ValueError):
        api.normalize_url(url)


@pytest.mark.parametrize("seen,available", [(None, False), (900, False), (999, True), (1010, False), (float("nan"), False)])
def test_staleness(seen, available):
    assert api.snapshot_available(status(last_seen_timestamp=seen), 30, now=1000) is available


@pytest.mark.parametrize("raw,expected", [(0, False), (1, True), (None, None), (2, None),
                                         (-1, None), ("1", None), (1.0, None), (True, None)])
def test_explicit_binary_states_only(raw, expected):
    assert api.binary_state(raw) is expected


@pytest.mark.parametrize("payload", [
    {"command": "set-charge-power", "watts": True},
    {"command": "set-charge-power", "watts": 250},
    {"command": "set-charge-power", "watts": 1900},
    {"command": "set-charge-power", "watts": 300, "ac_output": False},
    {"command": "set-charge-cap", "upper": 81},
    {"command": "set-backup-reserve", "reserve": 5},
    {"command": "set-backup-reserve", "reserve": 95},
    {"command": "return-grid", "timeout": True},
    {"command": []},
    {"command": "set-ac-output", "enabled": False},
])
def test_invalid_controls_are_rejected(payload):
    with pytest.raises(ValueError):
        api.validate_command(status(), payload)


def test_schedule_validation_and_guard():
    periods = [{"tariff": "off_peak", "start_hour": 0, "end_hour": 24}]
    api.validate_command(status(), {"command": "set-tou-plan", "enabled": True, "periods": periods})
    for invalid in ([], [{"tariff": "peak", "start_hour": True, "end_hour": 24}],
                    [{"tariff": "peak", "start_hour": 23, "end_hour": 2}], periods * 2):
        with pytest.raises(ValueError):
            api.validate_plan(invalid, True)
    snapshot = status()
    snapshot["metrics"]["ac_fast_charge_enabled"] = 1
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-tou-plan", "enabled": True, "periods": periods})
    del snapshot["metrics"]["ac_input_connected"]
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "return-grid", "timeout": 30})


def test_cap_preserves_reserve():
    snapshot = status()
    snapshot["metrics"]["backup_reserve_percentage"] = 85
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-charge-cap", "upper": 80})


@pytest.mark.parametrize("watts", [100, 200, 1000])
def test_original_c1000_power_allowed_when_advertised(watts):
    snapshot = status(model="c1000", protocol="legacy", controls=["set-charge-power"])
    api.validate_command(snapshot, {"command": "set-charge-power", "watts": watts})
    snapshot["controls"] = []
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-charge-power", "watts": watts})


@pytest.mark.parametrize("watts", [0, 50, 1100, True])
def test_original_c1000_rejects_invalid_power(watts):
    with pytest.raises(ValueError):
        api.validate_command(status(model="c1000"), {"command": "set-charge-power", "watts": watts})


def test_original_c1000_power_support_does_not_expand_other_controls():
    with pytest.raises(ValueError):
        api.validate_command(status(model="c1000"), {"command": "set-charge-cap", "upper": 90})
    with pytest.raises(ValueError):
        api.validate_command(status(model="c300"), {"command": "set-charge-power", "watts": 330})


async def with_gateway(callback):
    state = {"snapshot": status(), "posts": [], "post_status": 200, "post_body": None, "auth": True}

    async def devices(request):
        if state["auth"] and request.headers.get("Authorization") != "Bearer test-token":
            return web.json_response({"error": "Unauthorized"}, status=401)
        return web.json_response({"devices": [state["snapshot"]]})

    async def device(request):
        assert request.match_info["name"] == "UPS / office"
        assert request.headers.get("Authorization") == "Bearer test-token"
        return web.json_response(state["snapshot"])

    async def command(request):
        assert request.match_info["name"] == "UPS / office"
        assert request.headers.get("Authorization") == "Bearer test-token"
        state["posts"].append(await request.json())
        body = state["post_body"] if state["post_body"] is not None else state["snapshot"]
        return web.json_response(body, status=state["post_status"])

    app = web.Application()
    app.router.add_get("/devices", devices)
    app.router.add_get("/devices/{name}", device)
    app.router.add_post("/devices/{name}/commands", command)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            await callback(api.GatewayClient(session, f"http://127.0.0.1:{port}", "test-token"), state)
    finally:
        await runner.cleanup()


def test_http_roundtrip_and_capability_revocation():
    async def scenario(client, state):
        assert list(await client.async_devices()) == ["UPS / office"]
        payload = {"command": "set-charge-power", "watts": 300}
        result = await client.async_command("UPS / office", payload)
        assert result["power_flow"] == "grid"
        assert state["posts"] == [payload]
        state["snapshot"]["controls"] = []
        with pytest.raises(ValueError):
            await client.async_command("UPS / office", payload)
        assert len(state["posts"]) == 1
        state["snapshot"]["controls"] = list(api.COMMANDS)
        state["snapshot"]["last_seen_timestamp"] -= 100
        with pytest.raises(ValueError):
            await client.async_command("UPS / office", payload)
        assert len(state["posts"]) == 1
    asyncio.run(with_gateway(scenario))


def test_http_auth_and_command_timeout_no_retry():
    async def scenario(client, state):
        client.token = ""
        with pytest.raises(api.GatewayAuthError):
            await client.async_devices()
        with pytest.raises(api.GatewayAuthError):
            await client.async_command("UPS / office", {"command": "return-grid", "timeout": 30})
        assert not state["posts"]
        client.token = "test-token"
        state["post_status"] = 504
        state["post_body"] = {"error": "PowerFlowTimeout"}
        with pytest.raises(api.GatewayCommandError, match="settings may have changed"):
            await client.async_command("UPS / office", {"command": "return-grid", "timeout": 30})
        assert len(state["posts"]) == 1
    asyncio.run(with_gateway(scenario))


def test_http_command_deadlines_match_gateway_budgets():
    async def scenario(client, state):
        requests = []
        session = client.session

        class RecordingSession:
            def request(self, method, url, **kwargs):
                requests.append((method, kwargs["timeout"]))
                return session.request(method, url, **kwargs)

        client.session = RecordingSession()
        commands = [
            ({"command": "set-charge-power", "watts": 300}, 60),
            ({"command": "set-tou-plan", "enabled": True,
              "periods": [{"tariff": "off_peak", "start_hour": 0, "end_hour": 24}]}, 135),
            ({"command": "return-grid", "timeout": 30}, 175),
        ]
        for payload, expected in commands:
            await client.async_command("UPS / office", payload)
            assert requests[-2][0] == "GET" and requests[-2][1].total == 10
            assert requests[-1][0] == "POST" and requests[-1][1].total == expected
            assert requests[-1][1].sock_connect == 10
        assert len(state["posts"]) == 3
    asyncio.run(with_gateway(scenario))


def test_malformed_response_and_duplicate_names():
    with pytest.raises(api.GatewayError):
        api.parse_snapshot(status(available="yes"))

    async def scenario(client, state):
        state["post_body"] = {"ok": True}
        with pytest.raises(api.GatewayCommandError, match="settings may have changed"):
            await client.async_command("UPS / office", {"command": "return-grid", "timeout": 30})
    asyncio.run(with_gateway(scenario))

    async def duplicate_scenario():
        client = api.GatewayClient(None, "http://gateway.test")

        async def request(method, path):
            return {"devices": [status(), status()]}

        client._request = request
        with pytest.raises(api.GatewayError, match="unique"):
            await client.async_devices()
    asyncio.run(duplicate_scenario())


def test_manifest_and_translations():
    base = ROOT / "custom_components/solix_link"
    manifest = json.loads((base / "manifest.json").read_text())
    assert manifest["domain"] == "solix_link" and manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert json.loads((base / "strings.json").read_text()) == json.loads((base / "translations/en.json").read_text())


def c1000_native(**changes):
    snapshot = status(model="c1000_gen2")
    snapshot["metrics"].update(ac_charging_power_limit_w=1200,
                               temperature_unit_fahrenheit=0, ac_off_grid_alert_enabled=0)
    snapshot.update(changes)
    return snapshot


@pytest.mark.parametrize("payload", [
    {"command": "set-backup-reserve", "reserve": 15},
    {"command": "set-discharge-floor", "lower": 5},
    {"command": "set-temperature-unit", "fahrenheit": True},
    {"command": "set-off-grid-alert", "enabled": False},
    {"command": "return-grid", "timeout": 30},
    {"command": "set-tou-plan", "enabled": True,
     "periods": [{"tariff": "off_peak", "start_hour": 0, "end_hour": 24}]},
])
def test_c1000_native_capabilities_reach_ha(payload):
    snapshot = api.parse_snapshot(c1000_native())
    api.validate_command(snapshot, payload)
    assert snapshot["metrics"]["temperature_unit_fahrenheit"] == 0
    assert snapshot["metrics"]["ac_off_grid_alert_enabled"] == 0


@pytest.mark.parametrize("payload", [
    {"command": "set-discharge-floor", "lower": 5},
    {"command": "set-temperature-unit", "fahrenheit": True},
    {"command": "set-off-grid-alert", "enabled": False},
])
@pytest.mark.parametrize("changes", [
    {"model": "c2000_gen2"}, {"model": "c1000"}, {"model": "c300"},
    {"protocol": "prime"}, {"protocol": "legacy"},
    {"controls": []}, {"last_seen_timestamp": 0},
])
def test_c1000_settings_refuse_wrong_profile_or_revoked_capability(payload, changes):
    if changes == {"model": "c1000"} and payload["command"] == "set-temperature-unit":
        # Original native support requires this explicit advertised capability.
        api.validate_command(api.parse_snapshot(c1000_native(**changes)), payload)
        return
    with pytest.raises(ValueError):
        api.validate_command(api.parse_snapshot(c1000_native(**changes)), payload)


@pytest.mark.parametrize("payload", [
    {"command": "set-discharge-floor", "lower": True},
    {"command": "set-discharge-floor", "lower": 2},
    {"command": "set-discharge-floor", "lower": 10},  # Reserve is only 10%.
    {"command": "set-discharge-floor", "lower": 5, "reserve": 15},
    {"command": "set-temperature-unit", "fahrenheit": 1},
    {"command": "set-off-grid-alert", "enabled": "false"},
])
def test_new_settings_strict_values_and_fields(payload):
    with pytest.raises(ValueError):
        api.validate_command(c1000_native(), payload)


@pytest.mark.parametrize("metric", ["temperature_unit_fahrenheit", "ac_off_grid_alert_enabled"])
@pytest.mark.parametrize("value", [None, 2, True, 0.0, "0"])
def test_boolean_settings_need_explicit_readback(metric, value):
    snapshot = c1000_native()
    snapshot["metrics"][metric] = value
    payload = ({"command": "set-temperature-unit", "fahrenheit": True}
               if metric == "temperature_unit_fahrenheit"
               else {"command": "set-off-grid-alert", "enabled": True})
    with pytest.raises(ValueError):
        api.validate_command(api.parse_snapshot(snapshot), payload)


def test_discharge_floor_options_follow_fresh_reserve_without_adjusting_it():
    snapshot = c1000_native()
    assert api.discharge_floor_options(snapshot) == ["1%", "5%"]
    snapshot["metrics"]["backup_reserve_percentage"] = 25
    assert api.discharge_floor_options(snapshot) == ["1%", "5%", "10%", "15%", "20%"]
    snapshot["metrics"]["backup_reserve_percentage"] = 5
    assert api.discharge_floor_options(snapshot) == []  # Invalid current lower/reserve pair.
    assert snapshot["metrics"]["backup_reserve_percentage"] == 5


@pytest.mark.parametrize("command", ["set-backup-reserve", "set-tou-plan", "return-grid"])
def test_c1000_native_charging_features_do_not_expand_ble_support(command):
    payload = {"set-backup-reserve": {"reserve": 15},
               "set-tou-plan": {"periods": [], "enabled": False},
               "return-grid": {"timeout": 30}}[command]
    with pytest.raises(ValueError):
        api.validate_command(c1000_native(protocol="prime"), {"command": command, **payload})


def test_c1000_native_cap_preserves_reserve():
    snapshot = c1000_native()
    snapshot["metrics"]["backup_reserve_percentage"] = 85
    with pytest.raises(ValueError):
        api.validate_command(snapshot, {"command": "set-charge-cap", "upper": 80})


def test_c1000_http_settings_recheck_limits_and_do_not_retry_writes():
    async def scenario(client, state):
        state["snapshot"] = c1000_native()
        payload = {"command": "set-discharge-floor", "lower": 5}
        await client.async_command("UPS / office", payload)
        assert state["posts"] == [payload]
        state["snapshot"]["metrics"]["backup_reserve_percentage"] = 5
        with pytest.raises(ValueError):
            await client.async_command("UPS / office", payload)
        assert state["posts"] == [payload]
        state["snapshot"] = c1000_native()
        state["post_status"] = 409
        state["post_body"] = {"error": "RuntimeError", "settings_may_have_changed": True,
                              "details": "PRIVATE diagnostic"}
        with pytest.raises(api.GatewayCommandError, match="settings may have changed") as caught:
            await client.async_command("UPS / office", {"command": "set-off-grid-alert", "enabled": True})
        assert "PRIVATE" not in str(caught.value)
        assert len(state["posts"]) == 2
    asyncio.run(with_gateway(scenario))
