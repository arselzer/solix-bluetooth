"""Synthetic original-model framing; no captured identities or credentials."""

import base64
from dataclasses import replace
import json

import pytest

from solix_link import APServiceConfig, Model, NativeMqttCommands, initialize_ap_service, load_ap_service
from solix_link.ap_service import api_response, http_reply
from solix_link.mqtt_credentials import decrypt_device_credential, encrypt_device_credential
from solix_link.native_mqtt import decode_mqtt_telemetry
from solix_link.protocol import DATA_REQUEST, DATA_RESPONSE, build_packet, parse_packet, parse_tlvs, tlv


SERIAL = "A1761TEST0000001"
ACCOUNT = "a" * 40


def original_envelope(command="0405", *, serial=SERIAL, product="A1761", status=0):
    fields = (tlv(0xA1, b"\x34") + tlv(0xB3, b"\x02\xab\x00")
              + tlv(0xC1, b"\x01\x64") + tlv(0xD1, b"\x02\xe8\x03")
              + tlv(0xD2, b"\x02\xd0\x02") + tlv(0xD7, b"\x01\x01")
              + tlv(0xD8, b"\x01\x00")
              + tlv(0xF8, bytes((4, 2, 2, 1, 1, 0, 1)) + bytes(14)))
    if command == "0840":
        fields = bytes((status,)) + fields
    packet = build_packet(DATA_RESPONSE, bytes.fromhex(command), fields)
    return json.dumps({"head": {"cmd": 16}, "payload": json.dumps({
        "sn": serial, "pn": product, "data": base64.b64encode(packet).decode(),
    })})


def unpack(request):
    envelope = json.loads(request.payload)
    payload = json.loads(envelope["payload"])
    return envelope, payload, parse_packet(base64.b64decode(payload["data"]))


@pytest.mark.parametrize("command", ["0405", "0840"])
def test_original_typed_telemetry_and_identity_filter(command):
    decoded = decode_mqtt_telemetry(original_envelope(command), model=Model.C1000, expected_serial=SERIAL)
    assert decoded.metrics["software_version"] == "1.7.1"
    assert decoded.metrics["battery_percentage"] == 100
    assert decoded.metrics["ac_output_enabled"] == 1
    assert decoded.metrics["dc_output_enabled"] == 0
    assert decoded.metrics["ac_charging_power_limit_w"] == 1000
    assert decoded.metrics["device_timeout_minutes"] == 720
    assert decoded.metrics["ac_power_saving_mode_enabled"] == 1
    assert decoded.metrics["dc_power_saving_mode_enabled"] == 1
    assert len(decoded.raw_tlvs[0xF8]) == 21
    assert decode_mqtt_telemetry(original_envelope(command, serial="OTHER"), model=Model.C1000,
                                 expected_serial=SERIAL) is None
    assert decode_mqtt_telemetry(original_envelope(command, product="A1763"), model=Model.C1000) is None
    assert decode_mqtt_telemetry(original_envelope(command), model=Model.C1000_GEN2) is None


@pytest.mark.parametrize("command", ["0407", "0421", "0900", "0844", "0830"])
def test_original_rejects_network_versions_and_other_model_tag_aliases(command):
    assert decode_mqtt_telemetry(original_envelope(command), model=Model.C1000) is None


def test_original_full_status_requires_explicit_success():
    with pytest.raises(ValueError, match="status request failed"):
        decode_mqtt_telemetry(original_envelope("0840", status=1), model=Model.C1000)


def test_original_status_and_power_use_original_opcode_and_typed_fields(monkeypatch):
    monkeypatch.setattr("solix_link.native_mqtt.time.time", lambda: 1800000000.125)
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    status = commands.status()
    outer, inner, packet = unpack(status)
    assert status.topic == f"cmd/anker_power/A1761/{SERIAL}/req"
    assert status.response_command == "0840"
    assert status.response_aliases == ("0405",)
    assert packet.pattern == DATA_REQUEST and packet.command.hex() == "0040"
    assert packet.payload.hex() == "a10122fe050300d2496b"
    assert outer["head"]["device_pn"] == "A1761"
    assert outer["head"]["device_sn"] == inner["device_sn"] == SERIAL
    assert inner["account_id"] == ACCOUNT
    for watts in (100, 300, 1000):
        request = commands.ac_charging_power(watts)
        _, _, packet = unpack(request)
        assert request.response_command == "0844" and packet.command.hex() == "0044"
        assert request.response_aliases == ()
        fields = parse_tlvs(packet.payload)
        assert list(fields) == [0xA1, 0xA2, 0xFE]
        assert fields[0xA1] == b"\x22"
        assert fields[0xA2] == b"\x02" + watts.to_bytes(2, "little")
        assert fields[0xFE] == b"\x03\x00\xd2\x49\x6b"


@pytest.mark.parametrize("watts", [True, False, 0, 99, 150, 1100, 100.0, "100"])
def test_original_power_domain_rejects_invalid_values_before_request(watts):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    with pytest.raises(ValueError, match="100–1000"):
        commands.ac_charging_power(watts)
    assert commands._sequence == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_original_dc_smart_native_packet_has_typed_bool_and_no_status_alias(enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    request = commands.dc_power_saving(enabled)
    _, _, packet = unpack(request)
    assert packet.command.hex() == "0076" and request.response_command == "0876"
    assert request.response_aliases == ()
    fields = parse_tlvs(packet.payload)
    assert set(fields) == {0xA1, 0xA2, 0xFE}
    assert fields[0xA1] == b"\x22" and fields[0xA2] == bytes((1, int(enabled)))
    assert fields[0xFE][0] == 3 and len(fields[0xFE]) == 5


@pytest.mark.parametrize("enabled", [0, 1, None, "on", 0.0])
def test_original_dc_smart_rejects_non_boolean_without_constructing_request(enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    with pytest.raises(ValueError):
        commands.dc_power_saving(enabled)
    assert commands._sequence == 0


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
@pytest.mark.parametrize("enabled", [False, True])
def test_dc_smart_native_builder_does_not_enable_other_models(model, enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=model)
    with pytest.raises(ValueError, match="original C1000 only"):
        commands.dc_power_saving(enabled)
    assert commands._sequence == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_original_ac_smart_packet_is_typed_and_has_no_status_alias(enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    request = commands.ac_power_saving(enabled)
    _, _, packet = unpack(request)
    assert packet.command.hex() == "0077" and request.response_command == "0877"
    assert request.response_aliases == ()
    fields = parse_tlvs(packet.payload)
    assert set(fields) == {0xA1, 0xA2, 0xFE}
    assert fields[0xA1] == b"\x22" and fields[0xA2] == bytes((1, int(enabled)))
    assert fields[0xFE][0] == 3 and len(fields[0xFE]) == 5


@pytest.mark.parametrize("enabled", [0, 1, None, "on", 0.0])
def test_original_ac_smart_rejects_non_boolean_without_request(enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    with pytest.raises(ValueError):
        commands.ac_power_saving(enabled)
    assert commands._sequence == 0


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
@pytest.mark.parametrize("enabled", [False, True])
def test_native_ac_smart_does_not_enable_other_models(model, enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=model)
    with pytest.raises(ValueError, match="original C1000 only"):
        commands.ac_power_saving(enabled)
    assert commands._sequence == 0


@pytest.mark.parametrize("method,args,kwargs", [
    ("readiness", (), {}), ("charge_cap", (90,), {}),
    ("discharge_floor", (5,), {}), ("off_grid_alert", (True,), {}),
    ("port_memory", (True,), {}),
    ("backup_reserve", (85,), {}), ("tou_plan", ((),), {"enabled": False}),
])
def test_original_cannot_fall_through_to_gen2_controls(method, args, kwargs):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    with pytest.raises(ValueError, match="Gen 2"):
        getattr(commands, method)(*args, **kwargs)
    assert commands._sequence == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_original_fast_request_is_model_specific_and_has_no_status_alias(enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    request = commands.fast_charge(enabled)
    _, _, packet = unpack(request)
    assert packet.command.hex() == "005e" and request.response_command == "085e"
    assert request.response_aliases == ()
    fields = parse_tlvs(packet.payload)
    assert set(fields) == {0xA1, 0xA2, 0xFE}
    assert fields[0xA1] == b"\x22" and fields[0xA2] == bytes((1, int(enabled)))
    assert len(fields[0xFE]) == 5 and fields[0xFE][0] == 3


@pytest.mark.parametrize("enabled", [0, 1, None, "on", 0.0])
def test_original_fast_rejects_non_boolean_without_constructing_request(enabled):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    with pytest.raises(ValueError):
        commands.fast_charge(enabled)
    assert commands._sequence == 0


def test_original_fast_support_does_not_allow_c2000_output_or_fast_writes():
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C2000_GEN2)
    with pytest.raises(ValueError, match="original C1000 and C1000 Gen 2"):
        commands.fast_charge(True)
    assert commands._sequence == 0


def test_original_credentials_match_independent_openssl_vector():
    plaintext = b"-----BEGIN CERTIFICATE-----\nsynthetic-original\n-----END CERTIFICATE-----\n"
    # openssl enc -aes-256-cbc, key=synthetic serial repeated twice, IV=serial.
    encoded = ("71GxIJQO1snMKe5+/WjvvV4/ce6M0SiM1mIw9ti3ZFQ8wGeXdO7KNUfxYJq+rKZe"
               "rotm0a6t7+BuNeshyiv2munzps0Lvdr7YWgFKyG0u+s=")
    assert encrypt_device_credential(SERIAL, plaintext) == encoded
    assert decrypt_device_credential(SERIAL, encoded) == plaintext


@pytest.mark.parametrize("seconds", [1, 15, 60, 120])
def test_original_stream_uses_uint16_expiry(seconds):
    request = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000).stream(seconds)
    _, _, packet = unpack(request)
    assert request.response_command == "0857" and packet.command.hex() == "0057"
    fields = parse_tlvs(packet.payload)
    assert list(fields) == [0xA1, 0xA2, 0xA3, 0xFE]
    assert fields[0xA1] == b"\x22" and fields[0xA2] == b"\x01\x01"
    assert fields[0xA3] == b"\x02" + seconds.to_bytes(2, "little")


@pytest.mark.parametrize("seconds", [True, 0, -1, 121, 65535, 60.0, "60"])
def test_original_stream_rejects_invalid_or_unbounded_expiry(seconds):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    with pytest.raises(ValueError, match="Stream duration"):
        commands.stream(seconds)
    assert commands._sequence == 0


def test_original_profile_certificate_bootstrap_and_content_length(tmp_path):
    config = APServiceConfig("original", "wlan_ap", "phy9", "AT", SERIAL, ACCOUNT, model=Model.C1000)
    directory = tmp_path / "original"
    initialize_ap_service(directory, config)
    assert config.product == "A1761"
    assert load_ap_service(directory / "ap_service.json") == config
    response = (directory / "mqtt-response.json").read_bytes()
    data = json.loads(response)["data"]
    assert data["device_sn"] == data["thing_name"] == SERIAL
    assert decrypt_device_credential(SERIAL, data["certificate_pem"]) == (directory / "client.pem").read_bytes()
    assert decrypt_device_credential(SERIAL, data["private_key"]) == (directory / "client-key.pem").read_bytes()
    body, chunked = api_response("//equipment/devicemanage/get_mqtt_info", {"device_sn": SERIAL}, config, response)
    assert not chunked and body == response
    wire = http_reply(body, credentials=chunked)
    assert f"Content-Length: {len(response)}\r\n".encode() in wire
    assert b"Transfer-Encoding" not in wire
    for invalid in (SERIAL + "0", SERIAL[:-1], None):
        with pytest.raises(ValueError, match="16-character"):
            replace(config, device_serial=invalid)
    with pytest.raises(ValueError, match="17-character"):
        replace(config, model=Model.C1000_GEN2)
    assert ACCOUNT not in repr(config) and SERIAL not in repr(config)


def test_deferred_status_alias_is_original_status_only():
    original = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    assert original.stream().response_aliases == ()
    for model in (Model.C1000_GEN2, Model.C2000_GEN2):
        commands = NativeMqttCommands("SYNTHETICGEN2TEST1", ACCOUNT, model=model)
        assert commands.status().response_command == "0900"
        assert commands.status().response_aliases == ()
        assert commands.ac_charging_power(300).response_aliases == ()
        assert commands.stream().response_aliases == ()


@pytest.mark.parametrize("method,command,width,values", [
    ("temperature_unit", "0050", 1, (False, True)),
    ("display_brightness", "004c", 1, (0, 1, 2, 3)),
    ("device_timeout", "0045", 2, (0, 30, 60, 120, 240, 360, 720, 1440)),
    ("display_timeout", "0046", 2, (20, 30, 60, 300, 1800)),
    ("light_mode", "004f", 1, (0, 1, 2, 3, 4)),
])
def test_original_preferences_have_only_typed_a2_seconds_and_no_status_alias(monkeypatch, method, command, width, values):
    monkeypatch.setattr("solix_link.native_mqtt.time.time", lambda: 1800000000.125)
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    for value in values:
        request = getattr(commands, method)(value)
        _, _, packet = unpack(request)
        assert packet.command.hex() == command and packet.pattern == DATA_REQUEST
        assert request.response_command == f"{int(command, 16) | 0x0800:04x}"
        assert request.response_aliases == ()
        assert parse_tlvs(packet.payload) == {
            0xA1: b"\x22", 0xA2: bytes((width,)) + int(value).to_bytes(width, "little"),
            0xFE: b"\x03\x00\xd2\x49\x6b",
        }


@pytest.mark.parametrize("method,values", [
    ("temperature_unit", (0, 1, None, "false")),
    ("display_brightness", (-1, 4, True, 1.0, "1")),
    ("device_timeout", (True, 1, 719, 1441, 720.0, "720")),
    ("display_timeout", (True, 0, 10, 1801, 30.0, "30")),
    ("light_mode", (True, -1, 5, 1.0, "1")),
])
def test_original_preferences_reject_wrong_domain_without_building_request(method, values):
    commands = NativeMqttCommands(SERIAL, ACCOUNT, model=Model.C1000)
    for value in values:
        with pytest.raises(ValueError):
            getattr(commands, method)(value)
        assert commands._sequence == 0


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_original_light_command_refuses_gen2(model):
    commands = NativeMqttCommands("SYNTHETIC", ACCOUNT, model=model)
    with pytest.raises(ValueError, match="original C1000 only"):
        commands.light_mode(1)
    assert commands._sequence == 0


def test_shared_preferences_still_use_gen2_opcodes_and_fields():
    commands = NativeMqttCommands("SYNTHETIC", ACCOUNT, model=Model.C1000_GEN2)
    cases = ((commands.temperature_unit(True), 0xA5, b"\x01\x01"),
             (commands.display_brightness(1), 0xA3, b"\x01\x01"),
             (commands.device_timeout(0), 0xA6, b"\x02\x00\x00"),
             (commands.display_timeout(60), 0xA4, b"\x02\x3c\x00"))
    for request, tag, value in cases:
        _, _, packet = unpack(request)
        assert packet.command.hex() == "0103"
        fields = parse_tlvs(packet.payload)
        assert list(fields) == [0xA1, tag, 0xFD] and fields[tag] == value
        assert request.response_command == "0903" and request.response_aliases == ()
