# Gen 2 firmware findings

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
| dspACDC | `5.0.6.0` | 80,896 | Decoded header; checksum method unresolved |
| dspDCDC | `5.0.6.0` | 87,040 | Decoded header; checksum method unresolved |
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

This supports a concrete explanation for the previous inactive-tariff trials:
AP association and a stored plan do not establish this readiness state.
Applying the explanation to C2000 is an inference from the C1000 code, not
verification of the C2000 implementation. Changing the controller's binding
flags directly has not been tested and is not exposed as a control.

### C1000 and C2000 schedule layouts differ

The C1000 command table registers `0090` at `0x0800c7c4`. Its handler reads:

| Field | C1000 firmware behavior |
| --- | --- |
| `A2` | Usage mode from the byte after the value's type marker |
| `A5` | Backup reserve percentage |
| `A6` | **Slot count**, not the C2000's separate schedule parameter |
| `A7` | Binary slot triplets: tariff, start hour, end hour; no embedded count |

Its stored structure has room for six triplets. The `D9` telemetry builder at
`0x080190d4` writes active tariff at byte 1, mode at 2, reserve at 3, upper/lower
charge caps at 4/5, **slot count at 6**, and triplets beginning at 7.
The tested C2000 instead reports a parameter at byte 6, count at 7, and
triplets from 8. Do not reuse its schedule encoder or those offsets for C1000.
C1000 schedule writes and tariff-label meanings beyond the observed code still
need hardware validation; no general schedule API is enabled.

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
  and radio diagnostic/configuration queries. C2000 reports an empty app/service
  ID during setup despite an `A6` service field, and zero MQTT errors without
  connecting. Trace its activation/storage path next, with a C1000 hardware
  comparison when reachable. C1000 emulation confirms `A6` is correct for its
  `4025` parser; do not substitute the different `4038` layout.
- When C1000 is reachable again, validate its own schedule layout and benign
  display/alert settings with baseline, telemetry, and restoration checks.
- Map the energy counters for read-only monitoring.
- Obtain C2000 firmware evidence before assuming its controller can service
  the Modbus bridge. Keep its AC output enabled throughout any live work.
