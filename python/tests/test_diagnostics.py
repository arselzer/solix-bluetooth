import asyncio

import pytest

from solix_gen2.client import SolixMonitor
from solix_gen2.diagnostics import decode_network_diagnostics
from solix_gen2.protocol import DATA_REQUEST, Model, Session, parse_packet, parse_tlvs, tlv


def report(mqtt_error=-21):
    return (b"\x00" + tlv(0xA1, b"\x03") + tlv(0xA2, b"\xff")
            + tlv(0xA3, (901).to_bytes(4, "little"))
            + tlv(0xA4, bytes(4)) + tlv(0xA5, (531).to_bytes(4, "little"))
            + tlv(0xA6, mqtt_error.to_bytes(4, "little", signed=True)))


def test_diagnostic_codes_are_signed_and_unknown_fields_are_allowed():
    values = decode_network_diagnostics(report() + tlv(0xB0, b"future"))
    assert values == {
        "system_reboot_code": 3, "sdk_reset_code": 255,
        "http_error_code": 901, "wifi_error_code": 0,
        "ble_disconnect_code": 531, "mqtt_error_code": -21,
    }


@pytest.mark.parametrize("payload", [
    b"", b"\x01" + report()[1:], report()[:-1], report() + b"\xa7",
    report() + tlv(0xA6, bytes(4)), report()[:-6],
    report()[:-6] + tlv(0xA6, b"\x00"),
])
def test_invalid_diagnostics_do_not_look_like_success(payload):
    with pytest.raises(ValueError):
        decode_network_diagnostics(payload)


@pytest.mark.parametrize("model", list(Model))
def test_diagnostic_query_uses_radio_namespace_and_requires_prime_session(model):
    session = Session(model, "a" * 40)
    with pytest.raises(RuntimeError):
        session.network_diagnostics_packet()
    session._secret = bytes(range(32))
    session.ready = True
    packet = parse_packet(session.network_diagnostics_packet())
    assert packet.pattern == DATA_REQUEST
    assert packet.command.hex() == "4020"
    assert set(parse_tlvs(session._crypt(packet.payload, False))) == {0xA1}
    legacy = Session(Model.C1000_GEN2, protocol="legacy")
    legacy.ready = True
    with pytest.raises(RuntimeError):
        legacy.network_diagnostics_packet()


def test_client_diagnostic_response_and_timeout():
    async def scenario():
        monitor = SolixMonitor("synthetic", model=Model.C2000_GEN2, owner_user_id="a" * 40)
        with pytest.raises(RuntimeError, match="not connected"):
            await monitor.network_diagnostics()

        class Client:
            is_connected = True
            respond = True
            writes = []

            async def write_gatt_char(self, uuid, data, response):
                packet = parse_packet(data)
                self.writes.append(packet.command.hex())
                if self.respond:
                    await monitor._responses.put(("4810", b"unrelated"))
                    await monitor._responses.put(("4820", report()))

        monitor._client = Client()
        monitor._ready.set()
        monitor._session.ready = True
        monitor._session._secret = bytes(range(32))
        await monitor._responses.put(("4820", report(-19)))  # Stale response.
        first, second = await asyncio.gather(
            monitor.network_diagnostics(), monitor.network_diagnostics())
        assert first == second and first["mqtt_error_code"] == -21
        assert monitor._client.writes == ["4020", "4020"]
        monitor._client.respond = False
        with pytest.raises(TimeoutError):
            await monitor.network_diagnostics(timeout=0.01)

    asyncio.run(scenario())
