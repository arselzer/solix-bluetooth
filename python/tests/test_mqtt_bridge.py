import asyncio
import json

import pytest

from solix_gen2.config import DeviceConfig
from solix_gen2.manager import MonitorService
from solix_gen2.mqtt_bridge import MqttBridge, decode_setting
from solix_gen2.protocol import Model


def test_mqtt_setting_payloads_are_strict():
    assert decode_setting("charge_limits", b'{"upper":95,"lower":1}') == {"upper": 95, "lower": 1}
    assert decode_setting("fast_charge", b'{"enabled":false}') == {"enabled": False}
    for operation, payload in (
        ("dc_output", b'{"enabled":false}'),
        ("charge_limits", b'{"upper":true,"lower":1}'),
        ("ac_charging_power", b'{"watts":"300"}'),
        ("fast_charge", b'{"enabled":1}'),
        ("charge_limits", b'{"upper":95,"lower":1,"extra":0}'),
    ):
        with pytest.raises(ValueError):
            decode_setting(operation, payload)


def test_bridge_uses_only_existing_c1000_link_and_ignores_retained_commands():
    class FakeClient:
        def __init__(self):
            self.messages = []

        def publish(self, topic, payload, **options):
            self.messages.append((topic, json.loads(payload)))

    class FakeMonitor:
        connected = True

        def __init__(self):
            self.calls = []

        async def set_charge_limits(self, upper, lower):
            self.calls.append((upper, lower))
            return {"max_charge_percentage": upper, "min_charge_percentage": lower}

        async def set_ac_charging_power(self, watts):
            self.calls.append(("power", watts))
            return {"ac_charging_power_limit_w": watts}

        async def set_display_timeout(self, seconds):
            self.calls.append(("display", seconds))
            return {"display_timeout_seconds": seconds}

        async def set_charge_cap(self, upper):
            self.calls.append(("cap", upper))
            return {"max_charge_percentage": upper, "min_charge_percentage": 1}

        async def set_ac_output_enabled(self, enabled):
            self.calls.append(("ac", enabled))
            return {"ac_output_enabled": int(enabled)}

        async def set_light_mode(self, mode):
            self.calls.append(("light", mode))
            return {"light_mode": mode}

    async def scenario():
        service = MonitorService([
            DeviceConfig("c1000", "AA:BB:CC:DD:EE:01", Model.C1000_GEN2, "a" * 40),
            DeviceConfig("c2000", "AA:BB:CC:DD:EE:02", Model.C2000_GEN2, "b" * 40),
            DeviceConfig("c300", "AA:BB:CC:DD:EE:03", Model.C300),
            DeviceConfig("original", "AA:BB:CC:DD:EE:04", Model.C1000),
        ])
        monitor = FakeMonitor()
        service._monitors["c1000"] = monitor
        service._monitors["c2000"] = monitor
        service._monitors["c300"] = monitor
        service._monitors["original"] = monitor
        bridge = MqttBridge(service)
        bridge._client = FakeClient()

        await bridge._handle_command("solix_gen2/c1000/set/charge_limits", b'{"upper":95,"lower":1}', False)
        assert monitor.calls == [(95, 1)]
        assert bridge._client.messages[-1][1]["confirmed"] == {
            "max_charge_percentage": 95, "min_charge_percentage": 1,
        }

        await bridge._handle_command("solix_gen2/c1000/set/charge_limits", b'{"upper":80,"lower":1}', True)
        assert monitor.calls == [(95, 1)]
        assert bridge._client.messages[-1][1]["ok"] is False

        count = len(bridge._client.messages)
        await bridge._handle_command("solix_gen2/c2000/set/charge_limits", b'{"upper":80,"lower":1}', False)
        assert len(bridge._client.messages) == count

        for name in ("c300", "original"):
            await bridge._handle_command(f"solix_gen2/{name}/set/ac_output", b'{"enabled":true}', False)
            assert monitor.calls[-1] == ("ac", True)
            assert bridge._client.messages[-1][1]["confirmed"] == {"ac_output_enabled": 1}
            await bridge._handle_command(f"solix_gen2/{name}/set/light_mode", b'{"mode":1}', False)
            assert monitor.calls[-1] == ("light", 1)
            await bridge._handle_command(f"solix_gen2/{name}/set/ac_charging_power", b'{"watts":300}', False)
            assert monitor.calls[-1] == ("power", 300)
        for name in ("c1000", "c2000"):
            before = len(monitor.calls)
            await bridge._handle_command(f"solix_gen2/{name}/set/ac_output", b'{"enabled":false}', False)
            assert len(monitor.calls) == before
            with pytest.raises(ValueError, match="not verified"):
                await service.apply_setting(name, "ac_output", enabled=False)
        with pytest.raises(ValueError, match="not verified"):
            await service.apply_setting("c2000", "charge_limits", upper=80, lower=1)

        await bridge._handle_command("solix_gen2/c2000/set/ac_charging_power", b'{"watts":1700}', False)
        assert monitor.calls[-1] == ("power", 1700)
        assert bridge._client.messages[-1][1]["confirmed"] == {"ac_charging_power_limit_w": 1700}
        await bridge._handle_command("solix_gen2/c2000/set/display_timeout", b'{"seconds":60}', False)
        assert monitor.calls[-1] == ("display", 60)
        assert bridge._client.messages[-1][1]["confirmed"] == {"display_timeout_seconds": 60}
        await bridge._handle_command("solix_gen2/c2000/set/charge_cap", b'{"upper":95}', False)
        assert monitor.calls[-1] == ("cap", 95)
        assert bridge._client.messages[-1][1]["confirmed"] == {
            "max_charge_percentage": 95, "min_charge_percentage": 1,
        }
        with pytest.raises(ValueError, match="only on C2000"):
            await service.apply_setting("c1000", "charge_cap", upper=95)
        count = len(bridge._client.messages)
        await bridge._handle_command("solix_gen2/c2000/set/fast_charge", b'{"enabled":true}', False)
        assert len(bridge._client.messages) == count

        for _ in range(bridge._commands.maxsize):
            bridge._enqueue_command("solix_gen2/c1000/set/charge_limits", b'{"upper":95,"lower":1}', False)
        bridge._enqueue_command("solix_gen2/c1000/set/charge_limits", b'{"upper":90,"lower":1}', False)
        assert bridge._client.messages[-1][1] == {
            "command": "charge_limits", "ok": False, "error": "Command queue is full",
        }
        count = len(bridge._client.messages)
        bridge._enqueue_command("solix_gen2/c2000/set/charge_limits", b'{"upper":90,"lower":1}', False)
        assert len(bridge._client.messages) == count

    asyncio.run(scenario())
