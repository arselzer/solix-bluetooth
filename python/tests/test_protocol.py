from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from solix_gen2.protocol import (
    DATA_REQUEST, DATA_RESPONSE, GCM_AAD, GCM_KEY, GCM_NONCE, Model, NEGOTIATION,
    Session, build_packet, decode_telemetry, parse_packet, parse_tlvs, tlv,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from zoneinfo import ZoneInfo
from datetime import datetime


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_prime_negotiation_with_synthetic_device(model):
    session = Session(model, timezone_name="Europe/Vienna")
    assert parse_packet(session.start()).command == bytes.fromhex("4001")
    first_reply = build_packet(
        NEGOTIATION, bytes.fromhex("4801"),
        AESGCM(GCM_KEY).encrypt(GCM_NONCE, b"\x00", GCM_AAD),
    )
    step = session.feed(first_reply)
    assert parse_packet(step.outgoing[0]).command == bytes.fromhex("4003")
    plaintext = AESGCM(GCM_KEY).decrypt(
        GCM_NONCE, parse_packet(step.outgoing[0]).payload, GCM_AAD
    )
    assert set(parse_tlvs(plaintext)) == {0xA1, 0xA3, 0xA4}

    peer = ec.generate_private_key(ec.SECP256R1())
    public = peer.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    key_reply = build_packet(
        NEGOTIATION, bytes.fromhex("4821"),
        AESGCM(GCM_KEY).encrypt(GCM_NONCE, tlv(0xA1, public[1:]), GCM_AAD),
    )
    step = session.feed(key_reply)
    confer = parse_packet(step.outgoing[0])
    assert confer.command == bytes.fromhex("4022")
    fields = parse_tlvs(session._crypt(confer.payload, False))
    assert fields[0xA5] == b"CET-1CEST,M3.5.0,M10.5.0/3"
    expected_west = -int(datetime.now(ZoneInfo("Europe/Vienna")).utcoffset().total_seconds())
    assert int.from_bytes(fields[0xA3], "little", signed=True) == expected_west
    assert session._secret is not None
    assert not session.ready

    for command in ("4822", "4827"):
        response = build_packet(
            NEGOTIATION, bytes.fromhex(command), session._crypt(b"\x00", True)
        )
        step = session.feed(response)
    assert session.ready
    subscribe = parse_packet(step.outgoing[0])
    assert subscribe.command == bytes.fromhex("4100")
    assert len(step.outgoing[0]) == 47  # Observed Prime 4100 frame length.
    assert session._crypt(subscribe.payload, False).startswith(
        bytes.fromhex("a10121a20a040100e3fbfcfe000000fe04")
    )


def test_prime_button_pairing_and_reconnect_id():
    owner = "0123456789abcdef0123456789abcdef01234567"
    session = Session(Model.C2000_GEN2, owner_user_id=owner)
    session._secret = bytes(range(32))
    reply = build_packet(NEGOTIATION, bytes.fromhex("4822"), session._crypt(b"\x00", True))
    registration = parse_packet(session.feed(reply).outgoing[0])
    assert parse_tlvs(session._crypt(registration.payload, False))[0xA2] == owner.encode()
    rejected = build_packet(NEGOTIATION, bytes.fromhex("4827"), session._crypt(b"\x09", True))
    assert session.feed(rejected).pairing_required
    retry = parse_packet(session.retry_registration())
    assert retry.command == bytes.fromhex("4027")
    assert parse_tlvs(session._crypt(retry.payload, False))[0xA2] == owner.encode()
    accepted = build_packet(NEGOTIATION, bytes.fromhex("4827"), session._crypt(b"\x00", True))
    assert session.feed(accepted).ready

    generated = Session(Model.C2000_GEN2)
    assert len(generated.owner_user_id) == 40
    assert set(generated.owner_user_id) <= set("0123456789abcdef")


def test_c1000_gen2_negotiation_and_telemetry():
    session = Session(Model.C1000_GEN2, protocol="legacy")
    assert parse_packet(session.start()).command == bytes.fromhex("0001")
    peer = ec.generate_private_key(ec.SECP256R1())
    public = peer.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    reply = build_packet(NEGOTIATION, bytes.fromhex("0821"), tlv(0xA1, public[1:]))
    result = session.feed(reply)
    assert session.ready
    assert [parse_packet(p).command.hex() for p in result.outgoing] == ["4022", "4100"]

    payload = b"".join([
        tlv(0xA5, bytes.fromhex("04fe000a64")),
        tlv(0xA6, bytes.fromhex("040f00a000")),
        tlv(0xA7, bytes.fromhex("04010f00")),
    ])
    encrypted = session._crypt(payload, True)
    middle = len(encrypted) // 2
    assert session.feed(build_packet(DATA_RESPONSE, bytes.fromhex("c421"), b"\x12" + encrypted[:middle])).telemetry is None
    update = session.feed(build_packet(DATA_RESPONSE, bytes.fromhex("c421"), b"\x22" + encrypted[middle:]))
    assert update.telemetry["battery_percentage"] == 10
    assert update.telemetry["ac_input_power_w"] == 160
    assert update.telemetry["ac_output_power_w"] == 15
    assert update.raw_tlvs[0xA5] == bytes.fromhex("04fe000a64")


def test_model_names_and_packet_validation():
    assert Model.from_name("SOLIX C1000 Gen 2") is Model.C1000_GEN2
    assert Model.from_name("SOLIX C2000 Gen 2") is Model.C2000_GEN2
    packet = build_packet(NEGOTIATION, bytes.fromhex("4001"), b"abc")
    assert parse_packet(packet).payload == b"abc"
    try:
        parse_packet(packet[:-1] + b"\x00")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid checksum was accepted")
    metrics, _ = decode_telemetry(tlv(0xA5, bytes.fromhex("04fe000a64")))
    assert metrics["temperature_c"] == -2


def test_c1000_prime_setting_packets_match_observed_app_shapes():
    session = Session(Model.C1000_GEN2, owner_user_id="a" * 40)
    session._secret = bytes(range(32))
    session.ready = True
    limits = parse_packet(session.charge_limits_packet(95, 1))
    assert limits.command.hex() == "4103"
    assert session._crypt(limits.payload, False) == bytes.fromhex("a10121aa02015fab020101")
    with pytest.raises(ValueError, match="lower"):
        session.charge_limits_packet(95, 2)

    power = parse_packet(session.ac_charging_power_packet(1000))
    assert power.command.hex() == "4101"
    plain = session._crypt(power.payload, False)
    assert plain.startswith(bytes.fromhex("a10121a40302e803ab03020000fd0e00"))
    assert len(plain) == 29
    assert plain[16:].decode("ascii").isdigit()

    display = parse_packet(session.display_timeout_packet(60))
    assert display.command.hex() == '4103'
    assert session._crypt(display.payload, False).startswith(bytes.fromhex('a10121a403023c00fd0e00'))
    fast = parse_packet(session.fast_charge_packet(True))
    assert fast.command.hex() == '4101'
    assert session._crypt(fast.payload, False).startswith(bytes.fromhex('a10121a7020101fd0e00'))
    with pytest.raises(ValueError, match='display timeout'):
        session.display_timeout_packet(45)
    with pytest.raises(ValueError, match='boolean'):
        session.fast_charge_packet(2)

    c2000 = Session(Model.C2000_GEN2, owner_user_id="b" * 40)
    c2000._secret = bytes(range(32))
    c2000.ready = True
    cap = parse_packet(c2000.charge_cap_packet(85))
    assert cap.command.hex() == '4103'
    assert c2000._crypt(cap.payload, False) == bytes.fromhex('a10121aa020155')
    with pytest.raises(ValueError, match='80–100'):
        c2000.charge_cap_packet(75)
    power = parse_packet(c2000.ac_charging_power_packet(1700))
    assert power.command.hex() == '4101'
    assert c2000._crypt(power.payload, False).startswith(bytes.fromhex('a10121a40302a406ab03020000fd0e00'))
    with pytest.raises(ValueError, match='1800'):
        c2000.ac_charging_power_packet(1900)
    display = parse_packet(c2000.display_timeout_packet(60))
    assert display.command.hex() == '4103'
    assert c2000._crypt(display.payload, False).startswith(bytes.fromhex('a10121a403023c00fd0e00'))
    with pytest.raises(ValueError, match='C2000 display timeout'):
        c2000.display_timeout_packet(300)
    with pytest.raises(RuntimeError, match="C1000"):
        c2000.charge_limits_packet(95, 1)
    with pytest.raises(RuntimeError, match="C1000"):
        c2000.fast_charge_packet(True)
    with pytest.raises(RuntimeError, match="C2000"):
        session.charge_cap_packet(85)

    reading = tlv(0xA4, bytes.fromhex("0400000000b004"))
    assert decode_telemetry(reading, Model.C1000_GEN2)[0]["ac_charging_power_limit_w"] == 1200
    assert decode_telemetry(reading, Model.C2000_GEN2)[0]["ac_charging_power_limit_w"] == 1200


def test_c2000_readonly_version_and_expansion_fields():
    # Non-identifying values matching the tested C2000's F9/A4 shape.
    versions = (
        bytes((4, 6, 1, 2)) + bytes((1, 7, 0, 5)) + bytes(4)
        + bytes((0, 7, 0, 5)) + bytes((0, 3, 3, 9))
        + bytes((1, 0, 0, 0)) + bytes((0, 0, 3, 3)) + b"\x00"
    )
    settings = bytearray(34)
    settings[5:7] = (1800).to_bytes(2, "little")
    settings[7] = 50
    settings[16:18] = (30).to_bytes(2, 'little')
    # C0 has a variable serial prefix; only its connection flag is exposed.
    no_pack = bytes((17,)) + b"X" * 17 + bytes(12) + b"\x00" + bytes(4)
    tou = bytes.fromhex('040001555a010401010018')
    reading = (tlv(0xA4, bytes(settings)) + tlv(0xF9, versions)
               + tlv(0xC0, no_pack) + tlv(0xD9, tou))
    metrics, _ = decode_telemetry(reading, Model.C2000_GEN2)
    assert metrics["software_version"] == "2.1.6.4"
    assert metrics["software_version_controller"] == "5.0.7.1"
    assert metrics["software_version_bms"] == "9.3.3.0"
    assert metrics["software_version_module"] == "3.3.0.0"
    assert metrics["ac_charging_power_limit_w"] == 1800
    assert metrics["ac_input_frequency_hz"] == 50
    assert metrics["display_timeout_seconds"] == 30
    assert metrics["expansion_battery_count"] == 0
    assert metrics["usage_mode"] == "time_of_use"
    assert metrics["backup_reserve_percentage"] == 85
    assert metrics["tou_schedule_parameter"] == 4
    assert metrics["tou_schedule_slot_count"] == 1
    assert "software_version" not in decode_telemetry(tlv(0xF9, b"\x01\x02"), Model.C2000_GEN2)[0]


def test_c1000_wifi_provisioning_packets_match_captured_field_order():
    session = Session(Model.C1000_GEN2, owner_user_id='a' * 40)
    session._secret = bytes(range(32))
    session.ready = True
    credentials = parse_packet(session.wifi_credentials_packet('Lab-AP', 'examplepass', 'b' * 40))
    assert credentials.command.hex() == '4024'
    fields = parse_tlvs(session._crypt(credentials.payload, False))
    assert list(fields) == [0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6]
    assert fields[0xA2] == b'b' * 40
    assert fields[0xA3] == b'Lab-AP'
    assert fields[0xA4] == fields[0xA5] == b'\x00'
    assert fields[0xA6] == b'examplepass'

    c2000 = Session(Model.C2000_GEN2, owner_user_id='a' * 40)
    c2000._secret = bytes(range(32))
    c2000.ready = True
    c2000_join = parse_packet(c2000.wifi_credentials_packet('Lab-AP', 'examplepass', 'b' * 40))
    assert c2000_join.command.hex() == '4024'
    c2000_fields = parse_tlvs(c2000._crypt(c2000_join.payload, False))
    assert [(tag, value) for tag, value in c2000_fields.items() if tag != 0xA1] == [
        (tag, value) for tag, value in fields.items() if tag != 0xA1
    ]

    cloud = parse_packet(session.wifi_cloud_config_packet(
        'b' * 40, 'https://example.invalid/', 'CET-1CEST,M3.5.0,M10.5.0/3', 'Europe/Vienna',
    ))
    assert cloud.command.hex() == '4025'
    fields = parse_tlvs(session._crypt(cloud.payload, False))
    assert list(fields) == [0xA1, 0xA2, 0xA3, 0xA4, 0xC3, 0xA6, 0xA7, 0xA8]
    assert fields[0xA3] == b'https://example.invalid/'
    assert fields[0xC3] == b'A2'
    assert fields[0xA6] == b'anker_power'
    assert fields[0xA7] == b'A1763'

    with pytest.raises(ValueError, match='SSID'):
        session.wifi_credentials_packet('x' * 33, 'examplepass', 'b' * 40)
    with pytest.raises(ValueError, match='passphrase'):
        session.wifi_credentials_packet('Lab-AP', 'short', 'b' * 40)
    with pytest.raises(RuntimeError, match='C1000'):
        c2000.wifi_cloud_config_packet(
            'b' * 40, 'https://example.invalid/', 'CET-1CEST,M3.5.0,M10.5.0/3',
            'Europe/Vienna',
        )

    ack = build_packet(DATA_REQUEST, bytes.fromhex('4824'), session._crypt(b'\x00', True))
    assert session.feed(ack).response == ('4824', b'\x00')


def test_ups_fields_follow_c1000_live_mains_transition():
    # Non-identifying A3/A6/A7 values from one C1000 AC input
    # plugged -> unplugged -> replugged capture on Prime firmware 1.1.4.9.
    states = [
        ("0400000000b0040064cc00580200", "04560056000000ab2a64", "04015600015600", 1, "idle", 0),
        ("0401000000b0040064cc00580200", "04550000000000b60064", "04015500000000", 0, "discharging", 1092),
        ("0400000000b0040064cc00580200", "04560056000000900164", "04015600015600", 1, "idle", 0),
    ]
    for a3, a6, a7, mains, status, minutes in states:
        payload = tlv(0xA3, bytes.fromhex(a3)) + tlv(0xA6, bytes.fromhex(a6)) + tlv(0xA7, bytes.fromhex(a7))
        metrics, _ = decode_telemetry(payload, Model.C1000_GEN2)
        assert metrics["ac_input_connected"] == mains
        assert metrics["battery_status"] == status
        assert metrics["battery_discharging"] == int(status == "discharging")
        assert metrics["time_remaining_minutes"] == minutes
        assert metrics["ac_output_enabled"] == 1


def test_c1000_setting_fields_decode_from_a4_status():
    settings = bytearray(34)
    settings[0] = 4
    settings[5:7] = (1200).to_bytes(2, 'little')
    settings[14:16] = (0).to_bytes(2, 'little')
    settings[16:18] = (30).to_bytes(2, 'little')
    settings[18] = 1
    settings[21] = 0
    settings[22] = 0
    settings[23] = 1
    metrics, _ = decode_telemetry(tlv(0xA4, bytes(settings)), Model.C1000_GEN2)
    assert metrics['ac_charging_power_limit_w'] == 1200
    assert metrics['device_timeout_minutes'] == 0
    assert metrics['display_timeout_seconds'] == 30
    assert metrics['display_mode'] == 1
    assert metrics['ac_fast_charge_enabled'] == 0
    assert metrics['display_enabled'] == 0
    assert metrics['port_memory_enabled'] == 1
