"""Small local configuration file for the CLI and monitoring server."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from .protocol import Model, timezone_confer


DEFAULT_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "solix-gen2" / "config.json"
_ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    address: str
    model: Model
    client_id: str | None = None
    protocol: str = "prime"
    timezone_name: str | None = None

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError("Device name must contain only letters, digits, _ or -")
        if not _ADDRESS.fullmatch(self.address):
            raise ValueError(f"Invalid Bluetooth address: {self.address}")
        if self.client_id is not None and (
            len(self.client_id) != 40 or any(char not in "0123456789abcdefABCDEF" for char in self.client_id)
        ):
            raise ValueError("client_id must be 40 hexadecimal characters")
        if self.protocol not in ("prime", "legacy"):
            raise ValueError("protocol must be prime or legacy")
        if self.model == Model.C2000_GEN2 and self.protocol != "prime":
            raise ValueError("C2000 Gen 2 requires Prime protocol")
        timezone_confer(self.timezone_name)

    def as_dict(self) -> dict[str, str]:
        result = {"name": self.name, "address": self.address.upper(), "model": self.model.value, "protocol": self.protocol}
        if self.client_id:
            result["client_id"] = self.client_id
        if self.timezone_name:
            result["timezone_name"] = self.timezone_name
        return result


def load_config(path: Path = DEFAULT_CONFIG) -> list[DeviceConfig]:
    if not path.exists():
        return []
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or not isinstance(document.get("devices"), list):
        raise ValueError("Config must contain a devices list")
    devices = []
    for entry in document["devices"]:
        devices.append(DeviceConfig(
            name=entry["name"],
            address=entry["address"],
            model=Model(entry["model"]),
            client_id=entry.get("client_id", entry.get("owner_user_id")),
            protocol=entry.get("protocol", "prime"),
            timezone_name=entry.get("timezone_name"),
        ))
    if len({device.name for device in devices}) != len(devices):
        raise ValueError("Device names must be unique")
    return devices


def save_config(devices: list[DeviceConfig], path: Path = DEFAULT_CONFIG) -> None:
    """Atomically save local BLE client IDs with owner-only file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"devices": [device.as_dict() for device in devices]}, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
