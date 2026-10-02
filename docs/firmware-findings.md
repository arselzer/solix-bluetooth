# Gen 2 firmware findings

The [2026-09-30 C1000 hardware follow-up](c1000-local-mqtt.md) validates
corrected BLE schedule storage and native MQTT charging/reserve/tariff controls
on main 1.1.4.9/radio 0.3.3.0, including generated local-ID authentication.
The [energy lifecycle follow-up](energy-report-lifecycle.md) adds reporting
switch, retry and persistence replays plus actual C2000 report reception.

The October 1 [UART worker audit](gen2-uart-request-worker.md) adds 45 cases:
some rejected or unrelated replies discard requests without callbacks, and
matching-register success need not refresh the complete DSP cache. It qualifies
the earlier [six-failure cache-clear interpretation](gen2-pv-bridge-and-restart.md).
The [109-case diagnostic-getter audit](gen2-diagnostic-getter-audit.md) finds no
complete disaster-plan backup among 23 reviewed getters and confirms a shared
upgrade-reset-timer cancellation side effect. Its initially empty controller
command table also leaves BLE/MQTT getter reachability unproved. Normal
[frequency/Smart readbacks](gen2-preference-readback.md) add three C1000 Gen 2
read-only metrics and 24 serializer cases; C2000's unproven frequency byte is
now labeled raw. No diagnostic or frequency control is exposed.

The next offline continuation adds [22 table-ownership cases](gen2-diagnostic-table-ownership.md):
reviewed software initialization installs the app table and function callbacks
while preserving the diagnostic descriptor. The
[23-case factory USB transport replay](gen2-usb-factory-transport.md) supplies a
complete software request/reply path, with concatenation and overflow limits;
physical service-port access remains unverified. The
[288-case ordinary-field audit](gen2-normal-feature-audit.md) shows why A3's
Wi-Fi quality byte can report 100 after an RSSI failure. A separate radio RSSI
handler preserves failure status. The later
[71-case RSSI routing audit](radio-rssi-routing.md) executes its function-`10`
BLE and native MQTT admission/reply paths with synthetic identities and sessions;
physical observations and cross-model equivalence remain untested.
[106 radio-status cases](radio-status-features.md) identify a cached MQTT flag
and radio clock/offset quirks, while documenting the higher-opcode MQTT routing
limit. The [47-case ingress audit](radio-ingress-failure-audit.md) distinguishes
decryption error replies from outer-frame rejection and verifies conditional
session-material byte dumps. No new runtime controls or live probes are added.

The subsequent [42-case BLE clock/status route audit](radio-status-routing.md)
verifies their separate function-`10` paths and the higher-opcode MQTT bypass.
[144 module-update cases](radio-update-status.md) identify a cached Wi-Fi-module
status whose “ready” value does not prove installation success. A guarded
[C1000 Gen 2 readback](c1000-radio-readback-validation.md) validates six RSSI
unavailable replies plus clock/status responses, preserving fresh settings
baselines. The Python API and CLI now expose RSSI explicitly; 2,040 Python/HA
contract tests pass. No automatic sensor polling or power control is added.

Further offline work adds [96 original charging-source cases](c1000-charge-source-provenance.md):
BC follows internal gate priority, while AF aggregates ten input-cache members;
neither establishes main 1.7.1's physical supply source. [635 Gen 2 output-policy
cases](gen2-output-policy-followup.md) trace two-second countdowns and Smart
counter history, including the real report paths. [29 local-identity cases](account-free-local-setup.md)
carry a generated ID to the radio configuration setter and verify exact native
identity comparisons. These add no live control or model-equivalence claim.

The October 2 continuations add [85 original charging-gate cases](c1000-charge-gate-rules.md),
tracing input-event priority and the second charging channel without finding an
external bypass selector; [1,045 Gen 2 stop-worker cases](gen2-output-stop-worker.md),
proving a timer-zero write cannot revoke an already queued output stop; and
[38 radio identity-storage cases](radio-identity-storage.md), showing configured
and cached accounts can temporarily accept both old and new native identities.
Each continuation retains explicit version and physical-validation limits.
Separately, the [three-station HA runtime trial](ha-runtime-validation.md)
confirms actual discovery, a restored control and shared-service recovery.

The next [108 original saved-settings cases](c1000-saved-charge-validation.md)
show why main **1.5.9** accepting a 100 W write does not imply reload retention:
its valid-file loader rejects that value and restores broader defaults. Installed
**1.7.1** remains a separate, unverified restart boundary. The
[28 complete-file cases](gen2-syspara-backup-format.md) identify Gen 2's exact
415-byte `sysPara` artifact, preserving every saved backup record and switch;
invalid-file recovery erases that block. An external complete export is still
missing, so no backup setter is unlocked.
The [32 BLE/native identity cases](ble-native-identity-separation.md) identify
separate native-account storage and a 16-entry BLE allowlist on the A1763 radio.
Retained BLE IDs survive the executed account-change prefixes; explicit
registration can erase or evict them. Later activation effects and rollback on
the original/C2000 hardware remain unverified.

[121 clock-screen preservation cases](gen2-clock-screen-preservation.md) establish
a reachable hidden-enable mismatch after A2 toggle/restore, despite identical
DA readback. Any `0091` can also overwrite pending asset staging. Scalar brightness
and individual endpoint writes preserve the complete synthetic 280-byte block
when the clock is disabled and transfer state is zero. A later
[native clock-brightness trial](c1000-gen2-clock-ac-smart-validation.md) confirmed
both saved window flags and restoration, without enabling the clock or proving
visible brightness. The separate [Gen 2 native DC Smart trial](c1000-gen2-native-dc-smart-validation.md)
validated the guarded HA/SDK route with complete restoration and AC enabled.

[92 native output-readiness cases](gen2-native-output-readiness.md) extend the
AC/DC off-task branches: positive timers are cleared while the output is off,
and a DC initial-duration cache survives zero cancellation. They also prove
synthetic 415-byte saved-setting preservation for native AC Smart. The subsequent
C1000 Gen 2 AC-off Smart trial confirmed storage/restoration; C2000 remains excluded.

[131 original diagnostic-path cases](c1000-diagnostic-charging-audit.md) traverse
both diagnostic tables and selected radio-staging handlers. Neither charging
gate is directly registered, and the executed prefixes change no protected
power RAM. They establish no new discharge control or safe diagnostic request.
The [below-full charging test plan](c1000-native-charging-test-plan.md) remains
deferred until an expendable load is attached; installed main 1.7.1 differs
from the analyzed 1.5.9 image. These suites are separate from the 1,842-case runner.

The latest [reconnect, clock and power-gate follow-up](c2000-mqtt-reconnect-and-tariff.md)
includes additional offline executions and guarded C2000 hardware observations.
The [parallel power-control analysis](mqtt-power-offline-followup.md) adds
73 actual-code cases for clock synchronization, complete tariff selection and
native charge-limit handlers, including reserve side effects and diagnostic limits.
The [schedule encoding audit](c2000-tou-encoding-audit.md) corrects the earlier
C2000 slot layout and invalidates claims that those trials installed valid
all-day Peak plans. The [DSP investigation](inverter-dsp-investigation.md)
verifies all 159 inverter image blocks and traces the AC-input readiness producer.
The subsequent [corrected C2000 Peak trial](c2000-corrected-peak-trial.md)
verifies local MQTT discharge with mains present. It also records a delay
between restoring Standard and confirming grid input, with twelve additional
C1000 firmware cases identifying a possible internal-mode retention mechanism.

The September 30 continuations add these independently reproducible results:

| Investigation | Offline cases | Main finding |
| --- | ---: | --- |
| [Gen 2 preferences](gen2-preference-candidates.md) | 1,060 | Brightness levels, ambient setter stub, raw language storage and Smart-mode side effects |
| [Clock-screen schedule](gen2-timer-plan-investigation.md) | 60 | `0091` controls the LCD clock screen and display windows, **not charging** |
| [Disaster plans / Storm Guard](gen2-disaster-plan-investigation.md) | 762 | Active plans override charging policy and effective BMS bounds; cancellation can alter other windows |
| [Original C1000 bootstrap state](c1000-bootstrap-state-investigation.md) | 1,165 | MCU acknowledgements, radio-state retries and window timers; no validated remote recovery command |

These counts are separate from the combined 1,842-case runner. Original-C1000
replays use public main **1.5.9**; the live control tests used **1.5.1**.
The [app OTA capture audit](c1000-app-ota-capture-investigation.md) identifies
candidate download/logging and encrypted-chunk paths. During the subsequent
[isolated official update](c1000-original-update-network.md), the app reported
installed **1.7.1** after Retry. Prime Bluetooth independently confirms that
version and six restored BLE controls. The later [original native MQTT trial](c1000-original-mqtt-followup.md)
confirmed main 1.7.1/radio 0.3.3.0 and the same six controls locally.
A subsequent [DC Smart transport trial](c1000-native-dc-smart-validation.md)
brings both updated-version whitelists to seven controls.
A later [Prime Fast trial](c1000-prime-fast-validation.md) brings BLE to eight;
the [independent native Fast trial](c1000-native-fast-validation.md) then brings
native to eight. Actual Fast rate and reboot persistence remain unverified.
The subsequent [Prime AC/Smart trial](c1000-prime-ac-smart-validation.md)
brings Prime to ten SDK controls/nine gateway preferences; native remains eight.
The subsequent [native AC Smart trial](c1000-native-ac-smart-validation.md)
brings native to nine preferences with AC off and an inactive countdown.
A plaintext 1.7.1 main image remains unrecovered.

## Scope and evidence

These findings come from offline analysis of the owner's retained **C1000 Gen 2
(A1763) update to 1.1.4.9**, captured on 2026-09-28 and analyzed on 2026-09-29.
The radio application identifies itself as **v0.3.3.0**. We have not recovered
the C2000 firmware, so identical behavior on that model remains unproven.
Firmware analysis used retained local files; separate live network probes are
documented below and in the linked investigation. No firmware was flashed.

With owner authorization, the recovered vendor application images and hashes
are now public under [firmware/](../firmware/README.md). The
[offline analysis tools](../tools/firmware_analysis/) reproduce 1,842 synthetic
instruction cases, radio signature verification and DSP record extraction.
See [reproduction and substitutions](firmware-analysis-reproduction.md).
Phone captures, session keys, account/device identifiers and raw device logs
remain owner-only in ignored `.solix-private/`. This document contains protocol
findings and code addresses. See [live observations](gen2-protocol.md) and
[new feature candidates](gen2-feature-candidates.md).

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

Public reproducibility: [verify_radio_signature.py](../tools/firmware_analysis/verify_radio_signature.py)
checks the bundled radio image and three signature cases. Results without key
contents are included beside the image. This includes the vendor's public
verification data, not device credentials or private signing keys.

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
Corrected C2000 storage/activation is now verified in the
[bounded native Peak trial](c2000-corrected-peak-trial.md). C1000 corrected schedule storage and native activation are now
[hardware validated](c1000-local-mqtt.md). C2000 has a guarded native schedule API, tested
separately; see [grid-return validation](c2000-offpeak-grid-return.md).

## Additional control candidates

These are registered handlers in the recovered main firmware. Presence in code
does not establish that every setting has a useful hardware effect or that the
same field is supported on C2000. Values below are after the usual type marker.

| Command / field | Firmware label or behavior | Remaining work |
| --- | --- | --- |
| `0103/A3` | LCD brightness: 1/2/3; A4[18] | Actual duty lookup confirmed offline; zero turns the display off without replacing saved brightness |
| `0103/A5` | Temperature unit: 0 Celsius / 1 Fahrenheit; A4[20] | Native C1000 setting round trip validated; see [general settings](c1000-general-settings.md) |
| `0103/A7` | Ambient-light setter is a return-only stub | ACK does not establish a working feature; no justified control |
| `0103/A9` | Language stored raw; A4[26], copied to LCD state | Names, valid enum and visible effect remain unresolved |
| `0103/B0` | AC off-grid alert switch; A4[32] bit 1 | Setting round trip validated; real outage notification/delivery remains untested |
| `0101/A5` | AC frequency, one byte | **Do not test on the server-backed C2000** |
| `005e` | Manual/automatic disaster-preparation plans | Decoded and replayed; effective 100%/1% BMS bounds, fast ceiling and cancellation side effects preclude a simple guarded switch |
| `0091` | LCD clock screen/theme, weekday mask and two display windows | DA readback decoded; asset changes can start a transfer; no charging function established |

The existing display, timeout, output-memory, charging-power, charge-cap, and
fast-charge fields also have matching handlers. `0103` is at `0x0800c530`,
`0101` at `0x0800bd1c`, disaster plans at `0x0800c0a8`, and timer plans at
`0x0800c314`. These are internal opcode numbers; ordinary encrypted app packets
use the previously documented `41xx`/`40xx` form and app command namespace.

The [preference audit](gen2-preference-candidates.md) distinguishes raw byte
acceptance from supported values. Smart AC/DC settings can later shut outputs
off under low load. Port-memory OFF clears recovery bookkeeping, so restoring
ON does not recreate that transient state.

The [disaster-plan replay](gen2-disaster-plan-investigation.md) executes both
manual and automatic activation, D9 readback and charging/BMS consumers. An
active plan bypasses Peak/Mid-Peak charging suppression, uses the internal fast
ceiling and requests effective upper/lower limits **100%/1%** while leaving
saved power/caps/reserve unchanged. BMS zero-current allowance still prevents
requested current. Manual disable invalidates automatic windows covering the
current UTC time, including disabled records; D9 cannot provide their complete backup. There is no
public actuator or live validation for this override.

The [LCD schedule audit](gen2-timer-plan-investigation.md) establishes that
`0091` time fields are minutes since local midnight. Weekday mask zero has
one-shot behavior; asset metadata can initiate an asynchronous resource update.
Existing DA telemetry is preferable to `0092` as a passive source because the
query clears a failure flag after replying. Its 60 cases are distinct from the
47 [tariff clock/offset cases](gen2-schedule-clock-audit.md).

Additional code tracks tariff-only solar-to-battery, grid-to-battery and
battery-to-load sums, separately from a general AC/DC report. The [101-case
follow-up](tariff-energy-followup.md) recovered a binary protobuf report and
its local logging-API route. It corrects earlier descriptions that attributed
the named tariff-only counters directly to that report. Runtime units, reset
rules and C2000 equivalence remain unverified; passive decoding is available.

## Original C1000 bootstrap and official update

The [bootstrap-state investigation](c1000-bootstrap-state-investigation.md)
decodes the app's `4825=26` category as server-connection failure. The recovered
main 1.5.9 function-`10` `0825` handler merely sets an acknowledgement flag;
it cannot explain that radio error. Internal module commands and button/window
timers are not established app-facing remote recovery controls.

In the later September 30 cached-profile retry on installed **main 1.5.1 /
radio 0.1.3.0**, the passive AP initially saw no association. Repeating only the
known same-profile `4024` join returned ACK `00`; association, DHCP and NTP then
occurred, but **no configured API or MQTT connection was observed**. No `4025`
activation or power control was sent in that retry. Three independent final
BLE samples matched all **11 protected settings**, including AC output on.
This advances the observed network boundary without proving local MQTT works
or identifying why the radio never reached the API. Raw logs and identities
remain private.

The user later confirmed **1.7.1** in the app after an official update using a
temporary internet AP that blocked the home LAN. The
[OTA capture audit](c1000-app-ota-capture-investigation.md) finds URL/path logging
before download, internal `App.bin` storage and deletion on screen closure.
Logging is runtime-dependent. The inherited original-model `002f` chunk sender
uses normal encrypted framing, so HCI alone does not guarantee plaintext
firmware recovery. Its two synthetic app-framing cases and raw app analysis
remain separate from the public controller replay counts.
The [update-network record](c1000-original-update-network.md) documents the
failed attempt, faster retry, encrypted download limits and local-updater
prerequisites. Neither the update nor cloud TLS proves direct local MQTT works.
The tested unit then accepted Prime/GCM hello and original `4040` status;
legacy hello had disconnected without a reply. Its expanded F8 matches the
older public MCU serializer, allowing all 11 baseline settings to be checked.
Brightness, charging limit and Device Timeout each passed a round trip with
AC on. The [app security selector](c1000-original-update-network.md#app-security-selection)
uses capability and cached product state; no automatic firmware-version
threshold or on-air capability-byte mapping is established.

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
  Native charging-power and mode/reserve/schedule writes now also work. The
  [binding follow-up](c2000-binding-followup.md) tested complete small API
  replies and examined controller readiness; binding alone is now a weaker
  explanation. Correcting A6/A7 now activates Peak and discharge on C2000.
  A [packaged CLI follow-up](c2000-offpeak-grid-return.md) confirmed tariff-3
  return to grid before clearing Standard. Investigate persistent scheduling
  and reserve floors. C1000 Gen 2 now has its own
  [native MQTT validation](c1000-local-mqtt.md); the updated original C1000
  now has [native MQTT and eight controls](c1000-original-mqtt-followup.md). Do not substitute the different `4038` layout.
- Follow the [preference](gen2-preference-candidates.md),
  [LCD schedule](gen2-timer-plan-investigation.md) and
  [disaster-plan](gen2-disaster-plan-investigation.md) prerequisites before
  exposing further controls; preserve unknown plan and asset fields.
- Continue [energy-unit calibration](gen2-energy-counter-investigation.md)
  using event timestamps and measured power, without relabeling raw counters
  as verified Wh.
- Continue original-C1000 generated-ID setup and remaining Prime controls
  separately from the verified ten BLE/nine native controls. Recover a verified public
  1.7.1 image before proposing local OTA.
- Obtain C2000 firmware evidence before assuming its controller can service
  the Modbus bridge. Keep its AC output enabled throughout any live work.

The later [report lifecycle investigation](energy-report-lifecycle.md) traces
analytics point 20001, report retry/completion and accounting persistence.
Local C2000 report capture now confirms the known protobuf transport.

## October 1: original native controls and charging/backup limits

The original C1000 now joins the isolated AP, receives local credentials and
establishes authenticated TLS/MQTT on main **1.7.1/radio 0.3.3.0**. Six controls
passed changes and restoration. Actual main-1.5.9 instruction replay explains
status routing and suppressed setter ACKs; installed-version readback supplies
the live evidence. See [the native record](c1000-original-mqtt-followup.md).

Two new original replays cover **87 charging-path cases** and **48 actual ACK
cases**. Internal converter disable preserves the output bit in RAM, but no
supported external force-discharge/bypass command was identified. A zero
charging ceiling still takes the active charging-producer path. See
[charging paths and ACK suppression](c1000-bypass-firmware-followup.md).

**16 Gen 2 backup cases** show D9 omits dormant records and saved maximums.
Manual disable invalidates automatic windows covering now, even with their
switch off; future and expired windows survive. Complete record backup is
required before a reversible backup-mode trial. See
[persistence and complete readback](gen2-persistent-plan-followup.md).

## October 1: reconnect persistence and additional query/mode paths

A live C1000 Gen 2 **main 1.1.4.9/radio 0.3.3.0** trial retained a nonempty
all-day Peak plan and 20% reserve across two local-server restarts and a
same-profile AP restart. The read-only restarted services sent status queries
without replaying activation. Battery supply resumed with AC enabled and mains
present. Standard, reserve 10% and all original settings were restored, with
an independent final Bluetooth check. See [the persistence record](gen2-persistent-plan-followup.md).
This does not establish whole-device power-cycle persistence.

The [24-case full-query replay](gen2-backup-query-investigation.md) traces
`0100` through the actual descriptor table and D9/DA/FE callbacks. It preserves
checked saved settings, complete backup records and the clock-screen failure
flag. A strict C1000 Gen 2 `controller_utc_timestamp_seconds` metric now exposes
FE; incremental messages can contain a cached value. Nineteen direct callers
of the complete internal backup getter still do not establish an external
full-record export.

The [73-case original-C1000 follow-up](c1000-timer-and-mode-followup.md) executes
main **1.5.9** timer, complete F8 and Fast paths. Timer expiry toggles the cached
output request in either direction, and RTC time-of-day jumps affect the
countdown. Normal charging allowance uses saved watts × 0.91; Fast changes a
volatile allowance rather than disabling the charging input.

A separate live **main 1.7.1** [DC Smart test](c1000-prime-dc-smart-validation.md)
confirmed `4076` and full F8 restoration while DC stayed off. That trial
established BLE Prime DC Smart; the later native evidence follows below.
AC Smart remains unverified on the updated version.

## October 1: native DC Smart and remaining export candidates

An [independent original C1000 native DC Smart trial](c1000-native-dc-smart-validation.md)
now confirms `0076` on **main 1.7.1/radio 0.3.3.0**, followed by a public SDK
repeat and independent final Bluetooth check. The original native whitelist
reached seven controls at that stage. DC must be off in both directions; all
protected settings and the full F8 are confirmed. Original AC Smart remains
unverified; the subsequent independent BLE and native Fast trials follow below.

The [93-case original Fast/BMS replay](c1000-fast-status-retention.md) executes
8,192 full charging callbacks in main **1.5.9**. Fresh E5 reports the runtime
Fast flag, not the last request; SOC 100% alone does not clear it in the tested
mains-present paths. BF/C0 expose raw main/expansion BMS state bytes. The
decoder now exports those strict byte fields, without inferring UPS states;
43 retained main-1.7.1 reports confirm their shape with both codes zero.

[27 new Gen 2 radio/diagnostic cases](gen2-backup-export-radio-app.md) resolve
further backup-export candidates. The radio's device-parameter builder queries
energy point `20001`; its backup file is MQTT connection storage. App `0490`
BackupMind links to the existing energy report. Its `SETTING_MSG_DESC` callback
serializes tracking sessions rather than the saved backup block, and clears
tracking during construction, including some nested encoding failures. Saved
configuration stays unchanged in the bounded replay. Whole-report delivery
and physical effects were not tested; do not trigger it as a passive backup
export. Complete disaster-record readback remains unresolved.

## October 1: Prime Fast, BMS phases and solar weak-light lock

The [original Prime Fast trial](c1000-prime-fast-validation.md) confirms
`405e` retention and restoration at full SOC on **main 1.7.1 / radio 0.3.3.0**.
A separate C1000-chain input-loss trial cleared Fast while original AC stayed
on, then restored the upstream supply and every protected setting. This
establishes the eighth Prime BLE control, independently of native MQTT;
actual Fast charging-rate enforcement and reboot persistence remain untested.

[59 BMS producer cases](c1000-battery-phase-firmware.md) execute both original
MainBMS/SubBMS images from the public 1.5.9 package. Codes 1/2 are latched
discharge/charge phases, with asymmetric delayed clearing. Zero can also arise
through protection/reset paths, and code 4 is produced but unassigned.
BF/C0 remain raw SDK values; these are not instantaneous mains/UPS indicators.
The installed 1.7.1 BMS component versions were not recovered.

[73 new weak-light cases](gen2-weak-light-observability.md) trace the C1000
Gen 2 MPPT lock producer, full/incremental serialization, timer and retry
branches. The strict A3 diagnostic `pv_weak_light_locked` is now surfaced in
monitoring, the browser/TUI and a disabled HA binary diagnostic. An elapsed
RTC difference greater than 600 sets a retry flag without clearing this lock;
the fixed 600 in A3 is not a countdown. Physical solar behavior remains
unvalidated, and no new recovery command is exposed.

## October 1: native Fast, current ceilings and solar retry origins

An [independent original native Fast trial](c1000-native-fast-validation.md)
verified `005e` on **main 1.7.1 / radio 0.3.3.0**, followed by the public SDK
and an independent Bluetooth audit. Both native and Prime now expose eight
verified controls. The native stages made four setting writes in total,
confirmed fresh full readbacks and restored every protected setting and F8 byte.
AC output stayed on; actual Fast rate and reboot persistence remain unverified.
Separate [normal charging-rate trials](c1000-charging-rate-validation.md)
retained 125 complete snapshots, thirteen restored setting writes and no
original AC-output toggle. Full-SOC reporting prevented a stable comparison;
100 W below the load did not establish forced battery operation with mains.

[50 new original current-limit cases](c1000-fast-current-limits.md) execute
the separate Fast temperature/segment tables, complete charging policy and
DSP register-4 queue construction in **main 1.5.9**. SOC initializes a cached
segment; voltage and temperature history affect later requests. Near-full
Fast can request less current than Normal despite its higher power allowance.
Changing the saved charging ceiling while Fast is active need not lower the
request; disabling Fast uses the then-current saved ceiling. These are synthetic
controller requests, with no DSP enforcement or installed-1.7.1 rate measurement.

[59 new Gen 2 solar-retry cases](gen2-pv-retry-origins.md) execute the existing
`0103` brightness handler through the user-action flag, weak-light timer and
input-debounce consumer in **main 1.1.4.9**. Writing the saved nonzero brightness
can mark a locked MPPT state for retry without changing outputs. The SOC ≤1
branch has additional timer/peripheral effects. Physical recovery and timing
remain unverified; a naturally locked, adequately charged PV trial is required
before exposing any automatic recovery behavior. No recovery API was added.

## October 1: Prime AC/Smart, SOC history and MPPT actuation

The [original Prime AC/Smart prototype and public SDK repeat](c1000-prime-ac-smart-validation.md)
each passed four writes and twenty complete snapshots on **main 1.7.1**:
AC off, Normal, Smart, AC on. Full original settings/F8 and read-only upstream
Gen 2 settings/D9/A4 were restored. Prime now has ten SDK controls and nine
gateway preferences; native remains eight. Fresh inactive AC countdowns guard
both new controls; AC Smart additionally requires AC off in either direction.
No C2000 connection, cloud access or firmware change was involved.

[78 original SOC/recharge cases](c1000-soc-and-recharge-firmware.md) execute
public **main 1.5.9 / MainBMS 0.0.33 / SubBMS 0.0.5** paths. C1 is a cached
primary-BMS estimate, separate from C2. Rounding, a full-related latch, capacity
history and charge-phase rebasing can preserve a reported 100%. This does not
establish why installed 1.7.1 stayed at 100% in the earlier rate trials, an exact
recharge threshold, a total input budget, or a forced-discharge command.

[140 Gen 2 PV actuation cases](gen2-pv-retry-actuation.md) trace qualified
DCDC register `0126` state through real controller policy rows, configuration
and `0015` start/stop requests, then DSP event/fault guards. Configuration
success checks current input again; input loss requests stop, and stop wins
over simultaneous start. Status may retain an earlier state before its qualifier
is set. UART, full converter sequencing, physical PV and actual recovery remain
unverified. The existing brightness write remains a recovery candidate; no
automatic recovery API or internal-register control was introduced.

## October 1: native AC Smart and deeper startup/blocker traces

[Native AC Smart](c1000-native-ac-smart-validation.md) passed an independent
two-write/23-snapshot prototype and two-write/35-snapshot public SDK repeat
on original main **1.7.1**. Both required AC off and exact fresh zero countdowns;
whole original settings/F8 and the read-only upstream Gen 2 baseline were
restored. Native now has nine preferences, matching the Prime gateway. A
failed BLE restoration after Wi-Fi provisioning was resolved through a bounded
native output-ON request; private OFF/ON setup in the repeat also passed.
No public native output-switch route was added. C2000 was not accessed.

[90 original Smart-blocker cases](c1000-smart-blocker-followup.md) trace the
**1.5.9** qualified DSP flag and independent GPIO. Initial qualification and
later removal use different predicates. A blocker only present between
eligible Smart checks can leave an inherited counter intact. Output lifecycle
reset paths differ from preference changes. Internal F0 led to the subsequent
[diagnostic-tunnel replay](c1000-f0-diagnostic-tunnel.md); its physical GPIO
meaning and installed 1.7.1 behavior remain unverified.

[60 Gen 2 DSP-continuation cases](gen2-pv-start-continuation.md) follow the
accepted start request into periodic gates, a 100-invocation counter and
protected peripheral write intents. The counter can accumulate before a
request. A fault exits to mode 4 and clears the request; fault clearance alone
does not establish automatic recovery. The subsequent
[mode-4 replay](gen2-pv-mode4-recovery.md) establishes an internal return path;
actual timing, converter regulation and physical PV recovery remain unverified.
No recovery API was added.

## October 1: diagnostic transport limits and DSP fault recovery

[94 original F0 cases](c1000-f0-diagnostic-tunnel.md) establish the exact
**main 1.5.9** diagnostic envelope, CRC, asynchronous reply and GPIO getter.
Correctly decoded requests preserve settings/outputs but cancel an existing
upgrade-mode reset timer. Malformed embedded lengths above 255 trap its
8-bit copy loop; these are offline cases only.

The [separate installed-1.7.1 attempt](c1000-f0-live-transport-limit.md) used
an encrypted BLE body without the established `4000` encryption marker. It
returned no diagnostic reply, and a reconnect showed the original AC off.
One validated SDK AC-on command restored the complete original baseline;
three paired snapshots confirmed it and unchanged upstream Gen 2 settings.
The precise cause is unproved. No diagnostic runtime API was added, and
C2000 was not accessed.

[73 Gen 2 recovery cases](gen2-pv-mode4-recovery.md) execute the real DSP
dispatcher and mode-4/6 handler: 200 qualifying healthy observations return
to mode 1; 300 consecutive qualifying nonblocking-fault observations clear
the stored fault. A fresh start must arrive after mode entry. These counts
are not measured durations or proof of physical recovery.

The [recovery-event origin audit](gen2-pv-recovery-event-origins.md) executes
all 65,536 internal register-`0015` values and audits 62 direct event-writer
calls. Only words `0057`/`0058`/`0059` generate start/stop/stored-fault-clear
events through that consumer. The normal main-controller helper can emit
only start/stop; no public fault-clear opcode or bank-0 bit-6 producer was
established. No internal-register or fault-bypass control was introduced.

## October 1: radio forwarding and main-controller retry

[96 radio-routing cases](radio-factory-routing.md) execute the **A1763 radio
0.3.3.0** function registry, receiver, encryption classification, dispatcher
and outer framing. Function `0c`/normalized command zero forwards between
BLE port 0 and controller port 2; native MQTT port 5 is rejected. The body
and inner CRC are passed unchanged. Missing the `4000` marker forwards
ciphertext without decryption. This does not establish original-radio
equivalence or a safe diagnostic request.

The [BLE framing guard](ble-encryption-framing.md) now rejects inconsistent
encrypted headers in the Python library and Web Bluetooth app. Eighteen new
Python regression cases and two browser tests verify rejected headers produce
no packet/send and preserve supported negotiation, GCM and CBC framing. The
complete Python/HA suite passes **1,977 tests**. No diagnostic API was added.

[47 main-controller retry cases](gen2-pv-bridge-and-restart.md) trace real
task/timer/callback instructions in **main 1.1.4.9**. Absent cached DCDC
running feedback can reset the configuration latch after eleven sampling
periods and permit configuration plus a fresh start request. Allocation and
queue failures can retry earlier. Six failed bulk-status completions after
a success clear the 146-byte DSP cache and request error 25/display refresh;
this is not proof of mains loss or physical output interruption. Actual
transport cadence, fault clearing and charging recovery remain unverified.
The direct queue-call audit still establishes no public stored-fault-clear
route. This follow-up sent no device commands.

## October 2: bounded countdown and original input qualification

The [98-case C1000 Gen 2 lifecycle replay](gen2-ac-countdown-roundtrip.md) shows
normal early cancellation/rearming preserves all 415 saved bytes, while hidden
checkpoint/counter state is not fully restored. Zero cannot revoke an already
queued output stop. The separate [native MQTT trial](c1000-gen2-native-ac-countdown-validation.md)
confirmed 600→594→0 on main 1.1.4.9, with two timer writes and no output-switch
writes. It restored reported preferences and did not exercise physical expiry.
The private operator CLI supports this guarded control; HTTP/HA exclude it.
Original/C2000 received only status requests.

[1,064 original main-1.5.9 DC-input cases](c1000-second-input-qualification.md)
trace DSP bit qualification, 202-call debounce transitions and qualified AC
priority. Input flags and rule events do not establish an external charging-source
selector or installed-1.7.1 behavior. A real original charging-rate test still
requires battery headroom and the deferred expendable load.

The [static app OTA follow-up](c1000-firmware-metadata-followup.md) identifies
the original shared metadata route and `LastPackage` URL/hash/size fields.
Compatible OTA endpoints belong to MicroInverter paths, while the discovered
precharge action has a device-class gate excluding the retained original C1000
class. No cloud request or supported pause/discharge control follows from this
source audit. These findings can guide future firmware acquisition; they do not
implement a local updater.

The [50-case radio-local MQTT replay](radio-ble-advertising-recovery.md) traces
A1763 radio 0.3.3.0 admission through BLE enable `10/0024`. The initialized path
preserves protected native/allowlist identity regions and forwards no MCU
output command, but ACK can succeed without physical advertising. Query `0003`
reports application flags; `0023/0025` can mutate network/binding and are excluded.
Radio response pattern `030010` differs from current controller handling, so
the controller builder must not be reused. Physical callbacks, initialization
and other-model equivalence remain unproved; no runtime recovery API or station
trial was added. Physical IoT-button fallback remains a live-trial prerequisite.

## October 2: radio query validation and OTA capture boundary

The [47-case first-enable/activation replay](radio-ble-initialization-activation.md)
traces radio initialization defaults, two 1000 ms timers and BLE flag producers.
Advertising enable leaves application BLE state unchanged; direct connection
callbacks write it. Normal provisioning can persist a changed account, but
substituted lifecycle calls prevent a rollback or independent-recovery claim.
An update-check callback previously worth inspecting is not a MQTT-binding
success callback. No physical BLE-enable or identity trial followed.

The [23-case native query audit](radio-native-info-queries.md) resolves `0002`
MAC/region providers and `0003` private network text. `0002` can initialize Wi-Fi
or return stale scratch with status 00; it stays excluded from public getters.
`0003` A4 contains configured network text, not a MAC. Only raw A1/A2 flags
are selected by the new private operator command.

One [live native `0003` query](c1000-gen2-native-wireless-state-validation.md)
on C1000 Gen 2 main **1.1.4.9 / radio 0.3.3.0** returned BLE application 0,
Wi-Fi application 1. Full fresh protected settings matched, all three stations
remained fresh with AC enabled, and the wire audit found zero setting writes.
Exact radio pattern/opcode matching isolates replies from controller ACKs and
freshness. HTTP/HA controls and identity profiles did not change.

The [static app transport audit](c1000-ota-http-wrapper.md) finds conditional
native SDK/Dio paths and where decrypted OTA metadata reaches `OtaUpdateModel`.
Native SDK signing/encryption remains unresolved, and main 1.7.1 is still missing.
App Fast/charging-limit gates establish no original charging pause or forced
discharge control. Equal `0003` values in the original app refer to a different
capability-negotiation namespace, so original/C2000 wireless queries remain
unverified. Raw captures and app extracts stay private.

## October 2: network-state producers and SDK action limits

The [31-case radio producer replay](radio-network-state-producers.md) separates
got-IP state from MQTT connection state and traces the cached IPv4 formatter.
Station STOP and the disconnect helper can leave old IP text; the disconnect
event clears it. Application Wi-Fi state requires both IP and MQTT conditions
on the examined normal callback. These fields do not establish cloud binding,
BLE availability or asynchronous recovery.

One [native RSSI trial](c1000-gen2-native-rssi-validation.md) returned **−42
dBm** with unchanged fresh protected settings and zero setting writes across
the fleet. The private CLI exposes `ap-service-wifi-rssi`; exact failure
status becomes null, and radio responses cannot refresh controller telemetry.

The [direct AC-input-disable app audit](gen2-ac-input-disable-app-audit.md)
finds a real SDK action and capability gate, but no verified native setter or
readback mapping. `acInputStatus` maps to AC output status in the app's
transaction rules; substituting that parameter would target the wrong function.
Optimistic UI state and SDK completion are insufficient confirmation.

[39 original negotiation cases](c1000-power-method-and-negotiation-audit.md)
show main 1.5.9 function `01/0003` marks session state and can continue after
some errors. It is not a read-only charging-feature query. The inherited
port-memory `0079` builder remains a newer-model/version lead, absent from all
recovered old tables; no original pause/battery-only control was added.

The [static Android SDK inventory](android-sdk-native-boundaries.md) locates
clear loader bytecode and exported native primitives. It records digest-table
selectors but recovers no OTA envelope or action-to-frame dispatcher. App APKs,
crypto material and operational evidence stay private; only sanitized metadata
and independently reproducible synthetic/static results are public.
