# Original C1000: remaining app power methods and negotiation limits

Offline audit dated **2026-10-02**. App evidence uses retained ARM64
`libapp.so`, SHA-256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
Firmware replay uses original **A1761 main 1.5.9**, SHA-256
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Installed **main 1.7.1** remains unavailable. No hardware, cloud, credential,
output or persistent-storage access was performed.

The result is a narrower discovery limit, not a new charging control:
the original model's inherited update-energy method requests telemetry, and
its library capability negotiation changes session state rather than
enumerating charging features. A remaining inherited port-memory method
supplies a model/version question, but no supported original setter is added.

## Remaining inherited app methods

The retained `A1761AnkerDevice` (`212a`) belongs to the `A1753AnkerDevice` /
`A1771AnkerDevice` family. Its retained controllers share builders, but a
method's presence in this family does not prove the app displays that feature
for A1761 or that a particular installed firmware accepts it.

| Method examined | Actual builder or scope | Meaning for further investigation |
|---|---|---|
| `setDeviceUpdateEnergy` (`04080ad8` → controller `04081780`) | Acts only when the model reports `wifiConnected`. Builds **`0057`**, empty positional data with `valueMap` **A2 = byte 1**, **A3 = uint32 300**; also clears an app-side cached member. | Requests the existing bounded realtime telemetry stream. The name does not establish charging/precharge or an energy reserve setter. |
| `setSosMode` (`045f3b60`) | Builds **`004f`** from the supplied integer, with model/controller branches. | This is the existing light-mode opcode, not an energy-management mode. Its method name alone supplies no charging behavior. |
| `setDeviceWaitTime` (`04787160`) | Builds **`0045`**, using `CmdUtil.intToList2`. | The already documented Device Timeout; no additional charging prerequisite is supplied. |
| `setAcOutputMode` / `setCarPortMode` (`03cfbed0` / `03cfc0d4`) | Delegates to the existing Smart-mode controller builders. | Saved output inactivity policies, whose counters and shutdown behavior are already documented. |
| `setMomenryPortSwitch` (`0314962c` → controller `03149688`) | Builds **`0079`** with `CmdUtil.intToList`, through the shared send method. | An inherited method candidate, not an established A1761 UI caller or physical behavior. **`0079` is absent from all three recovered 1.5.9 application/module/library tables.** No current-version setter/readback is proved. |

The normal 1.5.9 table inventory already contains 32 application handlers,
32 module handlers and 14 library handlers, preserved in
`c1000-original-commands.json`. Other table entries include update,
networking, reset and report operations; an unassigned name or response is
not evidence of a safe charging command. This audit does not enumerate their
effects or repeat the existing watts/Fast, gate, timer, SOC or second-input
replays.

Port-memory investigation would first need an A1761 app feature gate and
readback mapping, or the exact newer main firmware. The inherited builder
does not identify an equivalent old opcode or a reserve/bypass control.
No raw command sweep or idle rate write follows from this inventory.

The app's update-energy `valueMap` contains raw A2 `[01]` and A3
`[2c,01,00,00]`; `CmdUtil.intToList4` (`02273e68`) supplies four
little-endian duration bytes. That width differs from the original SDK's
documented typed-uint16 `0057` request. The lower app MQTT transformation and
installed 1.7.1 acceptance of this specific app form were not observed here.
It does not change the supported SDK reporting-window builder or limits.

## Capability negotiation is a separate namespace

`A1771AnkerDevice.capabilitiesNegotiation` at **`036cfd30`** uses the
shared **`[00,03]`** opcode constant. Its call to
`ZXCommandTransformer.formatCommand` (`02275b34`) omits `functionType`;
the formatter defaults that value to **`01`** at `02275b94`. Encryption and
secure-interaction booleans come from the device model. This is distinct from
radio **function `10` / `0003`** wireless status, even when BLE encryption
changes an opcode's high byte.

The shared payload builder **`036cf9ec`** writes timestamp, a conditional
user-ID/empty member, and fixed parameter arrays `[32]` and `[0,240]` through
`BleWritePayloadHelper`. It is not an empty getter request. The app's shared
response parser (`02425398`) also supports decryption and encryption-scheme
processing. Its presence is not an original charging-capability schema.

## Actual main 1.5.9 handler and response

The recovered library table selects **`08017fa0`** for function `01`,
command `0003`. The new replay runs the real slot parser **`0801df4c`**,
lookup **`0801df26`**, dispatcher **`0801dfac`**, handler and serializers.

The handler looks up raw **A3** and **A4** slots, signaling an error when either
is absent, then reads
the first two backing bytes from A4 as a little-endian word. It calls a
registered provider through **`2000640c`**, which supplies another word.
The successful response is:

```text
00  A1 01 00  A2 02 <min(requested_word, provider_word), uint16 LE>
```

There is no watt setting, reserve percentage, list of supported charging
opcodes or charging-status field in this examined reply. The request/response
looks like a negotiated protocol limit; its exact physical/application units
are deliberately unassigned here.

After building the reply, **`08018036..0801803a` writes 1 to
`200009d4 + context_source`**. Thus the handler is not a read-only feature
query: it marks per-source library session state. The complete synchronous
low-RAM comparison in every executed case permits only that byte change;
saved settings, charging/output flags, timers and BMS/DSP caches remain exact.
That comparison does not execute later session processing, the radio, DSP or
relay, or establish physical output continuity.

### Error branches do not necessarily end processing

These are synthetic handler boundaries, not supported request forms:

| Synthetic condition | Actual replayed result |
|---|---|
| A3 missing, A4 contains 123 | Sends error `04`, then the ordinary negotiated reply for 123 and marks the session byte. |
| A4 missing, A3 has two backing bytes for 77 | Sends error `04`, then consumes the retained A3 pointer, replies for 77 and marks the session byte. |
| A4 declared length 0 or 1 | Handler still reads two backing bytes; supplied adjacent synthetic bytes affect the selected word. |
| Capacity provider writes 256 but returns failure | Sends error `01`, then the ordinary negotiated reply and marks the session byte. |
| No registered provider, synthetic stack seed 123 | Reply uses that seed. Application callback registration was not run; this is not a claim about an initialized station. |

The slot lookup checks presence, not the handler's required data width.
The missing-field/provider error calls return to the enclosing handler, which
continues. Complete outer radio admission, authentication, protection against
these malformed forms and newer-firmware behavior were not executed.
Unmapped-pointer cases are intentionally absent from the suite and are not
proposed as live tests. A response or error alone would not prove that this
handshake left session state unchanged.

## Reproduction

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_capability_negotiation.py \
  --output /tmp/original-capability-negotiation-results.json \
  --manifest /tmp/original-capability-negotiation-manifest.json
cmp /tmp/original-capability-negotiation-results.json \
  tools/firmware_analysis/expected_results/original-capability-negotiation-results.json
cmp /tmp/original-capability-negotiation-manifest.json \
  tools/firmware_analysis/expected_results/original-capability-negotiation-manifest.json
```

**39 new cases pass:** 32 requested/provider-limit and source combinations,
four malformed-shape boundaries, two provider failures, and one explicitly
synthetic missing-provider state. Two independent runs matched results and
manifests byte-for-byte. Manifests record source/dependency, firmware and
result hashes, plus Python/Unicorn versions; matching environments are needed
for byte-identical manifest comparison.

Only the capacity provider and final response-body transport are substituted.
Peripheral/flash CPU writes and unexpected ACK/persistence boundaries are
rejected. Main startup scatter initialization runs, but complete application
registration and session processing do not. App evidence is static disassembly
and object-pool comparison, not additional CPU emulation cases.
Private excerpts remain in ignored
`.solix-private/original-http-wrapper-20261002/`, directories 700/files 600.

For current-version charging evidence, retain the existing
[below-full native test plan](c1000-native-charging-test-plan.md).
The missing main 1.7.1 artifact and an independently useful loaded charging
interval remain the concrete next prerequisites; this audit identifies no
new external charging-pause or battery-only command.
