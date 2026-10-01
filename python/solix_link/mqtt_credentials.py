"""Credential envelope used by the device's get_mqtt_info endpoint.

Verified on original C1000 radio 0.3.3.0 (16-character serial) and Gen 2
stations (17-character serial) for the client certificate/key;
the device response's root CA field remains plain PEM. This differs from the account
API, which returns PEM directly. These helpers perform no network requests.
The serial-derived key does not provide secrecy from someone with the serial.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _cipher(device_serial: str) -> Cipher:
    if not isinstance(device_serial, str):
        raise ValueError("Device serial must be 16 or 17 ASCII characters")
    try:
        serial = device_serial.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("Device serial must be 16 or 17 ASCII characters") from None
    if len(serial) not in (16, 17):
        raise ValueError("Only observed 16- or 17-character device serial formats are supported")
    return Cipher(algorithms.AES((serial * 2)[:32]), modes.CBC(serial[:16]))


def encrypt_device_credential(device_serial: str, plaintext: bytes) -> str:
    """Wrap one PEM field for the experimental device MQTT response."""
    encryptor = _cipher(device_serial).encryptor()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_device_credential(device_serial: str, encoded: str) -> bytes:
    """Unwrap one credential field; the caller must validate the resulting PEM."""
    decryptor = _cipher(device_serial).decryptor()
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Credential must be valid Base64") from None
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError("Credential ciphertext must contain complete AES blocks")
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        raise ValueError("Credential padding is invalid") from None
