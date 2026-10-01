import asyncio
import json

from cryptography.exceptions import InvalidTag
import pytest

from solix_link import cli
from solix_link.client import SolixMonitor
from solix_link.config import DeviceConfig, save_config
from solix_link.diagnostics import decode_wifi_rssi
from solix_link.protocol import DATA_REQUEST, RADIO_REQUEST, Model, Session, build_packet, parse_packet, tlv


def report(value=-70):
    return b"\x00\xa1\x04" + value.to_bytes(4, "little", signed=True)


def ready_session():
    session = Session(Model.C1000_GEN2, "a" * 40)
    session.ready = True
    session._secret = bytes(range(32))
    return session


@pytest.mark.parametrize("value", [-128, -100, -70, -1, 1, 127])
def test_rssi_signed_byte_in_raw_four_byte_tlv(value):
    assert decode_wifi_rssi(report(value)) == value


def test_unavailable_is_not_zero_or_full_quality():
    assert decode_wifi_rssi(b"\x01") is None


@pytest.mark.parametrize("payload", [
    b"", b"\x00", b"\x08", b"\x01\xa1\x04" + bytes(4),
    report()[:-1], report() + b"\x00", b"\x00\xa2\x04" + bytes(4),
    b"\x00\xa1\x01\xba", report(0), report(-129), report(128),
])
def test_invalid_rssi_is_rejected(payload):
    with pytest.raises(ValueError):
        decode_wifi_rssi(payload)


def test_separate_radio_query_and_authenticated_reply_leave_telemetry_alone():
    session = ready_session()
    packet = parse_packet(session.wifi_rssi_packet())
    assert packet.pattern == RADIO_REQUEST and packet.command == b"\x40\x22"
    assert session._crypt(packet.payload, False) == tlv(0xA1, b"\x21")
    wire = build_packet(RADIO_REQUEST, b"\x48\x22", session._crypt(report(), True))
    update = session.feed(wire)
    assert update.radio_response == ("4822", report())
    assert update.telemetry is None and update.raw_tlvs is None and update.response is None
    corrupted = bytearray(session._crypt(report(), True))
    corrupted[-1] ^= 1
    with pytest.raises(InvalidTag):
        session.feed(build_packet(RADIO_REQUEST, b"\x48\x22", bytes(corrupted)))


@pytest.mark.parametrize("pattern,command", [
    (DATA_REQUEST, b"\x48\x22"), (RADIO_REQUEST, b"\x40\x22"),
    (RADIO_REQUEST, b"\x08\x22"), (RADIO_REQUEST, b"\x48\x4b"),
])
def test_other_namespaces_and_commands_are_not_rssi_replies(pattern, command):
    session = ready_session()
    update = session.feed(build_packet(pattern, command, session._crypt(report(), True)))
    assert update.radio_response is None


@pytest.mark.parametrize("model", [Model.C1000, Model.C300, Model.C2000_GEN2])
def test_unvalidated_models_have_no_rssi_query(model):
    session = Session(model)
    session.ready = True
    session._secret = bytes(range(32))
    with pytest.raises(ValueError):
        session.wifi_rssi_packet()
    wire = build_packet(RADIO_REQUEST, b"\x48\x22", session._crypt(report(), True))
    assert session.feed(wire).radio_response is None


def test_legacy_and_incomplete_negotiation_cannot_send_radio_query():
    legacy = Session(Model.C1000_GEN2, protocol="legacy")
    legacy.ready = True
    with pytest.raises(ValueError):
        legacy.wifi_rssi_packet()
    prime = Session(Model.C1000_GEN2)
    with pytest.raises(RuntimeError):
        prime.wifi_rssi_packet()
    prime.ready = True
    with pytest.raises(RuntimeError):
        prime.wifi_rssi_packet()


def test_client_query_is_serialized_and_ignores_stale_or_controller_replies():
    async def scenario():
        monitor = SolixMonitor("synthetic", model=Model.C1000_GEN2, owner_user_id="a" * 40)
        with pytest.raises(RuntimeError, match="not connected"):
            await monitor.wifi_rssi()
        monitor._session = ready_session()
        monitor._ready.set()
        monitor.metrics = {"ac_output_enabled": 1, "battery_percentage": 100}

        class Client:
            is_connected = True
            respond = True
            writes = []
            replies = [report(-70), b"\x01", report(-80), b"\x08"]

            async def write_gatt_char(self, _uuid, data, response):
                packet = parse_packet(data)
                self.writes.append((packet.pattern, packet.command))
                assert not response and packet.pattern == RADIO_REQUEST
                if self.respond:
                    await monitor._handle(build_packet(DATA_REQUEST, b"\x48\x22",
                        monitor._session._crypt(report(-90), True)))
                    await monitor._handle(build_packet(RADIO_REQUEST, b"\x48\x22",
                        monitor._session._crypt(self.replies.pop(0), True)))

        monitor._client = Client()
        await monitor._radio_responses.put(("4822", report(-100)))
        first, second = await asyncio.gather(monitor.wifi_rssi(), monitor.wifi_rssi())
        assert (first, second) == (-70, None)
        assert await monitor.wifi_rssi() == -80
        with pytest.raises(ValueError):
            await monitor.wifi_rssi()
        assert monitor.metrics == {"ac_output_enabled": 1, "battery_percentage": 100}
        assert not monitor._responses.empty()  # Independent controller response queue.
        monitor._client.respond = False
        with pytest.raises(TimeoutError):
            await monitor.wifi_rssi(timeout=.01)
        assert monitor._client.writes == [(RADIO_REQUEST, b"\x40\x22")] * 5
        for invalid in (0, -1, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                await monitor.wifi_rssi(timeout=invalid)
        assert len(monitor._client.writes) == 5

    asyncio.run(scenario())


def test_cli_rssi_output_and_preconnection_validation(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    save_config([DeviceConfig("test", "00:11:22:33:44:55", Model.C1000_GEN2, "a" * 40)], config)
    calls = []

    class Monitor:
        def __init__(self, *args, **kwargs):
            calls.append("create")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            calls.append("disconnect")

        async def wifi_rssi(self, timeout):
            calls.append(timeout)
            return None

    monkeypatch.setattr(cli, "SolixMonitor", Monitor)
    assert cli.main(["wifi-rssi", "--name", "test", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out) == {"wifi_rssi_dbm": None}
    assert calls == ["create", 20, "disconnect"]
    calls.clear()
    assert cli.main(["wifi-rssi", "--name", "test", "--config", str(config), "--timeout", "0"]) == 1
    assert not calls
    save_config([DeviceConfig("test", "00:11:22:33:44:55", Model.C2000_GEN2, "a" * 40)], config)
    assert cli.main(["wifi-rssi", "--name", "test", "--config", str(config)]) == 1
    assert not calls
