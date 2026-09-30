"""Native station MQTT request framing and telemetry decoding."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import json
import re
import secrets
import time

from .protocol import DATA_REQUEST, DATA_RESPONSE, Model, build_packet, decode_telemetry, parse_packet, tlv
from .tou import TouPeriod, validate_periods


@dataclass(frozen=True)
class MqttTelemetry:
    """Power readings and original fields, which may contain device identifiers."""

    metrics: dict[str, int | str]
    raw_tlvs: dict[int, bytes]


@dataclass(frozen=True)
class NativeMqttRequest:
    """A nonretained publish; topic and payload contain private identifiers."""

    topic: str = field(repr=False)
    payload: str = field(repr=False)
    response_command: str


@dataclass
class NativeMqttCommands:
    """Build Gen 2 native requests; callers own publishing and confirmation.

    Requires a provisioned station and the configured account ID. These methods
    do not contact Anker, connect to a broker, or determine whether a write took
    effect. Publish with retain=False and confirm settings in fresh telemetry.
    """

    device_serial: str = field(repr=False)
    account_id: str = field(repr=False)
    model: Model = Model.C2000_GEN2
    _session_id: str = field(default_factory=lambda: secrets.token_hex(8), init=False, repr=False)
    _sequence: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model = Model(self.model)
        if self.model not in (Model.C1000_GEN2, Model.C2000_GEN2):
            raise ValueError("Native MQTT commands support C1000 Gen 2 and C2000 Gen 2 only")
        if not isinstance(self.device_serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.device_serial):
            raise ValueError("Invalid native MQTT device serial")
        if not isinstance(self.account_id, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", self.account_id):
            raise ValueError("Native MQTT account ID must be 40 hexadecimal characters")

    def status(self) -> NativeMqttRequest:
        """Request a single 0900 telemetry reply."""
        return self._request("0100", b"", milliseconds=False)

    @property
    def product(self) -> str:
        return "A1763" if self.model == Model.C1000_GEN2 else "A1783"

    def readiness(self) -> NativeMqttRequest:
        """Read controller readiness (0089/0889); opaque fields stay private."""
        return self._request("0089", b"", milliseconds=False)

    def stream(self, seconds: int = 60) -> NativeMqttRequest:
        """Request a bounded telemetry stream; renew explicitly if needed."""
        if type(seconds) is not int or not 1 <= seconds <= 120:
            raise ValueError("Stream duration must be an integer from 1 to 120 seconds")
        fields = tlv(0xA2, b"\x01\x01") + tlv(0xA3, b"\x03" + seconds.to_bytes(4, "little"))
        return self._request("0057", fields, milliseconds=False)

    def ac_charging_power(self, watts: int) -> NativeMqttRequest:
        """Set the charging-power limit; does not include an AC output switch."""
        maximum = 1200 if self.model == Model.C1000_GEN2 else 1800
        if type(watts) is not int or not 300 <= watts <= maximum or watts % 100:
            raise ValueError(f"Charging power must be 300–{maximum} W in 100 W steps")
        fields = tlv(0xA4, b"\x02" + watts.to_bytes(2, "little"))
        return self._request("0101", fields, milliseconds=True)

    def charge_cap(self, percentage: int) -> NativeMqttRequest:
        """Set only the upper charge limit; omit the lower-limit field entirely."""
        if type(percentage) is not int or percentage not in (80, 85, 90, 95, 100):
            raise ValueError("Charge cap must be 80–100 percent in 5 percent steps")
        return self._request("0103", tlv(0xAA, bytes((1, percentage))), milliseconds=True)

    def backup_reserve(self, percentage: int) -> NativeMqttRequest:
        """Set only backup reserve; callers must check upper/lower limits first."""
        if type(percentage) is not int or not 5 <= percentage <= 100 or percentage % 5:
            raise ValueError("Backup reserve must be 5–100 percent in 5 percent steps")
        return self._request("0090", tlv(0xA5, bytes((1, percentage))), milliseconds=True)

    def tou_plan(self, periods: tuple[TouPeriod, ...], *, enabled: bool = False) -> NativeMqttRequest:
        """Store a plan in Standard by default, or explicitly activate Time-of-Use.

        A6 carries the count. A7 has type04 plus triplets, with no second count.
        This changes mode/schedule only; it never includes an output switch.
        """
        periods = validate_periods(periods)
        if type(enabled) is not bool or (enabled and not periods):
            raise ValueError("Enabled Time-of-Use requires a nonempty schedule")
        fields = (tlv(0xA2, bytes((1, int(enabled)))) + tlv(0xA3, b"\x01\x00")
                  + tlv(0xA4, b"\x01\x00") + tlv(0xA6, bytes((1, len(periods))))
                  + tlv(0xA7, b"\x04" + (b"".join(p.to_bytes() for p in periods) or b"\x00")))
        return self._request("0090", fields, milliseconds=True)

    def _request(self, command: str, fields: bytes, *, milliseconds: bool) -> NativeMqttRequest:
        now = time.time()
        timestamp = (tlv(0xFD, b"\x00" + str(int(now * 1000)).encode("ascii"))
                     if milliseconds else tlv(0xFE, b"\x03" + int(now).to_bytes(4, "little")))
        frame = build_packet(DATA_REQUEST, bytes.fromhex(command), tlv(0xA1, b"\x22") + fields + timestamp)
        self._sequence += 1
        envelope = {
            "head": {
                "version": "1.0.0.1", "client_id": "solix-local-research",
                "sess_id": self._session_id, "msg_seq": self._sequence,
                "seed": 1, "timestamp": int(now), "cmd_status": 2, "cmd": 17,
                "sign_code": 1, "device_pn": self.product, "device_sn": self.device_serial,
            },
            "payload": json.dumps({
                "device_sn": self.device_serial, "account_id": self.account_id,
                "data": base64.b64encode(frame).decode("ascii"),
            }, separators=(",", ":")),
        }
        return NativeMqttRequest(
            topic=f"cmd/anker_power/{self.product}/{self.device_serial}/req",
            payload=json.dumps(envelope, separators=(",", ":")),
            response_command=f"{int(command, 16) | 0x0800:04x}",
        )


def decode_mqtt_telemetry(
    message: str | bytes,
    *,
    model: Model,
    expected_serial: str | None = None,
) -> MqttTelemetry | None:
    """Decode an unencrypted native MQTT ``param_info``/response envelope.

    The outer JSON contains a JSON string in ``payload``; its ``data`` field
    holds a Base64 SOLIX packet. This path was verified on C2000 main 2.1.6.4.
    Returns None for non-telemetry messages and other devices. Malformed data
    raises ValueError without including message contents. This does not connect
    to a broker, provision a station, or establish freshness/availability.
    """
    if len(message) > 131072:
        raise ValueError("MQTT message is too large")
    try:
        envelope = json.loads(message)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), str):
            raise ValueError
        payload = json.loads(envelope["payload"])
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("Invalid native MQTT envelope") from None
    if "data" not in payload:
        return None
    if not isinstance(payload.get("sn"), str) or not isinstance(payload.get("pn"), str):
        raise ValueError("Missing native MQTT device identity")
    product = {Model.C1000_GEN2: "A1763", Model.C2000_GEN2: "A1783"}[Model(model)]
    if payload["pn"] != product or (expected_serial is not None and payload["sn"] != expected_serial):
        return None
    if payload.get("encoding_type", 0) != 0:
        raise ValueError("Encrypted native MQTT payload is unsupported")
    if not isinstance(payload["data"], str):
        raise ValueError("Invalid native MQTT packet encoding")
    try:
        packet = parse_packet(base64.b64decode(payload["data"], validate=True))
    except (ValueError, binascii.Error):
        raise ValueError("Invalid native MQTT packet") from None
    if packet.pattern != DATA_RESPONSE:
        return None
    body = packet.payload
    if packet.command == bytes.fromhex("0900"):
        if not body or body[0] != 0:
            raise ValueError("Native MQTT status request failed")
        body = body[1:]
    elif packet.command != bytes.fromhex("0421"):
        return None
    metrics, raw_tlvs = decode_telemetry(body, model=Model(model))
    return MqttTelemetry(metrics=metrics, raw_tlvs=raw_tlvs)
