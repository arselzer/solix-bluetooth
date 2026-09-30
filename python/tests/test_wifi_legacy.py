"""App-derived A1761 provisioning differs from the tested Prime layout."""

import pytest

from solix_link.protocol import DATA_REQUEST, Model, Session, build_packet, parse_packet, parse_tlvs


def ready(model=Model.C1000):
    session = Session(model)
    session.ready, session._secret = True, bytes(range(32))
    return session


def test_original_wifi_uses_legacy_cbc_untyped_fields_and_country_without_c3():
    session = ready()
    credentials = parse_packet(session.wifi_credentials_packet('Local-AP', 'examplepass', 'a' * 40))
    assert credentials.pattern == DATA_REQUEST and credentials.command.hex() == '4024'
    fields = parse_tlvs(session._crypt(credentials.payload, False))
    assert list(fields) == list(range(0xA1, 0xA7))
    assert len(fields[0xA1]) == 4 and fields[0xA2] == b'a' * 40
    assert fields[0xA3] == b'Local-AP' and fields[0xA6] == b'examplepass'
    assert fields[0xA4] == fields[0xA5] == b'\x00'
    activation = parse_packet(session.wifi_cloud_config_packet(
        'a' * 40, 'http://192.168.77.1/', 'UTC0', 'Etc/UTC', country_code='AT', allow_http=True))
    assert activation.command.hex() == '4025'
    fields = parse_tlvs(session._crypt(activation.payload, False))
    assert list(fields) == list(range(0xA1, 0xA9)) and 0xC3 not in fields
    assert fields[0xA5] == b'AT' and fields[0xA7] == b'A1761'
    for opcode in ('4824', '4825'):
        ack = build_packet(DATA_REQUEST, bytes.fromhex(opcode), session._crypt(b'\x00', True))
        assert session.feed(ack).response == (opcode, b'\x00')


@pytest.mark.parametrize('country', ['at', 'AUT', '', 'ÅT', None, 1])
def test_original_country_validation(country):
    with pytest.raises(ValueError, match='country_code'):
        ready().wifi_cloud_config_packet('a' * 40, 'https://example.invalid/', 'UTC0', 'Etc/UTC',
                                        country_code=country)


@pytest.mark.parametrize('model,protocol,is_ready', [
    (Model.C300, 'legacy', True), (Model.C1000, 'prime', True),
    (Model.C1000, 'legacy', False), (Model.C1000_GEN2, 'legacy', True),
])
def test_wifi_requires_the_supported_model_and_negotiated_transport(model, protocol, is_ready):
    session = ready(model)
    session.protocol, session.ready = protocol, is_ready
    with pytest.raises(RuntimeError):
        session.wifi_credentials_packet('Local-AP', 'examplepass', 'a' * 40)
    with pytest.raises(RuntimeError):
        session.wifi_cloud_config_packet('a' * 40, 'https://example.invalid/', 'UTC0', 'Etc/UTC')
