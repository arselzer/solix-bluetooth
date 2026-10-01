"""Publish local BLE telemetry and model-supported settings through MQTT.

The power stations do not connect to this broker. This process is the only
MQTT client and keeps the BLE connection on the machine running the bridge.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Any

from .manager import MonitorService
from .protocol import Model
from .c1000_capabilities import original_prime_operation_supported


_TOPIC_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_FIELDS = {
    "charge_limits": ("upper", "lower"),
    "charge_cap": ("upper",),
    "ac_charging_power": ("watts",),
    "display_timeout": ("seconds",),
    "display_brightness": ("level",),
    "fast_charge": ("enabled",),
    "ac_output": ("enabled",),
    "light_mode": ("mode",),
    "device_timeout": ("minutes",),
    "temperature_unit": ("fahrenheit",),
    "ac_power_saving": ("enabled",),
    "dc_power_saving": ("enabled",),
}
_CONFIRMED = {
    "charge_limits": ("max_charge_percentage", "min_charge_percentage"),
    "charge_cap": ("max_charge_percentage", "min_charge_percentage"),
    "ac_charging_power": ("ac_charging_power_limit_w",),
    "display_timeout": ("display_timeout_seconds",),
    "display_brightness": ("display_brightness",),
    "fast_charge": ("ac_fast_charge_enabled",),
    "ac_output": ("ac_output_enabled",),
    "light_mode": ("light_mode",),
    "device_timeout": ("device_timeout_minutes",),
    "temperature_unit": ("temperature_unit_fahrenheit",),
    "ac_power_saving": ("ac_power_saving_mode_enabled",),
    "dc_power_saving": ("dc_power_saving_mode_enabled",),
}


def _supports_operation(device: Any, operation: str) -> bool:
    if device.model in (Model.C300, Model.C1000) and device.protocol == "legacy":
        return (operation in ("display_timeout", "ac_charging_power", "ac_output", "light_mode")
                or (device.model == Model.C1000 and operation in (
                    "device_timeout", "temperature_unit", "fast_charge", "ac_power_saving", "dc_power_saving")))
    if device.protocol != 'prime':
        return False
    if device.model == Model.C1000:
        if operation == "ac_output":
            return False  # Prime output switching is limited to direct SDK/CLI/TUI.
        return original_prime_operation_supported(operation)
    if device.model == Model.C1000_GEN2:
        return operation in ('charge_limits', 'ac_charging_power', 'display_timeout', 'fast_charge', 'device_timeout')
    return device.model == Model.C2000_GEN2 and operation in ('charge_cap', 'ac_charging_power', 'display_timeout')


def decode_setting(operation: str, payload: bytes) -> dict[str, int | bool]:
    """Reject unexpected MQTT command shapes before touching Bluetooth."""
    if operation not in _FIELDS:
        raise ValueError("Unsupported setting")
    if len(payload) > 512:
        raise ValueError("Command payload is too large")
    try:
        values = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Command payload must be JSON") from error
    if not isinstance(values, dict) or set(values) != set(_FIELDS[operation]):
        raise ValueError(f"Expected JSON fields: {', '.join(_FIELDS[operation])}")
    for key, value in values.items():
        expected = bool if key in ("enabled", "fahrenheit") else int
        if type(value) is not expected:
            raise ValueError(f"{key} must be a {'boolean' if expected is bool else 'whole number'}")
    return values


class MqttBridge:
    """One broker connection sharing one BLE monitor per configured device."""

    def __init__(
        self,
        service: MonitorService,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        topic_prefix: str = "solix_gen2",
        username: str | None = None,
        password_file: Path | None = None,
        ca_file: Path | None = None,
    ) -> None:
        if not _TOPIC_SEGMENT.fullmatch(topic_prefix):
            raise ValueError("MQTT topic prefix must contain only letters, digits, _ or -")
        if password_file and not username:
            raise ValueError("MQTT username is required with a password file")
        self.service = service
        self.host = host
        self.port = port
        self.prefix = topic_prefix
        self.username = username
        self.password_file = password_file
        self.ca_file = ca_file
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._commands: asyncio.Queue[tuple[str, bytes, bool]] = asyncio.Queue(maxsize=20)
        self._connected = asyncio.Event()

    def _topic(self, name: str, suffix: str) -> str:
        return f"{self.prefix}/{name}/{suffix}"

    def _publish_snapshot(self, status: dict[str, Any]) -> None:
        name = status["name"]
        payload = {
            "name": name,
            "model": status["model"],
            "available": status["available"],
            "last_seen": status["last_seen"],
            "metrics": status["metrics"],
        }
        self._client.publish(self._topic(name, "state"), json.dumps(payload, separators=(",", ":")), qos=1, retain=True)
        self._client.publish(self._topic(name, "availability"), "online" if status["available"] else "offline", qos=1, retain=True)

    def _broker_connected(self) -> None:
        self._connected.set()
        self._client.publish(self._topic("bridge", "availability"), "online", qos=1, retain=True)
        for status in self.service.snapshots():
            self._publish_snapshot(status)

    def _enqueue_command(self, topic: str, payload: bytes, retained: bool) -> None:
        if self._commands.full():
            parts = topic.split("/")
            if len(parts) == 4 and parts[0] == self.prefix and parts[2] == "set":
                name = parts[1]
                device = self.service.devices.get(name)
                if device and _supports_operation(device, parts[3]):
                    self._client.publish(
                        self._topic(name, "result"),
                        json.dumps({"command": parts[3], "ok": False, "error": "Command queue is full"}),
                        qos=1,
                    )
            return
        self._commands.put_nowait((topic, payload, retained))

    async def _handle_command(self, topic: str, payload: bytes, retained: bool) -> None:
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != self.prefix or parts[2] != "set":
            return
        _, name, _, operation = parts
        device = self.service.devices.get(name)
        if device is None or not _supports_operation(device, operation):
            return
        try:
            if retained:
                raise ValueError("Retained commands are ignored")
            values = decode_setting(operation, payload)
            result = await asyncio.wait_for(self.service.apply_setting(name, operation, **values), timeout=30)
            message = {"command": operation, "ok": True, "confirmed": {key: result[key] for key in _CONFIRMED[operation]}}
        except (ValueError, KeyError) as error:
            message = {"command": operation, "ok": False, "error": str(error)}
        except (ConnectionError, TimeoutError, RuntimeError) as error:
            message = {"command": operation, "ok": False, "error": type(error).__name__}
        self._client.publish(self._topic(name, "result"), json.dumps(message, separators=(",", ":")), qos=1)

    async def run(self) -> None:
        """Run until cancelled, reconnecting BLE and MQTT as needed."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError as error:
            raise RuntimeError("Install the MQTT extra: pip install './python[mqtt]'") from error

        self._loop = asyncio.get_running_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.prefix}-bridge")
        self._client = client
        if self.username:
            password = self.password_file.read_text().rstrip("\r\n") if self.password_file else None
            client.username_pw_set(self.username, password)
        if self.ca_file:
            client.tls_set(ca_certs=str(self.ca_file))
        client.will_set(self._topic("bridge", "availability"), "offline", qos=1, retain=True)

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            if reason_code == 0:
                for device in self.service.devices.values():
                    for operation in _FIELDS:
                        if _supports_operation(device, operation):
                            client.subscribe(self._topic(device.name, f"set/{operation}"), qos=1)
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._broker_connected)

        def on_message(_client, _userdata, message):
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(
                    self._enqueue_command, message.topic, bytes(message.payload), bool(message.retain)
                )

        def on_disconnect(_client, _userdata, _flags, _reason_code, _properties):
            if self._loop and not self._loop.is_closed():
                def clear_connection():
                    self._connected.clear()
                    while not self._commands.empty():
                        self._commands.get_nowait()
                self._loop.call_soon_threadsafe(clear_connection)

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect
        updates = self.service.subscribe()
        loop_started = False
        try:
            await self.service.start()
            client.connect_async(self.host, self.port, keepalive=60)
            client.loop_start()
            loop_started = True
            await asyncio.wait_for(self._connected.wait(), timeout=30)
            while True:
                update_task = asyncio.create_task(updates.get())
                command_task = asyncio.create_task(self._commands.get())
                done, pending = await asyncio.wait({update_task, command_task}, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if update_task in done and self._connected.is_set():
                    self._publish_snapshot(update_task.result())
                if command_task in done:
                    await self._handle_command(*command_task.result())
        finally:
            self.service.unsubscribe(updates)
            try:
                if loop_started and self._connected.is_set():
                    info = client.publish(self._topic("bridge", "availability"), "offline", qos=1, retain=True)
                    await asyncio.to_thread(info.wait_for_publish, timeout=2)
            finally:
                try:
                    if loop_started:
                        client.disconnect()
                        client.loop_stop()
                finally:
                    await self.service.stop()
