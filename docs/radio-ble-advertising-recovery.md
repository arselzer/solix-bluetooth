# Native MQTT and BLE advertising recovery

## Scope and result

The public **A1763 C1000 Gen 2 main 1.1.4.9 / radio 0.3.3.0** image has a
normal radio-local MQTT command that reaches its BLE-enable helper. This is
**offline instruction evidence**, not a tested recovery feature. Neither the
original C1000 nor C2000 radio binary has been recovered; a matching version
label or app command number does not establish identical behavior.

Exact image SHA256:
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.

Fifty synthetic cases execute native JSON admission, raw callback, normal
dispatch, relevant handlers, initialized SDK BLE-enable logic, bitmap updates,
fast-advertising timer and response builders. NimBLE, OS services, configured
advertisement data, optional MAC/text providers and physical transport are
substitutes. No station, cloud, credentials or phone capture is accessed.

## Exact normal command boundary

| Purpose | Function / opcode | Handler | Behavior |
| --- | --- | --- | --- |
| Enable BLE | `10 / 0024` | `4203d056` | A1's first byte: nonzero enables; zero disables |
| Wireless flags | `10 / 0003` | `4203cc10` | Reads application BLE/Wi-Fi flags; other status tags |
| Wi-Fi mutation | `10 / 0023` | `420432aa` | Changes network/binding state; **not a query** |
| Broad wireless setter | `10 / 0025` | `42043316` | Can enable/disable BLE, close MQTT/Wi-Fi and alter binding-related state |

Use only the strict synthetic enable body **`a1 01 01`** for a future trial.
The firmware checks A1 presence but does not require length 1 or restrict its
value to 0/1. Values 2/255 also enable. A zero-length A1 consumes a following byte
in the replay and still produces success; callers must reject malformed fields.
Missing A1 produces response status 01 without an enable/disable call.

The native raw callback (`42043a18`) normalizes encryption flags and dispatches
opcodes through 0x3f locally. Higher opcodes reach controller-forward wrapper
`420439cc`. Consequently `004b`'s BLE-connected getter is **not** a radio-local
native query. Adding its table address alone would select the wrong namespace.

## Framing and request matching

The bounded candidate uses outer pattern **`03 00 10`**, opcode `00 24`, body
`a1 01 01`, full wire `ff090d000300100024a101016d`.

It is admitted on `cmd/anker_power/A1763/<synthetic-serial>/req` with the existing
native account/serial and an envelope head containing `cmd:17`, `cmd_status:2`,
`sign_code:1`. `payload` is a JSON **string** containing `account_id`,
`device_sn` and base64 `data`. Wrong synthetic account/serial never reaches the
BLE handler. This command does not bypass identity admission.

The cleartext reply is **`03 00 10 / 08 24 / 00`**, full wire
`ff090b00030010082400c2`. The native uplink uses `dt/.../param_info`, head
`cmd:16`, `cmd_status:1`, `sign_code:0`, `seed:"null"`; its string payload's
base64 `data` contains that wire. Established synthetic GCM requests produce
encrypted `4824` replies. Destination bytes 0/1/2/3 reach the handler and are
preserved in the reply; destination 0 is the narrow documented candidate.

The controller builder is unsuitable: it uses function 0f and prepends source
tag `A1=22`. A separate, [live-validated read-only adapter](c1000-gen2-native-wireless-state-validation.md)
now handles `0003` with the exact radio pattern and per-request matching.
It exposes no `0024` setter. Reusing the controller helper could enable BLE
through its prepended source byte and obscure the intended value.

## Executed effects and retention limits

For an already initialized SDK BLE path, `0024` calls `42029120`. It clears
bitmap bits 0x06, rebuilds selected advertisement bits from existing BLE info,
calls the PAL enable path and sets volatile SDK-enabled byte `3fc903fd=1`.
It starts the fast-advertising timer with counter 0 and limit 180. Repeating the
command resets that counter. Initialization registers its callback at 1000 ms;
the bounded timer compares the **previous** counter using `>180`, so it must
not be described as an exact 180-second deadline. Expiry requests interval 500
at `42037a28`, clears the fast timer and stops its OS timer when appropriate;
it does **not** disable physical advertising in this executed path.

PAL state 1 reaches the physical advertisement-start boundary and becomes 2.
State 2 starts again only when the substituted active provider reports false.
State 3 reaches a physical disconnect boundary; a failed synthetic disconnect
leaves state 3. State 0 starts no advertisement. **All receive status 00.** An ACK
therefore proves command handling, not that the station became discoverable.
The candidate can disturb an existing BLE connection.

No executed initialized-path instruction forwards an MCU output command or
changes the native configuration, runtime account, MQTT identity cache or BLE
allowlist regions. It also leaves the MCU-facing application BLE/Wi-Fi bytes
`3fc90646/47` unchanged. No persistent BLE preference was demonstrated. Physical
callbacks, delayed controller policy, first-time SDK initialization, flash,
real radio start errors and reboot retention remain outside the proof. These
limits prevent a general hardware safety or recovery guarantee.

`0003` replies status 00 then A1=application BLE flag and A2=application Wi-Fi
flag. Independent synthetic PAL states 1/2/3 yield the same selected flags. It
cannot establish whether BLE is physically advertising, connecting or
connected from this replay alone. The later
[47-case producer/initialization audit](radio-ble-initialization-activation.md)
finds BLE connection-state writers; advertising enable does not write this flag.
The [23-case query audit](radio-native-info-queries.md) also corrects optional
A4 to configured network text, which this older replay substituted. A later
MCU update could overwrite the current radio condition.

## Activation, IoT button and a future bounded trial

The static image contains separate SDK advertise/link timers, pairing-window
state and wireless lifecycle callbacks. Direct enable/disable callers include
normal BLE setters, broad wireless controls and restart/unbind paths; observing
BLE disappearance after provisioning does not identify which asynchronous
path caused it. This replay does not reproduce successful native activation's
whole callback graph or a physical IoT button. It does not establish an opcode
for opening a pairing window, changing registration or querying physical
advertising. Device Timeout Never is not evidence of any of those conditions.
The separate initialization/activation follow-up executes additional normal
instructions, while retaining physical, persistence and asynchronous boundaries.

Qualify **C1000 Gen 2 only**, while it already has fresh native telemetry and
a retained known-working Prime identity. Have physical IoT-button recovery
available before testing; the user's absence currently prevents that fallback,
so hardware testing remains deferred. Record current
outputs, identity/profile fingerprints, modes, limits and timers privately;
confirm independent BLE scanning works. Read `0003`, then issue **one** strict
`0024 / a10101` request using the separate radio adapter. Check its exact reply,
fresh MQTT telemetry, unchanged protected settings and a new BLE advertisement.
If visible, connect using the retained identity and perform only read-only
telemetry. Preserve all observations and disconnect normally afterward.

Stop on missing ACK, lost native telemetry, unexpected identity/profile/state
change or unavailable advertisement. Do not retry automatically, send zero,
use `0023/0025`, clear IDs, unbind, use factory namespaces or re-provision.
Physical IoT-button access is still the recovery fallback if the candidate
fails. Do not transfer this experiment to original/C2000 using version-label
equivalence; C2000 AC output remains protected throughout.

## Reproduce

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_ble_advertising.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/solix-radio-ble-advertising
```

Optimized Python is rejected because it disables assertions; wrong image size,
filename or hash also fails before emulation. The manifest hashes the source and
its two replay dependencies. Install the versions in
`tools/firmware_analysis/requirements.txt` into an
isolated environment if needed. Compare the two JSON outputs byte-for-byte
with `tools/firmware_analysis/expected_results/radio-ble-advertising-*`.
Public fixtures contain synthetic identifiers only; raw disassembly notes stay
in the ignored private directory with restricted permissions.
