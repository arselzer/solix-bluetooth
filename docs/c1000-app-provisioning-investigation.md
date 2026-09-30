# Original C1000: app-derived Wi-Fi provisioning

## Result and scope

The retained Android app's Dart snapshot now establishes a concrete **A1761
original C1000** provisioning path. Its inherited implementation sends Wi-Fi
credentials with `0024` and activation/API configuration with `0025`, using
function `0f` and the negotiated encrypted Bluetooth transport. The encrypted
wire opcodes are consequently **`4024` and `4025`**.

This is an **offline app-code finding**, dated 2026-09-30. No station command,
cloud request using app credentials, Wi-Fi change or binding change was made
by this investigation. It does not establish that the original radio accepts
our isolated API's certificate response or connects to our local MQTT broker.
The existing [original-model firmware investigation](c1000-legacy-network-investigation.md)
remains relevant to that separate question.

**Later hardware follow-up:** [Wi-Fi and endpoint replacement succeeded](c1000-original-wifi-validation.md)
on original main 1.5.1/radio 0.1.3.0 using a generated local ID. The original
radio's TLS/MQTT credential compatibility still remains unproven.

The analyzed `libapp.so` is an Android ARM64, compressed-pointer, product
snapshot for **Dart 3.11.0**, snapshot hash
`78da37fed6bf1489361a312568249f3f`. Input SHA-256:

```text
8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070
```

The app and its raw recovered code remain private. This document publishes
protocol structure and synthetic observations only.

## Why this applies to A1761

The recovered snapshot class/type tables give this inheritance chain:

```text
A1761AnkerDevice
  → A1753AnkerDevice
  → mixin(A1771AnkerDevice, BleOtaField)
  → A1771AnkerDevice
  → shared PpsDeviceFeature / ZXBleFeature
```

The A1761 allocation stub at `02429c78` independently encodes class ID
**8490**, matching the parsed class record. A1761 and A1753 have no recovered
override for the two methods below. More specifically, the inherited A1771
methods include branches accessing **A1753DeviceController.transformerZx**;
this is stronger evidence than merely finding similarly named methods in an
unrelated model.

| Method | App virtual address | Relevant call |
| --- | --- | --- |
| `A1771AnkerDevice.connectWifi` | `03665bdc` | `03665c14` → shared builder `03667260` |
| `A1771AnkerDevice.activateWifi` | `036c09fc` | `036c0a34` → shared builder `036c1e8c` |
| `BleWritePayloadHelper.writeParam` | `023b4fb4` | Appends tag, one-byte length, then data |
| `ZXCommandTransformer.formatCommand` | `02275b34` | Stores opcode, payload and transport flags |
| `ZXCommandTransformer._getNormalCommand` | `02273fec` | Encrypts payload and assembles frame |

Addresses are app ELF virtual addresses, **not controller firmware addresses**.
The decompiler's pseudocode contains inaccurate control-flow expressions in
some methods; the findings here were checked against instruction output,
resolved constant arrays and bounded instruction replay.

## Credential request: `0024`

The builder starts its tag counter at `a1`. Each field is an **untyped TLV**:
`tag`, `length`, `data`. There is no integer/string type prefix inside the
value, and there is no leading control-source TLV `a1 01 21`.

| Tag | Value |
| --- | --- |
| `A1` | Current UTC Unix seconds, four bytes little endian |
| `A2` | Account ID from `BleInfoHelper.accountId`, UTF-8 bytes |
| `A3` | Supplied SSID, UTF-8 bytes |
| `A4` | One byte `00` |
| `A5` | One byte `00` |
| `A6` | Supplied password, UTF-8 bytes |

`CmdUtil.getTimeStamp` at `023b524c` obtains time and divides microseconds
by 1000 twice; `CmdUtil.intToList4` at `02273e68` writes little-endian bytes.
`CmdUtil.getUserId` at `023b4f78` performs UTF-8 encoding. The two fixed zero
fields are explicit array construction in `03667310..0366738c`.

The isolated builder does not enforce SSID/password validity. Empty and
Unicode values in the replay demonstrate serialization only. They do not
establish valid WLAN inputs, supported passphrase encodings, or radio limits.
Keep the SDK's conservative WLAN validation. The app uses a runtime account
ID; this analysis does not prove that a generated local ID is accepted by the
original radio, even though that worked on tested Gen 2 models.

## Activation request: `0025`

The shared asynchronous builder emits these fields in ascending order:

| Tag | Value/provider |
| --- | --- |
| `A1` | UTC Unix seconds, four bytes little endian |
| `A2` | Account ID, UTF-8 |
| `A3` | `RequestApi.currentBaseUrl`, UTF-8 |
| `A4` | `DeviceUtil.getCurrentTimezoneGMT()` result, UTF-8 |
| `A5` | Current region's country code, UTF-8; null falls back to `US` |
| `A6` | Literal `anker_power`, UTF-8 |
| `A7` | Supplied product-code argument, UTF-8 |
| `A8` | `DeviceUtil.getCurrentTimeIdGMT()` result, UTF-8 |

The A1761 product getter returns its model code; the builder itself accepts
the product argument rather than embedding a different model's code.
Timezone providers use app/platform data and mapping helpers. The replay
substitutes their returned strings; it does not validate every timezone or
exercise those providers' external dependencies. The builder also requests
app-local timezone-cache persistence after constructing the payload.

**This path includes `A5` and does not emit `C3`.** At the time of this audit,
the established Prime Gen 2 SDK activation builder omits `A5` and appends an
opaque `C3` field. Do not copy that builder unchanged into an original C1000
test. The absence claim is limited to this recovered shared app builder;
it does not establish that original firmware rejects all extra fields.

## Framing and reply routing

The inherited senders supply constant opcode arrays `[00,24]` and `[00,25]`,
`functionType=15`, `isEncrypted=true`, and the device's secure-interaction
flag. The framing helper selects CBC or GCM from the negotiated state. The
original station's existing P-256/AES-CBC session implementation remains the
transport authority; this replay does not perform cryptography.

For an encrypted payload of length `N`, the recovered frame is:

```text
ff 09  (N+10 as uint16 LE)  03 00 0f  40 24-or-25  ciphertext  XOR
```

`022744a4` XORs `40` into the opcode's first byte when encryption is enabled
with usable session state. `022745f0` computes the XOR checksum over the
frame bytes, with the final checksum slot initially zero. This is **XOR,
not an additive checksum**.

The charging opcode map at `02420104` pairs:

| Plain opcode | Enum | Parser |
| --- | --- | --- |
| `0024` / `0824` | `connectWifiReq` / `connectWifiRes` | `parseConnectWifiData`, `02423588` |
| `0025` / `0825` | `activateWifiReq` / `activateWifiRes` | `parseActivateWifiData`, `02423154` |

The two `parse...` functions are **reply parsers**, not request serializers.
After optional session decryption, both preserve the first payload byte as
the reply-status value under their `A0` key. Activation also preserves bytes
from offset 3 onward under `A1` when the decrypted payload has at least four
bytes. This establishes indexing, not meanings for undocumented error codes
or those additional bytes.

These app opcodes must not be conflated with the recovered controller's
**function-01** library commands `0024`/`0025`. Its function-10 `0824`/`0825`
module acknowledgements are consistent contextual leads, but neither this
app analysis nor the main-MCU table recovers the original radio's HTTP/TLS
credential parser.

## Verification and retained tooling

The [AOTopsy static parser](https://github.com/BroNils/aotopsy) recovered
**124,032 function-name entries** from the local snapshot. It was built
locally using Go **1.25.4** and `golang.org/x/arch` **v0.23.0**. Only public
tool sources/dependencies were downloaded; no app material was uploaded.
A small local wrapper exported selected instructions, class/type records,
constant arrays and object-pool metadata. Its raw outputs remain private.

**Ten synthetic Unicorn 2.1.4 replay cases pass**:

- Four credential builders: ASCII, Unicode, empty values, and a 32-byte SSID
  with a 63-byte password.
- Two activation builders: explicit country and null-country fallback.
- Four final frames: each opcode with CBC and GCM selection, using fixed
  dummy ciphertext and the actual app frame/checksum instructions.

Actual builder instructions, `writeParam`, frame construction and checksum
execute. Allocation, collection-library operations, UTF-8 conversion,
account/time/API/timezone/region providers, asynchronous scheduling, logging
and app-cache persistence are substituted. Encryption returns a fixed
synthetic byte array. No app process, radio firmware, BLE stack or network
service executes. This proves bounded serializer behavior, not pairing,
encryption correctness, device acceptance or MQTT operation.

Private reproduction files are under
`.solix-private/c1000-app-provisioning-20260930/`: `solixlookup.go`,
`audit_metadata.py`, `replay_builders.py`, metadata assertions and replay
results. The manifest records hashes. Reproduction needs the exact private
app input and local analyzer metadata; neither is a public dependency of the
SDK or test suite.

## Next falsifiable step

An explicitly approved original-C1000-only trial can now use its existing
CBC session and the **app-derived A1–A8 layout** to join the isolated AP,
observe `4824`/`4825`, and record which local API requests actually arrive.
Start with unchanged output settings and a recorded/restorable baseline.
Treat WLAN association, API request shape, TLS behavior, credential parsing
and MQTT authentication as separate observations. Do not infer any of them
from a successful BLE acknowledgement alone.

If that trial cannot reach the API, the useful missing prerequisite is an
original-radio image or a corresponding private plaintext trace—not more
speculative command variants. No new original-model discharge, charge-cap
or reserve capability is established by recovering its networking path.
