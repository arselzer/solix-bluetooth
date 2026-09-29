"""Adapt namespace-worker snapshots to the existing read-only HTTP/SSE server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

from .lab_config import LabConfig
from .commands import NATIVE_COMMANDS, validate_command
from .lab_service import lab_request


class LabMonitorService:
    """Monitor-service interface backed by the private native MQTT status file."""

    def __init__(self, config: LabConfig, directory: Path) -> None:
        self.config, self.directory = config, directory
        self.devices = {config.name: config}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    def snapshot(self, name: str) -> dict:
        if name != self.config.name:
            raise KeyError(name)
        try:
            path = self.directory / "status.json"
            status = json.loads(path.read_text())
            fresh = time.time() - path.stat().st_mtime < 15
            latest = status.get("last_seen_timestamp")
            status["connected"] = bool(fresh and status.get("connected"))
            status["available"] = bool(status["connected"] and latest and time.time() - latest < 30)
            if not status["available"]:
                status["power_flow"] = "unknown"
            return status
        except (OSError, ValueError):
            return {"name": name, "model": self.config.model.value, "protocol": "native_mqtt",
                    "connected": False, "available": False, "last_seen_timestamp": None,
                    "error": "Lab status unavailable", "metrics": {}}

    def supported_commands(self, name: str) -> list[str]:
        return list(NATIVE_COMMANDS) if self.snapshot(name).get("control_enabled") else []

    async def command(self, name: str, command: str, **values) -> dict:
        validate_command(command, values)
        if command not in self.supported_commands(name):
            raise PermissionError("Native worker controls are disabled")
        if not self.snapshot(name)["available"]:
            raise ConnectionError("Fresh native telemetry is unavailable")
        return await lab_request(self.directory, command, **values)

    def snapshots(self) -> list[dict]:
        return [self.snapshot(self.config.name)]

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=20)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _poll(self) -> None:
        previous = None
        while True:
            status = self.snapshot(self.config.name)
            if status != previous:
                for queue in self._subscribers:
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(status)
                previous = status
            await asyncio.sleep(1)
