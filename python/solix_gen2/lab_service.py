"""Local HTTP credential bootstrap, NTP and MQTT services inside the AP namespace."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import struct
import time

from .lab_config import LabConfig, load_lab, private_write
from .mqtt_intercept import LocalMqttServer
from .protocol import parse_tlvs, timezone_confer


def api_response(path: str, request: dict, config: LabConfig, credentials: bytes) -> tuple[bytes, bool]:
    """Return the minimal observed device API schema, never forwarding requests."""
    # The radio concatenates a trailing-slash base URL with a leading-slash path.
    path = "/" + path.lstrip("/")
    if not isinstance(request, dict) or request.get("device_sn", config.device_serial) != config.device_serial:
        raise ValueError("Unexpected device identity")
    if path == "/equipment/devicemanage/get_mqtt_info":
        return credentials, True
    if path in ("/equipment/devicerelation/bind_device", "/equipment/devicerelation/check_relate_bind_device"):
        data = {"device_sn": config.device_serial, "account": request.get("account", ""),
                "is_bind": True, "is_relate": True, "bind": True, "relate": True,
                "bind_status": 1, "relate_status": 1, "status": 1, "result": True,
                "bind_state": 1, "relate_state": 1}
    elif path == "/equipment/help/dst":
        data = {"timezone": timezone_confer(config.timezone_name)[1].decode()}
    elif path in ("/equipment/devicemanage/update_info", "/equipment/agreement/get_device_point_switch"):
        data = {}
    else:
        raise ValueError("Unsupported local API path")
    return json.dumps({"code": 0, "msg": "success", "data": data, "trace_id": ""}).encode(), False


def http_reply(body: bytes, *, credentials: bool = False, status: int = 200) -> bytes:
    """Only the large certificate response uses the verified short-read workaround."""
    header = f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\nContent-Type: application/json\r\nConnection: close\r\n"
    if credentials:
        return (header + "Transfer-Encoding: chunked\r\n\r\n").encode() + b"".join(
            b"1\r\n" + bytes([value]) + b"\r\n" for value in body) + b"0\r\n\r\n"
    return (header + f"Content-Length: {len(body)}\r\n\r\n").encode() + body


def ntp_reply(request: bytes, now: float) -> bytes | None:
    if len(request) != 48 or request[0] & 7 != 3 or (request[0] >> 3) & 7 not in (3, 4):
        return None
    def timestamp(value: float) -> bytes:
        return struct.pack("!II", int(value) + 2208988800, int(value % 1 * 2**32))
    reply = bytearray(48)
    reply[:4] = bytes([(request[0] & 0x38) | 4, 2, request[2], 0xEC])
    reply[4:12] = struct.pack("!II", 1 << 16, 1 << 16)
    reply[12:16] = b"LOCL"
    reply[16:24] = timestamp(now - 1)
    reply[24:32] = request[40:48]
    reply[32:40] = timestamp(now)
    reply[40:48] = timestamp(now)
    return bytes(reply)


class _Ntp(asyncio.DatagramProtocol):
    def __init__(self, mqtt: LocalMqttServer) -> None:
        self.mqtt = mqtt

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address) -> None:
        reply = ntp_reply(data, time.time())
        if reply is not None:
            self.transport.sendto(reply, address)
            self.mqtt.record("ntp_served")


class InterceptService:
    """Own device-facing services. Use only on the isolated AP interface.

    The Unix control socket is owner-only. Network-facing HTTP emulates the
    station bootstrap API; it does not expose charging controls to the station.
    """

    def __init__(self, config: LabConfig, directory: Path, *, allow_control: bool = False, callback=None) -> None:
        self.config, self.directory = config, directory
        self.mqtt = LocalMqttServer(config, directory, allow_control=allow_control, callback=callback)
        self.credentials = (directory / "mqtt-response.json").read_bytes()
        self._servers: list[asyncio.Server] = []
        self._transport = None
        self._clients: set[asyncio.Task] = set()
        self._heartbeat: asyncio.Task | None = None
        self.socket_path = directory / "control.sock"
        self._socket_owned = False
        self._ready_owned = False

    async def start(self, *, api_port: int = 80, mqtt_port: int = 8883, ntp_port: int = 123) -> None:
        # Never unlink a potentially active worker's socket.
        if self.socket_path.exists():
            raise RuntimeError("Existing lab control socket; stop or recover the previous worker first")
        try:
            await self.mqtt.start(port=mqtt_port)
            self._servers.append(await asyncio.start_server(self._api, self.config.gateway, api_port, limit=16384))
            self._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Ntp(self.mqtt), local_addr=(self.config.gateway, ntp_port))
            self._servers.append(await asyncio.start_unix_server(self._control, path=self.socket_path, limit=4096))
            self._socket_owned = True
            os.chmod(self.socket_path, 0o600)
            self._heartbeat = asyncio.create_task(self._refresh())
            private_write(self.directory / "ready", "ready\n")
            self._ready_owned = True
        except BaseException:
            await self.stop()
            raise

    async def _refresh(self) -> None:
        while True:
            self.mqtt.changed()
            await asyncio.sleep(5)

    async def _api(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._clients.add(task)
        try:
            if len(self._clients) > 8:
                return
            async with asyncio.timeout(10):
                headers = await reader.readuntil(b"\r\n\r\n")
                lines = headers.decode("ascii").split("\r\n")
                method, path, version = lines[0].split()
                if method != "POST" or version not in ("HTTP/1.0", "HTTP/1.1"):
                    raise ValueError("Unsupported HTTP request")
                fields = {}
                for line in lines[1:]:
                    if line:
                        key, value = line.split(":", 1)
                        key = key.lower()
                        if key in fields:
                            raise ValueError("Duplicate HTTP header")
                        fields[key] = value.strip()
                if "transfer-encoding" in fields:
                    raise ValueError("Chunked requests are unsupported")
                length = int(fields.get("content-length", "0"))
                if not 0 <= length <= 16384:
                    raise ValueError("Request too large")
                body = await reader.readexactly(length)
                self.mqtt.record("api_request", headers_hex=headers.hex(), body_hex=body.hex())
                response, chunked = api_response(path, json.loads(body or b"{}"), self.config, self.credentials)
                writer.write(http_reply(response, credentials=chunked))
                await writer.drain()
                self.mqtt.record("api_response", path=path, size=len(response),
                                 framing="one_byte_chunks" if chunked else "content_length")
        except (ValueError, UnicodeError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
            writer.write(http_reply(b'{"code":400}', status=400))
            try:
                await writer.drain()
            except OSError:
                pass
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            self._clients.discard(task)

    async def _control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._clients.add(task)
        try:
            if len(self._clients) > 8:
                return
            async with asyncio.timeout(45):
                request = json.loads(await reader.readline())
                if not isinstance(request, dict):
                    raise ValueError("Invalid request")
                action = request.get("command")
                if action == "status":
                    result = self.mqtt.snapshot()
                elif action == "set-charge-power":
                    result = await self.mqtt.set_ac_charging_power(request.get("watts"))
                elif action == "readiness":
                    connection = self.mqtt.connection
                    if connection is None:
                        raise ConnectionError("Station is not connected")
                    reply = await connection.request(self.mqtt.commands.readiness())
                    fields = parse_tlvs(reply[1:])
                    # Opaque FD may contain an app identifier; never return it publicly.
                    result = {"controller_ready_prefix": fields.get(0xA1, b"").hex(),
                              "a2": fields.get(0xA2, b"").hex(), "a3": fields.get(0xA3, b"").hex()}
                else:
                    raise ValueError("Unsupported command")
                response = {"ok": True, "result": result}
        except (ValueError, OSError, TimeoutError, RuntimeError, asyncio.LimitOverrunError) as error:
            # Exception text is controlled by this package; avoid reflecting request data.
            response = {"ok": False, "error": type(error).__name__}
        finally:
            try:
                if "response" in locals():
                    writer.write(json.dumps(response).encode() + b"\n")
                    await writer.drain()
            except OSError:
                pass
            writer.close()
            self._clients.discard(task)

    async def stop(self) -> None:
        if self._ready_owned:
            (self.directory / "ready").unlink(missing_ok=True)
            self._ready_owned = False
        if self._heartbeat:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
        for server in self._servers:
            server.close()
            await server.wait_closed()
        self._servers.clear()
        if self._transport:
            self._transport.close()
        for task in list(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        await self.mqtt.stop()
        if self._socket_owned:
            self.socket_path.unlink(missing_ok=True)
            self._socket_owned = False


async def lab_request(directory: Path, command: str, **fields) -> dict:
    """Use the private Unix socket from the host namespace or another local process."""
    reader, writer = await asyncio.open_unix_connection(directory / "control.sock", limit=131072)
    try:
        writer.write(json.dumps({"command": command, **fields}).encode() + b"\n")
        await writer.drain()
        async with asyncio.timeout(45):
            response = json.loads(await reader.readline())
        if not response.get("ok"):
            raise RuntimeError(f"Lab request failed: {response.get('error', 'unknown')}")
        return response["result"]
    finally:
        writer.close()
        await writer.wait_closed()


async def _worker(directory: Path, allow_control: bool) -> None:
    service = InterceptService(load_lab(directory / "lab.json"), directory, allow_control=allow_control)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await service.start()
        await stop.wait()
    finally:
        await service.stop()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Device-facing services; run inside the isolated AP namespace")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--allow-control", action="store_true")
    args = parser.parse_args()
    asyncio.run(_worker(args.directory, args.allow_control))


if __name__ == "__main__":
    main()
