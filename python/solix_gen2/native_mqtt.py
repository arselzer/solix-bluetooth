"""Decode native station MQTT telemetry without Bluetooth or cloud access."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json

from .protocol import DATA_RESPONSE, Model, decode_telemetry, parse_packet


@dataclass(frozen=True)
class MqttTelemetry:
    """Power readings and original fields, which may contain device identifiers."""

    metrics: dict[str, int | str]
    raw_tlvs: dict[int, bytes]


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
