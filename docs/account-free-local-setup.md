# Account-free local setup: evidence and remaining tests

## Model-specific status

This audit on **2026-10-01** used retained files and the published **A1763
C1000 Gen 2 radio 0.3.3.0** image. It made no Bluetooth, SSH, MQTT, cloud or
device request, and changed no saved identities or network profiles.

| Device and firmware | Generated local BLE ID | Generated local native MQTT identity |
| --- | --- | --- |
| C1000 Gen 2 / A1763, main 1.1.4.9, radio 0.3.3.0 | Verified on hardware | Verified: bootstrap, mutual TLS, status and restored charging/tariff controls |
| C2000 Gen 2 / A1783, main 2.1.6.4 | Verified on hardware | Unverified; successful local MQTT trials used the retained app account ID |
| Original C1000 / A1761, main 1.7.1, radio 0.3.3.0 | Unverified | Unverified; successful Prime/local MQTT trials used the retained app account ID |

An earlier original-C1000 **1.5.1/radio 0.1.3.0** trial sent a generated ID in
legacy WLAN/activation fields and reached local HTTP. That is evidence for
those provisioning fields, not updated-original Prime pairing or native
MQTT. Neither original radio binary nor C2000 firmware has been recovered;
matching version strings cannot establish binary equivalence.

See the [Gen 2 local-identity trial](c1000-local-mqtt.md),
[original native trial](c1000-original-mqtt-followup.md),
[earlier original provisioning](c1000-original-wifi-validation.md) and
[C2000 binding follow-up](c2000-binding-followup.md).

## What the executed radio code checks

The new replay executes **29 synthetic cases** against the exact published
A1763 radio image, SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.

The actual TLV parser `4204f9a6`, activation handler `420537d8`, activation
copy routine `42052e48` and business callback `42049092` carry A2 account
bytes to persistent-configuration setter **`42011eda`**. Execution stops at
that setter's entry: storage, binding checks and network startup are outside
this replay. A freshly shaped local 40-character hexadecimal string reaches
the setter unchanged. Missing A2, API A3 or POSIX timezone A4 rejects with
status `04`; there is no Anker-issued-ID verification in this executed path.

This path also forwards uppercase hexadecimal, zero hexadecimal, nonhex,
short, 127-byte and present-but-empty account strings. Those are synthetic
boundary observations, **not supported setup domains**. Keep the SDK's
40-character hexadecimal requirement and use `secrets.token_hex(20)`.
Ascending tags preserve service A6; putting C3 before A6 hides the service.
Original-model country A5 is carried as an optional field in this A1763
code, which does not prove original-model firmware behavior.

Native JSON admission follows `42027f5e` / `42027c90` and the established
radio route. Payload account and serial must match stored values exactly.
Case changes, old account IDs, account prefixes/suffixes and absent identities
are rejected. Matching uppercase and lowercase IDs are separate accepted
cases when each matches its stored counterpart. Account mismatch invokes
identity-refresh providers, whose effects are substituted; it is not a
purely passive production probe. Account fields placed in the outer `head`
do not replace the payload identity. The selected accepted branch also
works without `sign_code`, consistent with the previous route audit.

Thus a locally generated identity is plausible for the two remaining models,
but cannot be certified by substituting their product names into A1763 code.
Successful native setup still requires local provisioning, stored account
agreement, correctly wrapped credentials, binding replies, time and mutual
TLS. It is not anonymous MQTT, and the public SDK rejects empty identities.
The replay does not prove a factory reset, account recovery or app coexistence.

## Safe next hardware sequence

Test the **original C1000 first**, separately from the HA deployment:

1. Save the working BLE/native profile, private certificates and radio network
   readback. Record fresh versions, power readings, protected settings and F8.
2. Pair a distinct generated Prime ID using the normal pairing workflow. If
   physical confirmation is required, wait for the user; do not reset the
   station or infer original button behavior from Gen 2.
3. Start an isolated local AP/API/broker with newly generated credentials and
   that same exact ID. Use A1761's country A5 and omit C3. Keep BLE connected
   through activation; disconnecting can trigger unsuccessful-setup cleanup.
4. Confirm local API requests, certificate-authenticated TLS, subscription and
   two fresh complete `0405` status reports after the startup grace. Send no
   preference/output command. A provisioning timeout alone is inconclusive.
5. Restart only the owned local server and confirm reconnect without another
   provision. Recheck all protected settings. Retain the new ID only after
   success; otherwise restore the saved network profile once and verify it.

For C2000, follow the same observation/restoration gates using its saved
generated BLE ID, A1783 layout and verified credential-response framing.
**Keep the working identity during HA deployment.** Schedule its identity
experiment separately: it powers servers, so never send an AC/DC-output,
shutdown, reset, timeout, charging, tariff or reserve command as part of
account testing. Network reconnection and before/after telemetry do not
measure uninterrupted inverter output.

## Reproduction and evidence

```sh
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  tools/firmware_analysis/emulate_radio_local_identity.py \
  --output /tmp/solix-local-identity
```

Assertions must be enabled. The tool validates image size/hash and emits
results plus a manifest with source hashes, runtime versions and limits.
Expected outputs are under `tools/firmware_analysis/expected_results/`.
Only synthetic identities enter those files; operational IDs and captures
remain ignored and private. OS/libc, allocation, JSON, session keys, check-code
and identity providers, identity-refresh effects, RSSI, crypto and transport
primitives are explicit host substitutes. This evidence extends acceptance
constraints; it does not add a newly verified hardware model.
