"""Read-only HTTP, SSE, and Prometheus endpoints for SOLIX telemetry."""

from __future__ import annotations

import asyncio
import json
import os
import re

from aiohttp import web

from .manager import MonitorService


def create_app(service: MonitorService, token: str | None = None) -> web.Application:
    """Create an aiohttp application without starting the BLE monitors."""

    @web.middleware
    async def authorize(request: web.Request, handler):
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer"})
        return await handler(request)

    app = web.Application(middlewares=[authorize])

    async def health(_request: web.Request) -> web.Response:
        devices = service.snapshots()
        ok = any(device["available"] for device in devices)
        return web.json_response({"ok": ok, "devices": len(devices), "available": sum(d["available"] for d in devices)}, status=200 if ok else 503)

    async def all_devices(_request: web.Request) -> web.Response:
        return web.json_response({"devices": service.snapshots()})

    async def one_device(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in service.devices:
            raise web.HTTPNotFound(text="Unknown device")
        return web.json_response(service.snapshot(name))

    async def events(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        queue = service.subscribe()
        try:
            initial = json.dumps({"devices": service.snapshots()}, separators=(",", ":"))
            await response.write(f"event: snapshot\ndata: {initial}\n\n".encode())
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                payload = json.dumps(event, separators=(",", ":"))
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
    app.router.add_get("/events", events)
    app.router.add_get("/metrics", metrics)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def run_server(service: MonitorService, host: str = "127.0.0.1", port: int = 8765) -> None:
    web.run_app(create_app(service, os.environ.get("SOLIX_HTTP_TOKEN")), host=host, port=port)
