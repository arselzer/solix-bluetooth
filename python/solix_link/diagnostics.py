"""Radio network error reports, separate from battery and inverter faults."""

FIELDS = {
    0xA1: ("system_reboot_code", 1),
    0xA2: ("sdk_reset_code", 1),
    0xA3: ("http_error_code", 4),
    0xA4: ("wifi_error_code", 4),
    0xA5: ("ble_disconnect_code", 4),
    0xA6: ("mqtt_error_code", 4),
}


def decode_wifi_rssi(payload: bytes) -> int | None:
    """Read raw signed RSSI from function 10/4822; status 01 is unavailable.

    The radio supplies a signed byte in a four-byte raw TLV. Reject other
    statuses, shapes and impossible source widths rather than returning a
    cached quality byte or interpreting failure as a signal measurement.
    """
    if payload == b"\x01":
        return None
    if len(payload) != 7 or payload[:3] != b"\x00\xa1\x04":
        raise ValueError("Invalid radio RSSI response")
    value = int.from_bytes(payload[3:], "little", signed=True)
    if not -128 <= value <= 127 or value == 0:
        raise ValueError("Invalid radio RSSI value")
    return value


def decode_network_diagnostics(payload: bytes) -> dict[str, int]:
    """Decode a successful 0f/4820 reply, rejecting incomplete reports.

    Error words are signed little-endian integers. Zero is a reported code,
    not proof of network connectivity. Reset bytes may be 255 after reading.
    """
    if not payload or payload[0] != 0:
        raise ValueError("Station did not return successful network diagnostics")
    result: dict[str, int] = {}
    position = 1
    while position < len(payload):
        if position + 2 > len(payload):
            raise ValueError("Truncated network diagnostic TLV")
        tag, length = payload[position:position + 2]
        position += 2
        if position + length > len(payload):
            raise ValueError("Truncated network diagnostic value")
        value = payload[position:position + length]
        position += length
        if tag in FIELDS:
            name, expected_length = FIELDS[tag]
            if length != expected_length or name in result:
                raise ValueError(f"Invalid network diagnostic field: {name}")
            result[name] = int.from_bytes(value, "little", signed=length == 4)
    if len(result) != len(FIELDS):
        raise ValueError("Incomplete network diagnostic report")
    return result
