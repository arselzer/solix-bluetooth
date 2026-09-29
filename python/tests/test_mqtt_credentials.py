import base64

import pytest

from solix_gen2.mqtt_credentials import (
    decrypt_device_credential,
    encrypt_device_credential,
)


SERIAL = "A1763TEST00000001"
PLAINTEXT = b"-----BEGIN CERTIFICATE-----\nsynthetic-fixture\n-----END CERTIFICATE-----\n"
# Independently generated with openssl enc -aes-256-cbc and this synthetic serial.
CIPHERTEXT = (
    "udkqnbC/vgIZuCYKywICjVLSgUmIFKIw1ZG5ueuVC5+4ZQ0BGhxSxEsBIH0IU8G6"
    "8FkFWcvvVB7dYzm4vjK6fOPhcz4qaqoVxk9+NQrC+xc="
)


def test_device_credential_matches_openssl_vector():
    assert encrypt_device_credential(SERIAL, PLAINTEXT) == CIPHERTEXT
    assert decrypt_device_credential(SERIAL, CIPHERTEXT) == PLAINTEXT


@pytest.mark.parametrize("serial", ["", "short", "A" * 16, "A" * 18, "ä" * 17])
def test_reject_unobserved_serial_formats(serial):
    with pytest.raises(ValueError, match="serial"):
        encrypt_device_credential(serial, PLAINTEXT)
    with pytest.raises(ValueError, match="serial"):
        decrypt_device_credential(serial, CIPHERTEXT)


@pytest.mark.parametrize("encoded", ["", "!invalid!", "ä", "YQ==", CIPHERTEXT + "\n"])
def test_reject_malformed_envelope(encoded):
    with pytest.raises(ValueError):
        decrypt_device_credential(SERIAL, encoded)


def test_reject_invalid_padding_without_echoing_credential():
    damaged = bytearray(base64.b64decode(CIPHERTEXT))
    # Flip the final padding byte via the preceding CBC block.
    damaged[-17] ^= 0xFF
    with pytest.raises(ValueError, match="^Credential padding is invalid$"):
        decrypt_device_credential(SERIAL, base64.b64encode(damaged).decode())
