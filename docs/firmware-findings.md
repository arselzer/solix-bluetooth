# Gen 2 firmware findings

The latest [reconnect, clock and power-gate follow-up](c2000-mqtt-reconnect-and-tariff.md)
includes additional offline executions and guarded C2000 hardware observations.
The [parallel power-control analysis](mqtt-power-offline-followup.md) adds
73 actual-code cases for clock synchronization, complete tariff selection and
native charge-limit handlers, including reserve side effects and diagnostic limits.
The [schedule encoding audit](c2000-tou-encoding-audit.md) corrects the earlier
C2000 slot layout and invalidates claims that those trials installed valid
all-day Peak plans. The [DSP investigation](inverter-dsp-investigation.md)
verifies all 159 inverter image blocks and traces the AC-input readiness producer.

## Scope and evidence

These findings come from offline analysis of the owner's retained **C1000 Gen 2
(A1763) update to 1.1.4.9**, captured on 2026-09-28 and analyzed on 2026-09-29.
The radio application identifies itself as **v0.3.3.0**. We have not recovered
the C2000 firmware, so identical behavior on that model remains unproven.
Firmware analysis used retained local files; separate live network probes are
documented below and in the linked investigation. No firmware was flashed.

Raw images, phone captures, disassembly, and reproducible extraction scripts
remain in the ignored, owner-only `.solix-private/firmware-analysis/` directory.
This document contains protocol findings and code addresses, without device or
account identifiers. See [live protocol observations](gen2-protocol.md) for
features already tested on hardware.

## Recovered components

The `4064` transfer contains 7,302 unique 204-byte chunks and 10 identical
retransmissions. The declared package length is 1,489,513 bytes. Its manifest
starts at offset 12 and contains six 32-byte entries: offset, size, check value,
four version bytes, and a 16-byte name.

Component data uses a repeating 256-byte XOR mask: entry `i` is the CRC-8 of
the single byte `i`, with polynomial `0x31`, initial value zero, and no reflection
or final XOR. The mask restarts at each component. This is independent of the
BLE session encryption removed when reconstructing the transfer.

| Component | Manifest version bytes | Size | Independent integrity check |
| --- | --- | ---: | --- |
| MainMcu | `1.1.4.9` | 198,656 | CRC-16/MODBUS matches `9e8d` |
| MainBMS | `1.0.4.2` | 104,448 | CRC-16/XMODEM matches `b5cb` |
| dspACDC | `5.0.6.0` | 80,896 | All 76 nested record CRC-16/MODBUS checks match; whole-component check unresolved |
| dspDCDC | `5.0.6.0` | 87,040 | All 83 nested record CRC-16/MODBUS checks match; whole-component check unresolved |
| lcd | `0.1.9.6` | 925,696 | Sum of decoded bytes matches `034edbc6` |
| SW2505PD | `3.1.0.255` | 91,685 | Decoded vector table; checksum method unresolved |

Version columns show manifest bytes, not necessarily app display formatting.
The outer package checksum/trailer remains unverified. The separate radio
image's embedded SHA-256 matches. MainMcu contains ARM Thumb code loaded at
`0x08005000`; the radio contains RISC-V code.

## Can the firmware be modified and installed?

The recovered images can be analyzed and edited offline. Installing an edited
image is a separate, unresolved problem. On the C1000 radio, there is concrete
evidence of **cryptographic update verification**, beyond the transport checksum:

- The retained 1,482,800-byte transfer contains an Espressif V2 signature block
  at offset `0x169000`, followed by unused signature space and 48 transfer bytes.
- Its block CRC-32 and SHA-256 of the preceding padded image both match.
- Its **RSA-3072/PSS signature verifies**, with SHA-256, MGF1/SHA-256, and a
  32-byte salt. Changing the digest or signature fails independent verification.
- Radio update completion calls `esp_ota_end` at `0x420a2358`, which calls the
  image verifier through `0x420048da`. That path reaches signature verification
  at `0x42004782` and rejects failure. A startup-table entry also invokes the
  running-image signature check at `0x420038f2`.

The signature format matches [Espressif's documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/security/secure-boot-v2.html).
The recovered verifier at `0x42003b8a` selects trusted key digests from the
running image or eFuses according to a hardware register. **We have not read
that register on the device.** The signed update does not establish whether
hardware Secure Boot, flash encryption, UART download restrictions, or JTAG
restrictions are enabled. A software verification bypass would not by itself
establish that a modified image could boot.

Consequently, ordinary OTA replacement is expected to reject a patched radio
image. A possible future route would require investigating physical recovery
access and the actual protection state on the noncritical C1000, or a verified
update-path weakness. No such route is established, and no modified image has
been sent to either station. The radio is the most relevant component for local
network support; replacing it still requires preserving its controller protocol.

The controller/BMS/display checksums above do **not** prove those components
accept unsigned replacements: their bootloaders and complete update validation
are not recovered. C2000 firmware remains unavailable. Do not infer C2000
flashability from these C1000 findings.

Private reproducibility: `verify_radio_signature.py` and
`firmware-signature-results.json` in `.solix-private/firmware-analysis/` retain
the local verification procedure and three results. Images and signature/key
material remain private.

## Why storing a Time-of-Use plan is insufficient

The C1000 tariff selector at `0x0801bcc4` returns no active tariff unless:

1. The stored usage mode is `1`.
2. The readiness function at `0x0800d1d8` succeeds. It checks a binding flag
   and bit 1 of the radio status byte.
3. A separate power-state flag, bit 0 at `0x200004be`, is set. Its exact
   hardware meaning has not yet been fully traced.
4. The controller's current RTC hour falls within a stored slot:
   `start_hour <= hour < end_hour`.

The binding handler at `0x0800b4d8` sets the flag and logs binding completion.
The radio-status handler at `0x0800cd34` updates the second readiness flag.
On the radio, `wifi_connect_state_notify` at `0x420440f2` normally waits for
**both MQTT connected and AP connected** before reporting Wi-Fi ready to the
controller. A separate factory-mode branch exists; it has not been exercised.

AP association and a stored plan do not establish this readiness state.
This initially made binding/network readiness a candidate explanation for
inactive tariffs. The later status-prefix comparison below weakens that
explanation for the C2000. Applying these gates to C2000 remains an inference
from the C1000 code. Changing the controller's binding flags directly has not
been tested and is not exposed as a control.

A later C2000 native MQTT trial returned AP connected and server connected
through `0027`/`0028`, then accepted a reserve change and the then-presumed all-day Peak schedule
over `0090`. Twelve samples over 25.8 seconds still showed tariff `none` and
idle battery; all settings were restored and confirmed over BLE. Thus a working
MQTT connection did not activate the malformed schedule. The later encoding
audit prevents conclusions about activation of a valid plan. The radio query does
not directly expose the controller's binding flag or cached network status.
See the [native TOU trial](local-mqtt-investigation.md#time-of-use-with-mqtt-connected)
and the bind-callback replay there before attributing this to any one gate.

### Readiness is also encoded in the C1000 status prefix

The C1000 `0100` handler at `0x0800b57c` calls the telemetry serializer
`0x080224a0` at `0x0800b5f4`. Before serializing other fields, that function
calls the same readiness gate at `0x080224cc` and writes **A1=`34` when
ready, otherwise `31`**. Twelve offline prefix replays confirmed this for
binding values 0/1/2 and cached network flags 0–3. Execution stopped before
the individual telemetry callbacks; this was not a full-frame emulation.

A comparison of **54 retained C2000 `0900`/`0421` records** found A1=`34`
throughout the earlier native charging-power trial, native TOU trial, and
later whole-body binding-reply trial. If the C2000 uses the same prefix
meaning, its binding/network gate was already satisfied during the failed
Peak test. This weakens the hypothesis that binding alone prevented TOU;
it does not establish identical C2000 internals or resolve the power/clock/
schedule conditions. Do not generalize the meaning of A1 to every command:
other C1000 report builders use different ready values.

### The separate power gate is debounced

Further static tracing links the C1000 power-state bit to a normal status
debouncer, rather than a remotely supplied schedule field:

- Initialization at `0x0800ecf8` registers the input getters `0x08025e50`
  and `0x08025edc`, and callback `0x08025e80`, in the first record at
  `0x200041a4`.
- The poller at `0x080254e4` counts consecutive getter results and emits
  event mask `2` for the active transition and `1` for the inactive
  transition. The callback respectively sets and clears bit 0 at
  `0x200004be`, which the tariff selector reads.
- The active getter requires bit 0 of the AC-module record at `0x20003f00`,
  the low 14 bits of its halfword at offset `2` to be zero, byte `4` to be
  zero, and bit 0 of byte `8` to be clear. The inactive getter checks only
  whether the first status bit is clear.

This identifies the software path and extra conditions. The physical meaning
of every AC-module bit, its relation to the published mains-presence field,
and the corresponding C2000 implementation are still unverified. No internal
flag or AC-module state was written during this analysis.

### Mains presence does not expose that power gate

The C1000 `A7` telemetry builder at `0x08017ee0` obtains byte 4 from a
different source: the inverse of bit 2 in peripheral register `0x40011808`,
read through `0x0801b164`. It does not read the debounced tariff power flag.
This is the field independently identified as mains presence by the earlier
C1000 unplug/replug capture.

The same builder's output-enabled byte (`A7[1]`) comes from stored flags:
`0x0801a518(0)` dispatches to `0x08019b00`, which tests mask `0x30` in
`0x20000164`. It also does not read the tariff power flag.

Thirty-two offline replays executed the real builder's update path, output
getter, and peripheral bit reader. Independently varying the synthetic
register, tariff flag, AC-module bit, and output-setting flags confirmed these
separate sources. Only memory copy was substituted; no real peripheral was
emulated. Thus **mains present and output enabled do not by themselves confirm
the tariff power gate**, even in the recovered C1000 implementation. C2000
implementation equivalence remains unverified.

### Schedule layout: corrected interpretation

The C1000 command table registers `0090` at `0x0800c7c4`. Its handler reads:

| Field | C1000 firmware behavior |
| --- | --- |
| `A2` | Usage mode from the byte after the value's type marker |
| `A5` | Backup reserve percentage |
| `A6` | **Slot count** |
| `A7` | Binary slot triplets: tariff, start hour, end hour; no embedded count |

Its stored structure has room for six triplets. The `D9` telemetry builder at
`0x080190d4` writes active tariff at byte 1, mode at 2, reserve at 3, upper/lower
charge caps at 4/5, **slot count at 6**, and triplets beginning at 7.
The retained C2000 data supports the **same layout**, correcting our earlier
extra-parameter interpretation. All 244 audited native status records satisfy
`length = 26 + 3 * D9[6]`, and 17 retained request/readback pairs reproduce the
recovered C1000 schedule serialization. Earlier requests duplicated a count in
`A7`, producing malformed slots. See the [complete audit](c2000-tou-encoding-audit.md).
Corrected C2000 storage/activation and C1000 schedule writes still need hardware
validation; no general schedule API is enabled.

## Additional control candidates

These are registered handlers in the recovered main firmware. Presence in code
does not establish that every setting has a useful hardware effect or that the
same field is supported on C2000. Values below are after the usual type marker.

| Command / field | Firmware label or behavior | Remaining work |
| --- | --- | --- |
| `0103/A3` | LCD brightness, one byte | Valid range and visible effect |
| `0103/A5` | Temperature unit, one byte | Confirm enum values in app/telemetry |
| `0103/A7` | Ambient light, one byte | Determine supported hardware behavior |
| `0103/A9` | Language, one byte | Determine enum and applicable displays |
| `0103/B0` | AC off-grid alert switch, one byte | Confirm notification behavior |
| `0101/A5` | AC frequency, one byte | **Do not test on the server-backed C2000** |
| `005e` | Manual/automatic disaster-preparation plans | Decode complete plan and activation rules |
| `0091` | Separate timer/clock plan with multiple flags and time fields | Decode semantics before writing |

The existing display, timeout, output-memory, charging-power, charge-cap, and
fast-charge fields also have matching handlers. `0103` is at `0x0800c530`,
`0101` at `0x0800bd1c`, disaster plans at `0x0800c0a8`, and timer plans at
`0x0800c314`. These are internal opcode numbers; ordinary encrypted app packets
use the previously documented `41xx`/`40xx` form and app command namespace.

Additional code tracks solar-to-battery, grid-to-battery, and battery-to-load
energy, plus time spent in tariff periods. These are promising monitoring
fields; their units, reset rules, and transport mapping remain unresolved.

## Modbus: radio bridge found, controller support missing from dispatch table

The radio handler `charge_set_modbus_tcp_config` at `0x42041128` accepts raw
`A1` length 1 for enable/disable and optional raw `A2` length 2 for a little-endian
port, default 502. Its registered opcode is `0066`.

The dispatcher at `0x4203c9e0` is registered under **function ID `0x10`** at
`0x420487ee`. The shared parser selects this ID from frame byte 6 and masks
opcode flags with `0x0fff`. Thus the static BLE route uses a different namespace
from ordinary app commands (`0x0f`). This route has not been exercised live.
Do not send an ordinary app `4066`: its controller table entry is a different
handler at `0x0800bacc`.

The TCP callback forwards received bytes to the main controller as function ID
`0x10`, opcode `0067`; the radio expects responses on `0867`. However, the
recovered C1000 controller dispatcher at `0x08022614` searches a fixed table of
19 request handlers, and **`0067` is absent**. Its preceding call only logs the
packet; it does not implement an alternate Modbus route. This is evidence that
the radio includes a bridge which this main firmware cannot complete through
that dispatcher. A listening socket would not establish functional Modbus.
C2000 controller support is unknown.

## Next useful checks

- The [native local MQTT investigation](local-mqtt-investigation.md) now includes
  executable parser/startup replays, credential storage bounds, a reproduced
  plain-HTTP short-read failure, embedded TLS credential parser/getter checks,
  and radio diagnostic/configuration queries. The actual TLV byte parser at
  `0x4204f9a6` explains the empty service ID: it scans ascending tags and skips
  `A6/A7/A8` after an earlier `C3`. Correcting `4025` order made C2000 store the
  service ID and connect to local TLS MQTT. Saved credentials also survived AP
  restart; MQTT status and telemetry-stream requests worked with AC output on.
  Native charging-power and mode/reserve/schedule writes now also work, but
  Peak remains inactive despite radio server-ready status. The
  [binding follow-up](c2000-binding-followup.md) tested complete small API
  replies and examined controller readiness; binding alone is now a weaker
  explanation. Investigate the separate power gate, controller clock, and
  C2000 schedule semantics. C1000 still needs a hardware comparison; do not
  substitute the different `4038` layout.
- When C1000 is reachable again, validate its own schedule layout and benign
  display/alert settings with baseline, telemetry, and restoration checks.
- Map the energy counters for read-only monitoring.
- Obtain C2000 firmware evidence before assuming its controller can service
  the Modbus bridge. Keep its AC output enabled throughout any live work.
