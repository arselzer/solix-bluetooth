"""Radio network error reports, separate from battery and inverter faults."""

FIELDS = {
    0xA1: ("system_reboot_code", 1),
    0xA2: ("sdk_reset_code", 1),
    0xA3: ("http_error_code", 4),
    0xA4: ("wifi_error_code", 4),
    0xA5: ("ble_disconnect_code", 4),
    0xA6: ("mqtt_error_code", 4),
}


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
