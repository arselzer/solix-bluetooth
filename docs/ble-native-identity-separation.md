# BLE registration and native account separation

## What this investigation establishes

On **2026-10-02**, **32 synthetic instruction cases** replayed BLE authorization,
Prime registration, native account changes and explicit BLE account saves in the
published **A1763 C1000 Gen 2 radio 0.3.3.0** image. Image SHA-256:
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.
No hardware, SSH, MQTT, Bluetooth or cloud connection was opened. Operational
profiles and the shared HA deployment were untouched.

**A retained BLE registration ID survives the executed native account-change
prefix and cache reload. This is not a complete hardware rollback guarantee.**
Normal WLAN provisioning and activation are deliberately stopped at the
boundaries below. Their remaining networking, binding and asynchronous effects
are unexecuted. Neither the original C1000 nor the C2000 radio binary has been
recovered; a matching original-C1000 `0.3.3.0` version does not prove equivalence.

## Two independent records in the executed A1763 paths

Native activation account A2 goes through actual configuration setter
`42011eda`, which writes account RAM at `3fc86ff0` and its runtime mirror.
The MQTT parameter cache at `3fc89f08` is separately reloaded; see
[the storage/cache audit](radio-identity-storage.md).

BLE registration instead uses persistent record **`ble_user_accounts`**:

| Property | Observed implementation |
| --- | --- |
| RAM block | `3fc8b5bc`, length `310` hexadecimal |
| Header | Magic `12345678`, version 1 |
| Capacity | 16 accounts, 48 bytes per slot |
| Count / next slot | Block offsets 8 / 9 |
| First slot | Block offset `0e` hexadecimal |
| Add / clear / lookup | `4204a4f8` / `4204a5da` / `4204a658` |
| Persist boundary | `4204a398` calls record writer `42014ee6` |

The actual application authorization callback `420435d6` consults this list
with exact, case-sensitive comparison. It does not fall back to the native
configured account getter in the executed path. Retained IDs pass; generated
native-only, case-mismatched, shortened, empty and null IDs fail. Complete
allowlist bytes remain unchanged during all these lookups.

## Normal `4024` / `4025` versus explicit save-auth

These paths must not be conflated, even where command numbers overlap another
function namespace:

| Path | Instructions executed | BLE list result / boundary |
| --- | --- | --- |
| WLAN credentials `4024` | TLV parser, handler `42053684`, credential copy `42052d16` | Unchanged through application callback entry; callback is unexecuted |
| Native activation `4025` | TLV parser, handler `420537d8`, activation copy `42052e48`, application callback `42049092`, configuration setter | Unchanged through setter return at `420491c6`; later effects are unexecuted |
| Prime registration opcode `27` | Encrypted/session checks and actual handler `4204e57a`, actual security getter and BLE checker | Retained ID passes; generated native-only ID requires registration |
| Explicit save-auth callback | Direct invocation of actual `42042abe`, actual list add/clear/persist decision | Adds in modes 1/2; clears then adds in modes 3/4 |

The core registration callback table at `3fc8bd94` supplies the BLE checker at
offset `18` and save-auth callback at offset `20` hexadecimal. The pointers are
reconstructed from the actual application startup instructions. Prime's
physical-confirmation registration branch calls that save-auth pointer. The
tested **normal `4024`/`4025` prefixes do not call it**. Its reachability through
unexecuted later activation or asynchronous effects is not established here.
It is a registration callback; the mode-3/4 log describes password mode, which
does not make it a normal native-account setter or prove it is only reached
through a change-password operation.

With an existing encrypted synthetic session, retained registration returns
status `00` in modes 1 and 2 and reaches port authorization. A native-only new
ID returns `01` in mode 1 with the physical window closed, or `09` with the
confirmation timeout TLV in mode 2. Confirmation/state effects are substitutes,
so this does not test a real button, advertisement lifetime or handshake.
Unencrypted registration is rejected before the allowlist check.

## Account-change and recovery replay

Both direct account-only storage and normal activation-prefix replay perform:

1. Previous native account and retained Prime ID.
2. Generated native account stored, with previous MQTT cache.
3. Generated account copied to the MQTT parameter cache.
4. Previous native account restored, with generated MQTT cache.
5. Previous account copied back to the cache.

At every phase, both retained BLE IDs still authorize and the generated
native-only ID still requires BLE registration. The entire `310`-byte allowlist
matches its baseline, with **zero BLE record writes**. Both cycles restore the
native account and record two configuration writes. Only the account-only cycle
asserts restoration of the entire native configuration block; activation also
supplies endpoint, timezone and service fields.

These results make retaining the old Prime ID a useful recovery hypothesis.
Flash operations, mutexes, status/MAC providers, state effects, session-ready
checks and physical response transmission remain substitutes. No flash readback,
power cycle, BLE advertising recovery or physical output behavior is verified.

## Explicit operations that can erase recovery credentials

Direct save-auth cases in modes 3 and 4 execute list clear **before** adding the
new ID, producing two recorded BLE writes. Modes 1/2 preserve the existing two
entries and add a third. Empty/null save inputs return 1 without writing, so the
callback's return value alone does not prove an account was stored.

At capacity, mode-2 add uses a ring: saving a seventeenth ID evicts the oldest
retained ID while preserving the other fifteen. A separate generated Prime
registration trial therefore has different recovery risks from a native-only
account trial, even without password security mode.

Static public-firmware references also locate explicit application function
`10` opcode `07` unbind handler `42043d5a` calling list clear, and opcode `56`
clear-info handler `4203d0ca` with list-clear branches. Those handlers were not
executed in this replay. Do not send unbind, clear-info, security-mode, password
or new-registration writes to test native-account independence.

## Smallest future hardware experiment

Do not mutate identities used by the working HA deployment now. First obtain
independent recovery access to the selected device: fresh BLE advertising and a
successful read with its retained Prime ID, plus physical IoT-button access if
that model requires it. A native MQTT connection alone does not satisfy this.

Save the complete working private profile and radio network settings. During a
target-only maintenance session, preserve the Prime ID and change only native
account A2 with normal model-specific `4024`/`4025` provisioning. Test the original
C1000 first: verify local bootstrap, MQTT subscription and two complete fresh
`0405` reports. A separate Gen 2 trial uses fresh `0421`/`0900` telemetry instead.
Restart only the owned target server and repeat. Restore the native account/profile, reload
parameters, reconnect and verify fresh reports, then independently verify Prime
BLE access again. Keep the original C1000's A1761 country/timezone rules.

Original-C1000 generated native activation and C2000 generated native activation
still require **their own hardware tests**. Generated Gen 2 BLE registration and
generated C1000 Gen 2 native onboarding were verified in earlier sessions; this
replay does not add hardware verification. Never toggle C2000 AC or use its
power, reset, shutdown or timeout settings for this identity experiment.

## Reproduction

```sh
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  tools/firmware_analysis/emulate_radio_ble_identity_separation.py \
  --output /tmp/solix-ble-identity-separation
```

The harness validates the exact firmware, emits sanitized results and records
source/runtime hashes and substitution limits. Expected result/manifest files
live in `tools/firmware_analysis/expected_results/`. All fixture IDs, AP details
and credentials are synthetic. Disassembly and operational material stay in
ignored `.solix-private/` with restricted permissions.
