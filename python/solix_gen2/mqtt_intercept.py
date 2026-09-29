"""Small, single-station MQTT 3.1.1 TLS endpoint for the isolated SOLIX lab.

This is a device protocol endpoint, not a general MQTT broker. It never bridges
traffic to Anker or another network and never sends an AC output command.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import ssl
import time
from typing import Callable

from .lab_config import LabConfig, private_write
from .native_mqtt import NativeMqttCommands, NativeMqttRequest, decode_mqtt_telemetry
from .protocol import DATA_RESPONSE, parse_packet


def mqtt_packet(first: int, body: bytes) -> bytes:
    remaining = len(body)
    if remaining > 131072:
        raise ValueError("MQTT packet too large")
    result = bytearray([first])
    while True:
        digit = remaining % 128
        remaining //= 128
        result.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes(result) + body


async def read_mqtt(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = (await reader.readexactly(1))[0]
    remaining = 0
    for shift in range(0, 28, 7):
        digit = (await reader.readexactly(1))[0]
        remaining |= (digit & 127) << shift
        if not digit & 128:
            break
    else:
        raise ValueError("Invalid MQTT remaining length")
    if remaining > 131072:
        raise ValueError("MQTT packet too large")
    return first, await reader.readexactly(remaining)


def mqtt_string(body: bytes, position: int) -> tuple[str, int]:
    if position + 2 > len(body):
        raise ValueError("Truncated MQTT string")
    size = int.from_bytes(body[position:position + 2], "big")
    end = position + 2 + size
    if end > len(body):
        raise ValueError("Truncated MQTT string")
    value = body[position + 2:end].decode("utf-8")
    if "\x00" in value:
        raise ValueError("Invalid MQTT string")
    return value, end


def native_response(message: bytes, config: LabConfig):
    """Validate device identity before accepting any acknowledgement."""
    try:
        outer = json.loads(message)
        if not isinstance(outer, dict) or not isinstance(outer.get("payload"), str):
            raise ValueError
        inner = json.loads(outer["payload"])
        if not isinstance(inner, dict):
            raise ValueError
        if inner.get("sn") != config.device_serial or inner.get("pn") != config.product:
            return None
        if inner.get("encoding_type", 0) != 0:
            raise ValueError
        frame = parse_packet(base64.b64decode(inner["data"], validate=True))
        return frame if frame.pattern == DATA_RESPONSE else None
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError("Invalid native response") from None


class LocalMqttServer:
    """Async native MQTT server with freshness tracking and telemetry callbacks.

    A status reply is required after every charging write. Timed-out requests
    close the connection, preventing a late ACK from satisfying another write.
    Device responses have their own sequence counter, so commands are serialized
    and matched by opcode within the current connection rather than msg_seq.
    """

    def __init__(self, config: LabConfig, directory: Path, *, allow_control: bool = False,
                 callback: Callable[[dict], None] | None = None) -> None:
        self.config = config
        self.directory = directory
        self.allow_control = allow_control
        self.callback = callback
        self.commands = NativeMqttCommands(config.device_serial, config.account_id)
        self.topic = self.commands.status().topic
        self.connection: _Connection | None = None
        self.metrics: dict = {}
        self.last_seen: float | None = None
        self.error: str | None = None
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self._clients: set[_Connection] = set()
        self._control_lock = asyncio.Lock()

    def snapshot(self) -> dict:
        connected = bool(self.connection and not self.connection.writer.is_closing())
        return {"name": self.config.name, "model": self.config.model.value, "protocol": "native_mqtt",
                "connected": connected,
                "available": bool(connected and self.last_seen and time.time() - self.last_seen < 30),
                "last_seen_timestamp": self.last_seen, "error": self.error, "metrics": self.metrics.copy()}

    def record(self, event: str, **fields) -> None:
        # Full protocol evidence is deliberately kept only in this private file.
        path = self.directory / "mqtt-events.jsonl"
        if path.is_symlink():
            raise ValueError("Capture file must not be a symlink")
        import os
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps({"time": time.time(), "event": event, **fields}) + "\n")

    def changed(self) -> None:
        snapshot = self.snapshot()
        private_write(self.directory / "status.json", json.dumps(snapshot))
        if self.callback:
            self.callback(snapshot)

    async def start(self, host: str | None = None, port: int = 8883) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.directory / "server.pem", self.directory / "server-key.pem")
        context.load_verify_locations(self.directory / "ca.pem")
        context.verify_mode = ssl.CERT_REQUIRED
        self._server = await asyncio.start_server(self._accept, host or self.config.gateway, port, ssl=context,
                                                 ssl_handshake_timeout=10, ssl_shutdown_timeout=3, limit=262144)
        self.changed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._clients) >= 4:
            writer.close()
            return
        connection = _Connection(self, reader, writer)
        tls = writer.get_extra_info("ssl_object")
        self.record("tls_connected", version=tls.version() if tls else None,
                    client_certificate=bool(tls and tls.getpeercert()))
        self._clients.add(connection)
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            await connection.run()
        except (OSError, ValueError, asyncio.IncompleteReadError, TimeoutError):
            self.record("connection_closed")
        finally:
            await connection.close()
            self._clients.discard(connection)
            self._tasks.discard(task)
            if self.connection is connection:
                self.connection = None
                self.error = "Disconnected"
                self.changed()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for connection in list(self._clients):
            await connection.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.connection = None
        self.changed()

    async def set_ac_charging_power(self, watts: int) -> dict:
        request = self.commands.ac_charging_power(watts)  # Validate before I/O.
        if not self.allow_control:
            raise PermissionError("Native charging control is disabled")
        async with self._control_lock:
            connection = self.connection
            if connection is None:
                raise ConnectionError("Station is not connected")
            await connection.request(self.commands.status())
            before = self.metrics.copy()
            if "ac_output_enabled" not in before:
                raise RuntimeError("Missing fresh AC-output baseline")
            await connection.request(request)
            await connection.request(self.commands.status())
            if self.metrics.get("ac_output_enabled") != before["ac_output_enabled"]:
                raise RuntimeError("AC-output state changed unexpectedly")
            if self.metrics.get("ac_charging_power_limit_w") != watts:
                raise RuntimeError("Charging-power setting not confirmed by telemetry")
            return self.snapshot()


class _Connection:
    def __init__(self, server: LocalMqttServer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.server, self.reader, self.writer = server, reader, writer
        self.connected = False
        self.subscribed = False
        self.pending: tuple[str, asyncio.Future] | None = None
        self.lock = asyncio.Lock()
        self.poller: asyncio.Task | None = None

    async def send(self, first: int, body: bytes) -> None:
        self.writer.write(mqtt_packet(first, body))
        await self.writer.drain()

    async def close(self) -> None:
        if self.poller and self.poller is not asyncio.current_task():
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        if self.pending and not self.pending[1].done():
            self.pending[1].set_exception(ConnectionError("Station disconnected"))
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except OSError:
            pass

    async def request(self, request: NativeMqttRequest, timeout: float = 12) -> bytes:
        async with self.lock:
            if self.writer.is_closing() or not self.subscribed:
                raise ConnectionError("Station command subscription is unavailable")
            future = asyncio.get_running_loop().create_future()
            self.pending = (request.response_command, future)
            topic = request.topic.encode()
            try:
                self.server.record("request", payload=request.payload)
                await self.send(0x30, len(topic).to_bytes(2, "big") + topic + request.payload.encode())
                response = await asyncio.wait_for(future, timeout)
                if not response or response[0] != 0:
                    raise RuntimeError("Station rejected native request")
                return response
            except (TimeoutError, asyncio.CancelledError):
                self.writer.close()
                raise
            finally:
                self.pending = None

    async def poll(self) -> None:
        try:
            while not self.writer.is_closing():
                await self.request(self.server.commands.status())
                await asyncio.sleep(5)
        except (OSError, TimeoutError, RuntimeError):
            self.writer.close()

    async def run(self) -> None:
        while not self.writer.is_closing():
            first, body = await asyncio.wait_for(read_mqtt(self.reader), timeout=120)
            kind, flags = first >> 4, first & 15
            self.server.record("packet", first=first, body_hex=body.hex())
            if kind == 1 and not self.connected and flags == 0:
                protocol, pos = mqtt_string(body, 0)
                if protocol != "MQTT" or pos + 4 > len(body) or body[pos] != 4:
                    raise ValueError("Only MQTT 3.1.1 is supported")
                connect_flags = body[pos + 1]
                if connect_flags & 1 or (connect_flags & 0x18) == 0x18 or (not connect_flags & 4 and connect_flags & 0x38) or (connect_flags & 0x40 and not connect_flags & 0x80):
                    raise ValueError("Invalid MQTT CONNECT flags")
                _client_id, end = mqtt_string(body, pos + 4)
                if connect_flags & 4:
                    _, end = mqtt_string(body, end)
                    # Will payload is binary; validate its length without UTF-8 decoding.
                    size = int.from_bytes(body[end:end + 2], "big")
                    end += 2 + size
                if connect_flags & 0x80:
                    _, end = mqtt_string(body, end)
                if connect_flags & 0x40:
                    size = int.from_bytes(body[end:end + 2], "big")
                    end += 2 + size
                if end != len(body):
                    raise ValueError("Malformed MQTT CONNECT")
                self.connected = True
                await self.send(0x20, b"\x00\x00")
            elif not self.connected:
                raise ValueError("MQTT CONNECT is required")
            elif kind == 8 and flags == 2:
                if len(body) < 5 or body[:2] == b"\x00\x00":
                    raise ValueError("Invalid MQTT subscription")
                pos, grants = 2, bytearray()
                allowed = False
                while pos < len(body):
                    topic, pos = mqtt_string(body, pos)
                    if pos >= len(body) or body[pos] > 2:
                        raise ValueError("Invalid subscription QoS")
                    # Accept only the one station request topic; no wildcard subscriptions.
                    accepted = topic == self.server.topic and body[pos] <= 1
                    grants.append(body[pos] if accepted else 0x80)
                    allowed |= accepted
                    pos += 1
                await self.send(0x90, body[:2] + grants)
                if allowed and not self.subscribed:
                    old = self.server.connection
                    if old and old is not self:
                        await old.close()
                    self.subscribed = True
                    self.server.connection = self
                    self.server.last_seen = None
                    self.server.error = None
                    self.server.changed()
                    self.poller = asyncio.create_task(self.poll())
            elif kind == 3:
                qos = (flags >> 1) & 3
                if qos > 1:
                    raise ValueError("Only MQTT QoS 0/1 is supported")
                topic, pos = mqtt_string(body, 0)
                if qos:
                    if pos + 2 > len(body) or body[pos:pos + 2] == b"\x00\x00":
                        raise ValueError("Invalid MQTT packet ID")
                    await self.send(0x40, body[pos:pos + 2])
                    pos += 2
                # Retained messages and another device cannot establish freshness or ACK a command.
                parts = topic.split("/")
                if flags & 1 or not self.subscribed or len(parts) < 4 or parts[1:4] != ["anker_power", self.server.config.product, self.server.config.device_serial]:
                    continue
                message = body[pos:]
                try:
                    frame = native_response(message, self.server.config)
                    if frame is None:
                        continue
                    update = decode_mqtt_telemetry(message, model=self.server.config.model,
                                                   expected_serial=self.server.config.device_serial)
                    if update:
                        self.server.metrics = {k: v for k, v in update.metrics.items() if k != "serial_number"}
                        self.server.last_seen = time.time()
                        self.server.error = None
                        self.server.changed()
                    if self.pending and frame.command.hex() == self.pending[0] and not self.pending[1].done():
                        self.pending[1].set_result(frame.payload)
                except ValueError:
                    self.server.record("decode_error")
            elif kind == 12 and flags == 0 and not body:
                await self.send(0xD0, b"")
            elif kind == 14 and flags == 0 and not body:
                return
            else:
                raise ValueError("Unsupported MQTT packet")
