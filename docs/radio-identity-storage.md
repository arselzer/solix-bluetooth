# Local native identity storage and rollback

## Scope

On **2026-10-02**, **38 synthetic instruction cases** extended the previous
[activation/admission audit](account-free-local-setup.md) through configuration
storage, account getter, MQTT parameter copying and the binding-check callback.
The input is the published **A1763 C1000 Gen 2 radio 0.3.3.0** image, SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.
No device, SSH, Bluetooth, MQTT or cloud request was made, and no saved profile
was changed.

This binary is not the original C1000 or C2000 binary. Its behavior provides
testable hypotheses, not newly verified compatibility for either model.
Matching original radio version `0.3.3.0` does not establish code identity.

## Configured identity and active MQTT cache differ

Actual setter `42011eda` validates supplied strings through printable-ASCII
checker `4201b27e`. It accepts the tested local lower/uppercase hexadecimal,
short, printable nonhex and empty account strings, but rejects control and
non-ASCII bytes without replacing the account. Keep the SDK's **40-character
hexadecimal** requirement: accepting empty configuration does not establish
working anonymous setup.

Account changes update both configuration RAM at **`3fc86ff0`** and its runtime
mirror at **`3fc86c88`**. A persist request reaches the flash-write boundary
through `420115a4`; a nonpersisted change sets deferred dirty byte `3fc902c8`.
Null and unchanged account inputs create no write. Actual getter `420122f4`
returns the configured account through the configuration-copy path.
Flash operations and mutexes are recording/success substitutes; no actual
nonvolatile persistence or device reboot was tested.

The setter does **not** immediately replace active MQTT account cache
**`3fc89f08`**. Actual parameter routine `42028484` checks required nonempty
fields, skips an identical record and copies a changed record before its
flash-write call. Empty account input returns `90004` before copying.
The replay supplies its record directly; upstream record construction,
subsequent storage, MQTT startup and TLS are outside this test.

Native admission first tries the cached account; on mismatch it can call the
actual configured-account getter after substituted refresh providers. Thus
this synthetic cycle produces:

| Phase | Configured account | Cached account | Accepted native identities |
| --- | --- | --- | --- |
| Baseline | Previous | Previous | Previous |
| After account storage | Local | Previous | Both |
| After MQTT parameter reload | Local | Local | Local |
| After configuration restoration | Previous | Local | Both |
| After restored MQTT reload | Previous | Previous | Previous |

The full configuration block matched its baseline after the cycle, with two
recorded configuration writes. Identity-refresh effects remain substituted,
so real transition timing and provider side effects are unestablished.
A successful new-ID read on an existing connection is insufficient evidence
of durable onboarding or complete old-ID revocation. Require reconnect plus
fresh status after changing or restoring identity.

## Binding-response constraints

The actual binding-check callback **`4201fb34`** and common response checker
execute with substituted JSON primitives. Nine cases vary numeric
`bind_state` and `relate_state` through 0, 1 and 2:

- `relate_state != 0` bypasses these unbind/reset branches.
- `bind_state=1, relate_state=0` reaches unbind-related `4201fa80`.
- Both zero reach reset/unbind-related `4202bf3a(0)`.
- `bind_state=2, relate_state=0` reaches neither recorded branch.

These effect functions are recording stubs; their downstream behavior is not
executed. **Do not use zero binding states as harmless diagnostic variants.**
Missing data/relate returns `-6`; missing bind returns `-7` in these fixtures.
Ordinary code `10000` records error callbacks even with both states present.
The callback's complete successful fixtures retain return sentinel `-1`, so
that return alone is not a binding boolean.

With numeric states `1/1`, absent, matching and different `data.account`
produce the same selected behavior: this callback does not establish account
ownership by checking that extra field. Return valid complete `code=0`,
`msg=success` responses with the established state fields. This does not
establish that an existing main-controller binding flag changed.

## Smallest reversible original-C1000 experiment

Keep the known working **Prime BLE pairing ID** and test only the **native
provisioning account** first. They are separate fields in the tool; retained
C2000 live trials already used different BLE and native IDs. The A1763 replay
does not execute the BLE pairing/allowlist path, so their separation still
needs confirmation on the original hardware.

1. Save the working target profile and complete private radio network readback,
   including API URL, service/product, country and timezone; record two fresh
   original `0405` reports and all protected settings/F8. Preserve its working
   native identity and certificate material for rollback.
2. During a separate target-only maintenance trial, reuse the local WLAN,
   endpoint, timezone and certificates. Change only account A2 in the normal
   `4024`/`4025` sequence and the corresponding local broker/API profile.
   Use A1761 country A5 and omit C3. Do not change BLE registration identity.
3. Keep BLE through activation and use the model's startup grace. Verify local
   API account, mutual TLS, subscription and two full fresh `0405` reports with
   the generated account. Restart only the owned test server and repeat.
4. Restore the prior provisioning account/profile, require native reconnect
   and two fresh reports, then independently reconnect with saved Prime BLE
   credentials and verify settings. Do not interpret timeout as no change.

Do not alter a peer's identity while the shared HA deployment is under test.
This experiment isolates original native account independence; even success
does not prove generated-ID Prime BLE pairing. Test that separately afterward,
with physical confirmation available and no reset. C2000 already has a
generated BLE credential, but its native identity trial likewise needs its
own saved-profile rollback and reconnect gates. Never send C2000 output,
shutdown, reset, timeout or power-setting commands for this investigation.

## Reproduction

```sh
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  tools/firmware_analysis/emulate_radio_identity_storage.py \
  --output /tmp/solix-identity-storage
```

The tool validates the exact image and emits results plus a source/runtime
manifest. Expected outputs are under `tools/firmware_analysis/expected_results/`.
Assertions must remain enabled. All identifiers are synthetic; operational
data stays ignored and restricted. The manifest lists all host substitutes.
