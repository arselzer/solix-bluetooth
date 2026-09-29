"""HTTP/SSE telemetry with optional authenticated, allowlisted controls."""

from __future__ import annotations

import asyncio
import json
import os
import re
import hmac

from aiohttp import web

from .manager import MonitorService
from .commands import validate_command
from .tou import PowerFlowTimeout


def create_app(service: MonitorService, token: str | None = None, *, allow_control: bool = False) -> web.Application:
    """Create an aiohttp application without starting the BLE monitors."""

    if allow_control and not token:
        raise ValueError("HTTP controls require SOLIX_HTTP_TOKEN or an explicit Bearer token")

    @web.middleware
    async def authorize(request: web.Request, handler):
        if token and not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer"})
        return await handler(request)

    app = web.Application(middlewares=[authorize], client_max_size=16384)

    def status_with_controls(status: dict) -> dict:
        commands = getattr(service, "supported_commands", None)
        return {**status, "controls": commands(status["name"]) if allow_control and commands else []}

    def snapshots() -> list[dict]:
        return [status_with_controls(status) for status in service.snapshots()]

    async def health(_request: web.Request) -> web.Response:
        devices = service.snapshots()
        ok = any(device["available"] for device in devices)
        return web.json_response({"ok": ok, "devices": len(devices), "available": sum(d["available"] for d in devices)}, status=200 if ok else 503)

    async def all_devices(_request: web.Request) -> web.Response:
        return web.json_response({"devices": snapshots()})

    async def one_device(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in service.devices:
            raise web.HTTPNotFound(text="Unknown device")
        return web.json_response(status_with_controls(service.snapshot(name)))

    async def command(request: web.Request) -> web.Response:
        if not allow_control:
            return web.json_response({"error": "ControlsDisabled"}, status=403)
        name = request.match_info["name"]
        if name not in service.devices:
            raise web.HTTPNotFound(text="Unknown device")
        try:
            body = await request.json()
            if not isinstance(body, dict) or "command" not in body:
                raise ValueError("Invalid command body")
            action = body.pop("command")
            validate_command(action, body)
        except (ValueError, UnicodeError):
            return web.json_response({"error": "InvalidCommand", "settings_may_have_changed": False}, status=400)
        if action not in service.supported_commands(name):
            return web.json_response({"error": "UnsupportedCommand", "settings_may_have_changed": False}, status=403)
        try:
            result = await service.command(name, action, **body)
            return web.json_response(status_with_controls(result))
        except PowerFlowTimeout as error:
            failed_status = error.snapshot if error.snapshot.get("name") == name else service.snapshot(name)
            return web.json_response({"error": "PowerFlowTimeout", "settings_may_have_changed": True,
                                      "device": status_with_controls(failed_status)}, status=504)
        except (ValueError, PermissionError, ConnectionError, TimeoutError, RuntimeError) as error:
            code = 400 if isinstance(error, ValueError) else 403 if isinstance(error, PermissionError) else 504 if isinstance(error, TimeoutError) else 409
            return web.json_response({"error": type(error).__name__, "settings_may_have_changed": not isinstance(error, PermissionError),
                                      "device": status_with_controls(service.snapshot(name))}, status=code)

    async def events(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        queue = service.subscribe()
        try:
            initial = json.dumps({"devices": snapshots()}, separators=(",", ":"))
            await response.write(f"event: snapshot\ndata: {initial}\n\n".encode())
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                payload = json.dumps(status_with_controls(event), separators=(",", ":"))
                await response.write(f"event: update\ndata: {payload}\n\n".encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            service.unsubscribe(queue)
        return response

    async def metrics(_request: web.Request) -> web.Response:
        lines = []
        for status in service.snapshots():
            name = status["name"].replace("\\", "\\\\").replace('"', '\\"')
            label = f'{{device="{name}"}}'
            lines.append(f"solix_gen2_available{label} {int(status['available'])}")
            if status["last_seen_timestamp"] is not None:
                lines.append(f"solix_gen2_last_seen_timestamp_seconds{label} {status['last_seen_timestamp']}")
            for key, value in status["metrics"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metric = re.sub(r"[^a-zA-Z0-9_]", "_", key)
                    lines.append(f"solix_gen2_{metric}{label} {value}")
        return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")

    async def startup(_app: web.Application) -> None:
        await service.start()

    async def cleanup(_app: web.Application) -> None:
        await service.stop()

    app.router.add_get("/health", health)
    app.router.add_get("/devices", all_devices)
    app.router.add_get("/devices/{name}", one_device)
    app.router.add_post("/devices/{name}/commands", command)
    app.router.add_get("/events", events)
    app.router.add_get("/metrics", metrics)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def run_server(service: MonitorService, host: str = "127.0.0.1", port: int = 8765, *, allow_control: bool = False) -> None:
    web.run_app(create_app(service, os.environ.get("SOLIX_HTTP_TOKEN"), allow_control=allow_control), host=host, port=port)
