# BLE encryption framing guard

## Why the header matters

The radio checks the encryption marker in the command's first byte,
`packet[7] & 0x40`, before dispatching a body. AES-GCM or AES-CBC ciphertext
without that marker can be forwarded as ordinary bytes. Encrypting a body
does not automatically set the marker in its header.

The [original C1000 diagnostic attempt](c1000-f0-live-transport-limit.md) exposed
this distinction. Its encrypted body used command `0000`; normal encrypted BLE
requests use the `4000` marker. The older original MCU also has an unchecked
diagnostic length loop. A malformed body can therefore have consequences
beyond an unanswered request. Original-radio routing and the exact cause of
that station's observed output-off state remain unproved.

## Implemented checks

The Python `Session._send()` now requires a two-byte command with the `0x40`
marker whenever it encrypts a body: all Prime stages and legacy sessions after
key exchange. It raises `ValueError` before encryption or packet construction.
Legacy plaintext negotiation before key exchange remains supported.

The separate Web Bluetooth app applies the same check before Prime encryption
and before legacy encrypted GATT writes. Existing model-specific command guards
still apply. The low-level plaintext frame builders remain available for MCU
and native MQTT framing; their headers follow their own transport conventions.

This rejects the known inconsistent header. It does **not** validate an arbitrary
command, diagnostic body, length, device version, routing destination or physical
side effect. There is no new F0 or arbitrary-register API.

## Verification

```sh
PYTHONPATH=python python3 -m pytest python/tests/test_protocol.py -q
npm run test:protocol
npm run build
```

Python regression cases reject unmarked headers across Prime models, before
and after session-key exchange, and after legacy key exchange. They also reject
incorrect command lengths and preserve valid CBC frames/plaintext startup.
Existing negotiation/control tests cover the supported Prime frames.

The browser tests negotiate with a synthetic ECDH peer and verify GCM decoding
of an accepted request. A synthetic legacy GATT endpoint verifies CBC decoding.
Both assert that rejected commands produce no send/write. These tests access
no Bluetooth device, station configuration or private capture.
