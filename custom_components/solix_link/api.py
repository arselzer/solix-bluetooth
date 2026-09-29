"""Small async client for the local gateway; no Anker or Bluetooth access."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

COMMANDS = frozenset({"set-charge-power", "set-charge-cap", "set-backup-reserve",
                      "set-tou-plan", "return-grid"})
METRICS = frozenset({"battery_percentage", "temperature_c", "output_power_w",
                    "ac_input_power_w", "ac_output_power_w", "dc_output_power_w",
                    "ac_input_connected", "ac_output_enabled", "battery_status",
                    "ac_charging_power_limit_w", "max_charge_percentage",
                    "min_charge_percentage", "backup_reserve_percentage",
                    "active_tariff", "usage_mode", "tou_schedule_slot_count",
                    "ac_fast_charge_enabled", "software_version"})
POWER_MINIMUM = {"c1000": 100, "c1000_gen2": 300, "c2000_gen2": 300}
POWER_MAXIMUM = {"c1000": 1000, "c1000_gen2": 1200, "c2000_gen2": 1800}
CHARGE_CAP_MODELS = frozenset({"c1000_gen2", "c2000_gen2"})


class GatewayError(Exception):
    """Gateway communication or response validation failed."""


class GatewayAuthError(GatewayError):
    """Gateway rejected authentication."""


class GatewayCommandError(GatewayError):
    """A command failed; its settings may already have changed."""


def normalize_url(value: str) -> str:
    """Normalize an endpoint without accepting credentials or URL parameters."""
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("Enter an HTTP or HTTPS gateway URL")
    parts = urlsplit(value.strip())
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment):
        raise ValueError("Enter a gateway URL without credentials, query or fragment")
    port = parts.port  # Reject malformed/out-of-range ports.
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parts.scheme == "https" else 80):
        host += f":{port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


def gateway_id(url: str) -> str:
    """Identify the configured endpoint without an account or station serial."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def device_id(endpoint_id: str, name: str) -> str:
    """Keep names containing punctuation from colliding with each other."""
    return hashlib.sha256(f"{endpoint_id}\0{name}".encode()).hexdigest()


def numeric(value: Any) -> int | float | None:
    if type(value) in (int, float) and math.isfinite(value):
        return value
    return None


def binary_state(value: Any) -> bool | None:
    """Only explicit decoded 0/1 states establish mains/output presence."""
    return bool(value) if type(value) is int and value in (0, 1) else None


def snapshot_available(snapshot: dict, max_age: float = 90, now: float | None = None) -> bool:
    seen = numeric(snapshot.get("last_seen_timestamp"))
    if seen is None or not snapshot.get("connected") or not snapshot.get("available"):
        return False
    age = (time.time() if now is None else now) - seen
    return -5 <= age <= max_age


def parse_snapshot(value: Any) -> dict:
    """Validate the gateway contract and discard identity/raw diagnostic fields."""
    if not isinstance(value, dict):
        raise GatewayError("Gateway returned an invalid device")
    for field in ("name", "model", "protocol"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise GatewayError("Gateway returned an invalid device identity")
    if any(type(value.get(field)) is not bool for field in ("connected", "available")):
        raise GatewayError("Gateway returned invalid availability")
    if not isinstance(value.get("metrics"), dict):
        raise GatewayError("Gateway returned invalid telemetry")
    controls = value.get("controls", [])
    if not isinstance(controls, list) or any(not isinstance(item, str) for item in controls):
        raise GatewayError("Gateway returned invalid controls")
    result = {key: value[key] for key in ("name", "model", "protocol", "connected", "available")}
    result["last_seen_timestamp"] = numeric(value.get("last_seen_timestamp"))
    result["controls"] = sorted(set(controls) & COMMANDS)
    result["metrics"] = {key: metric for key, metric in value["metrics"].items()
                         if key in METRICS and (numeric(metric) is not None or isinstance(metric, str))}
    if value.get("power_flow") in ("unknown", "battery", "grid", "transitioning"):
        result["power_flow"] = value["power_flow"]
    return result


def integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def validate_plan(periods: Any, enabled: Any) -> list[dict]:
    if type(enabled) is not bool or not isinstance(periods, list) or len(periods) > 6:
        raise ValueError("Use a boolean enabled value and at most six periods")
    if enabled and not periods:
        raise ValueError("An enabled schedule needs at least one period")
    result = []
    for period in periods:
        if not isinstance(period, dict) or set(period) != {"tariff", "start_hour", "end_hour"}:
            raise ValueError("Each period needs tariff, start_hour and end_hour")
        tariff = period["tariff"]
        if not isinstance(tariff, str) or tariff not in ("peak", "mid_peak", "off_peak"):
            raise ValueError("Tariff must be peak, mid_peak or off_peak")
        start = integer(period["start_hour"], "Start hour")
        end = integer(period["end_hour"], "End hour")
        if not 0 <= start < end <= 24:
            raise ValueError("Use whole hours 0 <= start < end <= 24; split overnight periods")
        result.append(dict(period))
    ordered = sorted(result, key=lambda item: item["start_hour"])
    if any(left["end_hour"] > right["start_hour"] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Schedule periods must not overlap")
    return result


def validate_command(snapshot: dict, payload: dict) -> None:
    """Validate known settings against fresh telemetry before any POST."""
    command = payload.get("command")
    if not isinstance(command, str) or command not in COMMANDS or command not in snapshot["controls"]:
        raise ValueError("This control is not enabled by the gateway")
    if not snapshot_available(snapshot, 30):
        raise ValueError("Fresh, connected telemetry is required for controls")
    metrics = snapshot["metrics"]
    model = snapshot["model"]
    expected = {
        "set-charge-power": {"command", "watts"},
        "set-charge-cap": {"command", "upper"},
        "set-backup-reserve": {"command", "reserve"},
        "set-tou-plan": {"command", "periods", "enabled"},
        "return-grid": {"command", "timeout"},
    }[command]
    if set(payload) != expected:
        raise ValueError("Unexpected command fields")
    if command == "set-charge-power":
        watts = integer(payload["watts"], "Charging power")
        if (model not in POWER_MAXIMUM
                or not POWER_MINIMUM[model] <= watts <= POWER_MAXIMUM[model] or watts % 100):
            raise ValueError("Charging power is outside the validated model range")
        if numeric(metrics.get("ac_charging_power_limit_w")) is None:
            raise ValueError("Charging-power telemetry is missing")
    elif command == "set-charge-cap":
        upper = integer(payload["upper"], "Charge cap")
        if model not in CHARGE_CAP_MODELS or upper not in (80, 85, 90, 95, 100):
            raise ValueError("Charge cap must be 80–100 percent in steps of five")
        if numeric(metrics.get("max_charge_percentage")) is None:
            raise ValueError("Charge-cap telemetry is missing")
        reserve = numeric(metrics.get("backup_reserve_percentage"))
        if model == "c2000_gen2" and (reserve is None or upper < reserve):
            raise ValueError("Charge cap must preserve the reported backup reserve")
    elif command == "set-backup-reserve":
        reserve = integer(payload["reserve"], "Backup reserve")
        lower, upper = (numeric(metrics.get(key)) for key in ("min_charge_percentage", "max_charge_percentage"))
        if (model != "c2000_gen2" or lower is None or upper is None
                or numeric(metrics.get("backup_reserve_percentage")) is None
                or reserve % 5 or not 5 <= reserve <= 100 or not lower + 5 <= reserve <= upper):
            raise ValueError("Reserve must be within current caps, in steps of five")
    elif command == "set-tou-plan":
        if model != "c2000_gen2" or snapshot["protocol"] != "native_mqtt":
            raise ValueError("Time-of-Use control requires C2000 Gen 2 native MQTT")
        validate_plan(payload["periods"], payload["enabled"])
        if metrics.get("ac_fast_charge_enabled") != 0:
            raise ValueError("Fast charge must be off before changing Time-of-Use")
    elif command == "return-grid":
        if model != "c2000_gen2" or snapshot["protocol"] != "native_mqtt":
            raise ValueError("Return to grid requires C2000 Gen 2 native MQTT")
        if integer(payload["timeout"], "Timeout") != 30:
            raise ValueError("Return-to-grid timeout must be 30 seconds")
    if command in ("set-tou-plan", "return-grid"):
        if metrics.get("ac_output_enabled") != 1 or metrics.get("ac_input_connected") != 1:
            raise ValueError("Enabled AC output and connected mains must be reported")
        if any(numeric(metrics.get(key)) is None for key in (
            "battery_percentage", "max_charge_percentage", "min_charge_percentage", "backup_reserve_percentage"
        )):
            raise ValueError("Complete battery and charge-limit telemetry is required")


def request_deadline(method: str, payload: dict | None = None) -> int:
    """Allow the worker budget, its five-second RPC margin, and HTTP overhead.

    Match gateway lab_service.control_timeout without importing gateway/BLE
    dependencies into HA. This deadline bounds waiting, not device execution:
    connection failure or expiry cannot establish whether settings changed.
    """
    if method != "POST":
        return 10
    payload = payload or {}
    command = payload.get("command")
    budget = 45
    if command == "return-grid":
        duration = integer(payload.get("timeout", 30), "Timeout")
        if not 5 <= duration <= 120:
            raise ValueError("Return-to-grid timeout is outside the gateway range")
        budget = 2 * duration + 100
    elif command == "set-tou-plan":
        budget = 120
    return budget + 5 + 10


class GatewayClient:
    """Use Home Assistant's shared session; never log bearer tokens or bodies."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str = "") -> None:
        self.session = session
        self.url = normalize_url(url)
        self.token = token.strip()
        if any(ord(char) < 32 for char in self.token):
            raise ValueError("Invalid gateway token")

    async def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with self.session.request(method, self.url + path, json=payload,
                                            headers=headers, allow_redirects=False,
                                            timeout=aiohttp.ClientTimeout(
                                                total=request_deadline(method, payload), sock_connect=10
                                            )) as response:
                if response.status == 401 or (response.status == 403 and method == "GET"):
                    raise GatewayAuthError("Gateway authentication failed")
                if response.status != 200:
                    if method == "POST":
                        if response.status == 504:
                            raise GatewayCommandError("Power flow was not confirmed; settings may have changed. Refresh status before retrying.")
                        raise GatewayCommandError(
                            f"Gateway command failed (HTTP {response.status}); settings may have changed. Refresh status before retrying."
                        )
                    raise GatewayError(f"Gateway request failed (HTTP {response.status})")
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            if method == "POST":
                raise GatewayCommandError("Command result is unknown; settings may have changed. Refresh status before retrying.") from err
            raise GatewayError("Unable to read the gateway") from err

    async def async_devices(self) -> dict[str, dict]:
        data = await self._request("GET", "/devices")
        if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
            raise GatewayError("Gateway returned an invalid device list")
        result = {}
        for item in data["devices"]:
            snapshot = parse_snapshot(item)
            if snapshot["name"] in result:
                raise GatewayError("Gateway device names must be unique")
            result[snapshot["name"]] = snapshot
        return result

    async def async_device(self, name: str) -> dict:
        snapshot = parse_snapshot(await self._request("GET", f"/devices/{quote(name, safe='')}"))
        if snapshot["name"] != name:
            raise GatewayError("Gateway returned a different device")
        return snapshot

    async def async_command(self, name: str, payload: dict) -> dict:
        if not self.token:
            raise GatewayAuthError("A gateway token is required for controls")
        snapshot = await self.async_device(name)
        validate_command(snapshot, payload)
        raw = await self._request("POST", f"/devices/{quote(name, safe='')}/commands", payload)
        try:
            result = parse_snapshot(raw)
        except GatewayError as err:
            raise GatewayCommandError("Invalid command response; settings may have changed. Refresh status before retrying.") from err
        if result["name"] != name:
            raise GatewayCommandError("Gateway returned a different device; command result is unknown")
        return result
