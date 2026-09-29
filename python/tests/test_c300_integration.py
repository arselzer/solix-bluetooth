"""C300 AC discovery, session routing and service boundaries using synthetic data."""

import asyncio
import json

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from solix_gen2.cli import main, parser
from solix_gen2.client import SolixMonitor
from solix_gen2.config import DeviceConfig, load_config
from solix_gen2.manager import MonitorService
from solix_gen2.mqtt_bridge import _supports_operation
from solix_gen2.protocol import (
    DATA_RESPONSE, NEGOTIATION, Model, Session, build_packet, parse_packet, parse_tlvs, tlv,
)


@pytest.mark.parametrize("name", ["Anker SOLIX C300", "Anker SOLIX C300X", "A1722", "Anker_A1723"])
def test_ac_discovery_names(name):
    assert Model.from_name(name) is Model.C300


@pytest.mark.parametrize("name", ["C300 DC", "Anker SOLIX C300X DC", "A1726", "C300 A1728", "C3000", "A1753"])
def test_other_c300_layouts_are_not_assumed_compatible(name):
    with pytest.raises(ValueError):
        Model.from_name(name)


def test_c300_legacy_negotiation_and_fragmented_status():
    session = Session(Model.C300)
    assert session.protocol == "legacy"
    assert session.owner_user_id is None
    assert parse_packet(session.start()).command.hex() == "0001"
    peer = ec.generate_private_key(ec.SECP256R1())
    public = peer.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    reply = build_packet(NEGOTIATION, bytes.fromhex("0821"), tlv(0xA1, public[1:]))
    step = session.feed(reply)
    assert step.ready
    assert [parse_packet(p).command.hex() for p in step.outgoing] == ["4022", "4040"]
    request = parse_packet(step.outgoing[-1])
    assert session._crypt(request.payload, False).startswith(bytes.fromhex("a10121fe0503"))

    payload = tlv(0xBB, b"\x01\x31") + tlv(0xBA, b"\x01\x02") + tlv(0xAD, b"\x02\x32\x00")
    encrypted = session._crypt(payload, True)
    middle = len(encrypted) // 2
    first = build_packet(DATA_RESPONSE, bytes.fromhex("c840"), b"\x12" + encrypted[:middle])
    last = build_packet(DATA_RESPONSE, bytes.fromhex("c840"), b"\x22" + encrypted[middle:])
    assert session.feed(first).telemetry is None
    metrics = session.feed(last).telemetry
    assert metrics["battery_percentage"] == 49
    assert metrics["battery_status"] == "charging"
    assert metrics["input_power_w"] == 50
    assert "ac_input_connected" not in metrics

    with pytest.raises(ValueError, match="status requests only"):
        session.send_command("4041", bytes.fromhex("a10121"))
    with pytest.raises(RuntimeError):
        session.fast_charge_packet(True)
    with pytest.raises(RuntimeError):
        session.wifi_credentials_packet("TestNetwork", "TestPassword", "a" * 40)
    with pytest.raises(RuntimeError):
        session.network_diagnostics_packet()


def test_model_protocol_defaults_and_incompatible_overrides():
    assert Session(Model.C1000_GEN2).protocol == "prime"
    assert Session(Model.C1000_GEN2, protocol="legacy").protocol == "legacy"
    assert Session(Model.C2000_GEN2).protocol == "prime"
    monitor = SolixMonitor("AA:BB:CC:DD:EE:03", model=Model.C300)
    assert monitor.protocol == "legacy"
    assert monitor.owner_user_id is None
    with pytest.raises(ValueError, match="legacy"):
        SolixMonitor("AA:BB:CC:DD:EE:03", model=Model.C300, protocol="prime")
    with pytest.raises(ValueError, match="Prime"):
        DeviceConfig("ups", "AA:BB:CC:DD:EE:03", Model.C2000_GEN2, protocol="legacy")


def test_verified_c300_display_timeout_keeps_power_fields_out():
    session = Session(Model.C300)
    session.ready = True
    session._secret = bytes(range(32))
    for seconds in (30, 60):
        packet = parse_packet(session.display_timeout_packet(seconds))
        assert packet.command.hex() == "4046"
        plain = session._crypt(packet.payload, False)
        assert plain.startswith(b"\xa1\x01\x21\xa2\x03\x02" + seconds.to_bytes(2, "little") + b"\xfe\x05\x03")
    for seconds in (0, 20, 300, True):
        with pytest.raises(ValueError):
            session.display_timeout_packet(seconds)
    device = DeviceConfig("c300", "AA:BB:CC:DD:EE:03", Model.C300)
    assert _supports_operation(device, "display_timeout")
    assert _supports_operation(device, "ac_charging_power")


def test_c300_ac_light_and_charging_limit_packets_match_live_shapes():
    session = Session(Model.C300)
    session.ready = True
    session._secret = bytes(range(32))
    for packet, command, value in (
        (session.ac_output_packet(True), "404a", b"\x01\x01"),
        (session.ac_output_packet(False), "404a", b"\x01\x00"),
        (session.light_mode_packet(1), "404f", b"\x01\x01"),
        (session.light_mode_packet(0), "404f", b"\x01\x00"),
        (session.ac_charging_power_packet(300), "4044", b"\x02\x2c\x01"),
        (session.ac_charging_power_packet(330), "4044", b"\x02\x4a\x01"),
    ):
        decoded = parse_packet(packet)
        assert decoded.command.hex() == command
        fields = parse_tlvs(session._crypt(decoded.payload, False))
        assert set(fields) == {0xA1, 0xA2, 0xFE}
        assert fields[0xA1] == b"\x21"
        assert fields[0xA2] == value
        assert len(fields[0xFE]) == 5 and fields[0xFE][0] == 3
    for invalid in (0, 150, 500, True):
        with pytest.raises(ValueError):
            session.ac_charging_power_packet(invalid)
    for invalid in (-1, 4, True):
        with pytest.raises(ValueError):
            session.light_mode_packet(invalid)
    with pytest.raises(ValueError):
        session.ac_output_packet(1)


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_legacy_output_controls_cannot_target_gen2(model):
    session = Session(model)
    session.ready = True
    session._secret = bytes(range(32))
    with pytest.raises(RuntimeError):
        session.ac_output_packet(False)
    with pytest.raises(RuntimeError):
        session.light_mode_packet(0)


def test_cli_c300_config_requires_no_pairing_and_rejects_unknown_controls(tmp_path):
    path = tmp_path / "config.json"
    assert main(["add", "--name", "c300", "--address", "AA:BB:CC:DD:EE:03",
                 "--model", "c300", "--config", str(path)]) == 0
    device, = load_config(path)
    assert device.protocol == "legacy"
    assert device.client_id is None
    service = MonitorService([device])
    assert service.snapshot("c300")["model"] == "c300"
    assert _supports_operation(device, "ac_charging_power")
    with pytest.raises(ValueError, match="not verified"):
        asyncio.run(service.apply_setting("c300", "charge_limits", upper=90, lower=1))
    with pytest.raises(SystemExit):
        parser().parse_args(["pair", "--name", "c300", "--address", device.address, "--model", "c300"])

    # Config files without a protocol also resolve the model's default.
    path.write_text(json.dumps({"devices": [{"name": "c300", "address": device.address, "model": "c300"}]}))
    assert load_config(path)[0] == device
