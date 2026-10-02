# Reproducing the C1000 firmware analysis

## Included work

The [offline replay tools](../tools/firmware_analysis/) combined runner reproduces **1,842 synthetic cases** against C1000 Gen 2 (A1763) main firmware **1.1.4.9**. They use Unicorn to execute selected original ARM Thumb instructions. No station connection, Anker account, phone capture, credentials, radio image or SDK installation is needed for the instruction replays.

Separate continuations add [1,317 original-C1000 v1.5.9 cases](c1000-legacy-network-investigation.md)
and [1,347 original Smart-policy cases](c1000-smart-auto-off-policy.md),
[1,165 original bootstrap-state cases](c1000-bootstrap-state-investigation.md),
[49 Gen 2 energy scenarios plus 1,800 arithmetic checks](gen2-energy-counter-investigation.md),
[47 tariff clock/offset cases](gen2-schedule-clock-audit.md),
[1,060 preference cases](gen2-preference-candidates.md),
[60 LCD clock-screen cases](gen2-timer-plan-investigation.md), and
[762 disaster-plan cases](gen2-disaster-plan-investigation.md),
[87 original charging-path and 48 ACK cases](c1000-bypass-firmware-followup.md),
[16 complete-backup-readback cases](gen2-persistent-plan-followup.md),
[24 non-clearing full-status cases](gen2-backup-query-investigation.md), and
[73 original timer/Smart/Fast cases](c1000-timer-and-mode-followup.md), and
[93 original Fast-retention/BMS cases](c1000-fast-status-retention.md), and
[27 radio/diagnostic export-candidate cases](gen2-backup-export-radio-app.md),
[59 original BMS-phase cases](c1000-battery-phase-firmware.md),
[73 Gen 2 weak-light cases](gen2-weak-light-observability.md),
[50 original Fast current-limit/DSP cases](c1000-fast-current-limits.md), and
[59 Gen 2 solar-retry-origin cases](gen2-pv-retry-origins.md),
[78 original SOC/recharge cases](c1000-soc-and-recharge-firmware.md), and
[140 Gen 2 PV-retry actuation cases](gen2-pv-retry-actuation.md),
[90 original Smart-blocker cases](c1000-smart-blocker-followup.md), and
[60 Gen 2 DSP-startup continuation cases](gen2-pv-start-continuation.md),
[94 original diagnostic-tunnel cases](c1000-f0-diagnostic-tunnel.md),
[73 Gen 2 fault-recovery cases](gen2-pv-mode4-recovery.md), and an
[exhaustive 65,536-value internal recovery-command check](gen2-pv-recovery-event-origins.md),
[96 Gen 2 radio-routing cases](radio-factory-routing.md), and
[47 main-controller PV retry/status-failure cases](gen2-pv-bridge-and-restart.md),
[45 UART worker/completion cases](gen2-uart-request-worker.md),
[109 diagnostic-getter/exposure cases](gen2-diagnostic-getter-audit.md), and
[24 normal preference-readback cases](gen2-preference-readback.md),
[22 diagnostic-table ownership cases](gen2-diagnostic-table-ownership.md),
[23 factory USB transport cases](gen2-usb-factory-transport.md),
[288 ordinary Wi-Fi/transfer-capacity cases](gen2-normal-feature-audit.md),
[71 radio RSSI admission/reply cases](radio-rssi-routing.md),
[106 radio MQTT-state/clock cases](radio-status-features.md),
[47 radio ingress/error-reply cases](radio-ingress-failure-audit.md),
[42 BLE radio clock/status routing cases](radio-status-routing.md),
[144 radio module-update status cases](radio-update-status.md),
[96 original charging-source cases](c1000-charge-source-provenance.md),
[635 Gen 2 countdown/Smart cases](gen2-output-policy-followup.md), and
[29 local-identity acceptance cases](account-free-local-setup.md), and
[85 original input-event charging-gate cases](c1000-charge-gate-rules.md),
[1,045 Gen 2 output-stop-worker cases](gen2-output-stop-worker.md), and
[38 radio identity-storage cases](radio-identity-storage.md).
These have independent commands and manifests; they are not included in the
1,842-case combined runner. Their individual commands are in the linked records.
The diagnostic cases include malformed-envelope stalls; those synthetic inputs
must not be transmitted. The separate [installed-version transport attempt](c1000-f0-live-transport-limit.md)
did not validate a diagnostic sensor and required restoring the test station's AC output.
The [BLE framing regression checks](ble-encryption-framing.md) exercise the
SDK and browser guards separately from those firmware replays.

| Suite | Cases | Evidence |
| --- | ---: | --- |
| General settings | 1,063 | Temperature and off-grid-alert bytes, timer interaction, LCD brightness/display, port memory, device timeout, malformed alert fields |
| Alert consumer | 20 | Five-sample debounce, exact enable value, inhibit flag, notification descriptor and memory-write bounds |
| Charging follow-up | 245 | Lower limit/reserve side effects, A4/D9 mirrors, downstream bounds, native fast-charge gating, power validation, default recovery |
| Feature candidates | 212 | Fast-charge acceptance/automatic clearing, synthetic full-SOC/BMS-current states, 100/200 W settings-load preservation and internal descriptors |
| Additional telemetry | 147 | Fixed compatibility byte, temperature selection, PV fields/freshness and raw controller error code |
| Device Timeout | 155 | RTC selection, pending alarm replacement, independent idle sleep, activity flags and callback dispatch |

The tools are independently runnable research code. Their arbitrary-byte tests establish handler behavior, **not supported or safe values for live control**. In particular, a zero-watt value can fail settings-load validation and reset settings, including Device Timeout Never.

## Exact firmware input

The harness requires this decoded main-controller image:

| Property | Value |
| --- | --- |
| Filename | `MainMcu-decoded.bin` |
| Size | 198,656 bytes |
| SHA-256 | `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9` |
| ARM load address | `0x08005000` |

The exact image is included under `firmware/c1000_gen2/1.1.4.9/` and used by default. Set `SOLIX_FIRMWARE_DIR` to override that directory. A wrong hash or length fails before emulation; addresses must not be reused with another firmware version. The tools perform no download or extraction from a device.

The hash identifies the exact image used for these observations. It is not a vendor signature. The analysis source follows the repository license; vendor images have separate [provenance and notices](../firmware/README.md). This publication includes no phone-derived identifiers, captured session keys or private logs.

## Run from the repository root

Tested with **Python 3.12.3**, **Unicorn 2.1.4**, Linux x86_64. The replay needs Unicorn, pinned in the requirements file; the additional radio signature checker uses `cryptography` from the same file.

```sh
python3 -m venv /tmp/solix-analysis-venv
/tmp/solix-analysis-venv/bin/python -m pip install -r tools/firmware_analysis/requirements.txt
/tmp/solix-analysis-venv/bin/python tools/firmware_analysis/run_replays.py \
  --output /tmp/solix-replay-results
```

Dependency installation requires package access. The replay itself performs local file I/O and emulation only. The final line should report `total_cases: 1842` and `all_results_match: true`. Python optimization is rejected because it disables replay assertions.

The output directory contains six suite result files and `reproduction-manifest.json`, recording firmware hash, source hashes, exact runtime versions and counts. The runner compares complete result values against the published `tools/firmware_analysis/expected_results/` files. Those files contain generated settings, synthetic packet bodies and synthetic event data; they are not sanitized phone captures.

The published [verified-run manifest](../tools/firmware_analysis/verified-run.json) records a successful run matching every expected result. Input checks also rejected a missing image, a wrong-size image, a same-size image with the wrong hash, and optimized Python execution.

To run one suite, invoke its `emulate_*.py` entry point with `SOLIX_ANALYSIS_OUTPUT` set. Entry points are `emulate_general_settings.py`, `emulate_offgrid_alert.py`, `emulate_charging_followup.py`, `emulate_feature_candidates.py`, `emulate_additional_features.py` and `emulate_device_timeout.py`. Other modules supply the replay machinery; the D9 helper contains no capture reader. Feature replay assumptions and proposed future tests are in [feature candidates](gen2-feature-candidates.md), [additional telemetry](gen2-additional-feature-investigation.md) and [timeout behavior](device-timeout-behavior.md).

## Standalone preference, schedule and bootstrap continuations

These commands are **additional suites**, not changes to the combined runner:

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-preferences \
  python3 tools/firmware_analysis/emulate_preference_candidates.py
cmp /tmp/solix-preferences/preference-candidates-results.json \
  tools/firmware_analysis/expected_results/preference-candidates-results.json

python3 tools/firmware_analysis/emulate_timer_plan.py \
  --firmware-dir firmware/c1000_gen2/1.1.4.9 \
  --output /tmp/solix-clock-screen.json
cmp /tmp/solix-clock-screen.json \
  tools/firmware_analysis/expected_results/timer-plan-results.json

SOLIX_ANALYSIS_OUTPUT=/tmp/solix-disaster \
  python3 tools/firmware_analysis/emulate_disaster_plan.py
cmp /tmp/solix-disaster/disaster-plan-results.json \
  tools/firmware_analysis/expected_results/disaster-plan-results.json

python3 tools/firmware_analysis/emulate_c1000_network_state.py \
  --output /tmp/c1000-network-state.json
```

| Suite | Cases | Exact scope |
| --- | ---: | --- |
| Preferences | 1,060 | Brightness duty lookup, ambient setter stub, raw language storage/LCD copy and Smart storage; no visible display or asynchronous output-shutdown validation |
| LCD clock screen | 60 | `0091` handler, weekday/minute windows, visibility, one-shot expiry, DA/0092 readback and asset-update boundaries; **not a charging scheduler** |
| Disaster plans | 762 | Manual/automatic storage, activation/expiry, D9, charging policy and periodic BMS-limit tail; effective 100%/1% bounds and cancellation side effects |
| Original bootstrap | 1,165 | A1761 **main 1.5.9** ACK handlers, internal serializers, radio-state retries and timer registration; installed main **1.5.1** behavior is separate |

The first three require the same Gen 2 main image as the combined runner.
The original-bootstrap tool validates the distinct bundled
`firmware/c1000_original/1.5.9/MainMcu-decoded.bin` and accepts `--firmware`.
Its SHA-256 is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
The 60 LCD cases must not be confused with the separate 47 tariff-clock cases.

Newer suites execute some paths that older helpers substituted: the disaster
suite runs actual active-plan selection, for example, but substitutes calendar
conversion and captures charge/BMS delivery. The LCD suite captures asset
transfer, timer and display boundaries. Read each investigation's substitution
list before drawing a conclusion from its results. No disaster-plan actuator
or live safety guarantee follows from its replay.

The [original-C1000 OTA capture audit](c1000-app-ota-capture-investigation.md)
uses a retained private Android app and two synthetic framing checks. It is
not a public firmware-suite dependency. It identifies conditional URL logging,
internal file storage and encrypted OTA chunks; it does not install the
offered 1.7.1 update or establish guaranteed non-root plaintext recovery.

## Executed code and substitutions

The real firmware instructions execute for TLV parsing, relevant handlers, setting setters/getters, A4/D9 serialization, tariff selection, selected charging-policy branches, settings validation, and the post-file-read settings-load/default decision. Each harness rejects execution outside its allowlisted code and explicit substitute boundaries.

The environment substitutes logging, response transport, persistence/flash scheduling, LCD event delivery, selected timer setup, final refresh queues, allocation and queued BMS delivery. Memory-copy helpers are substituted where the harness states this. SOC, charge voltage/current, RTC, readiness flags, backup-plan state and RAM/MMIO are synthetic. Alert SOC is fixed at 73. Full A4 mirror replay substitutes zero remaining times. The charging policy records outgoing descriptors without operating a DSP or battery controller. The settings-load branch starts after a synthetic successful file/CRC check; it does not read a device filesystem or prove a real restart sequence.

The helper dependency chain is `ClockMachine → SocMachine → TouMachine → D9Machine → PolicyMachine`; general settings also derive directly from `ClockMachine`, and the alert and charging suites extend these helpers. The public helpers omit the old private capture-ingestion entry points. Firmware input loading is centralized in `replay_io.py`.

Consequently, results establish the reported code path for this exact C1000 image under the stated inputs. They do not establish asynchronous side effects beyond recorded boundaries, actual power flow, end-to-end alert delivery, unsupported setting safety, or C2000 behavior. Live evidence remains separately documented in the device/protocol research notes.

## Complete file-load follow-ups

Later standalone suites execute the file/checksum/default paths excluded by
the original combined runner's post-read substitute:

| Suite | Cases | Version and scope |
| --- | ---: | --- |
| [Original saved charging settings](c1000-saved-charge-validation.md) | 108 | A1761 main 1.5.9; actual header, CRC, settings validation and defaults with synthetic file I/O |
| [Complete Gen 2 backup file](gen2-syspara-backup-format.md) | 28 | A1763 main 1.1.4.9; 415-byte `sysPara` save/load, all backup records/switches and destructive invalid-file defaults |

Use each linked reproduction command and compare its complete expected JSON.
Independent replays matched both result and manifest files on Python **3.12.3**,
Unicorn **2.1.4** and Capstone **5.0.7**. These are separate suites, not additions
to the combined runner's 1,842 count. Neither operates real storage or reboots a
station. The original's installed **1.7.1** behavior and Gen 2 external backup
export remain unverified.

The separate [BLE/native identity replay](ble-native-identity-separation.md)
adds **32 cases** for A1763 radio **0.3.3.0**, including retained-ID authorization,
native-only account changes and explicit allowlist saves. Independent complete
results and manifest matched. Its defined provisioning prefixes stop before
later activation effects; it is not an end-to-end recovery or original/C2000
firmware proof. Use its own command, exact image hash and substitution list.

## Clock-screen preservation

The independent [clock-screen preservation suite](gen2-clock-screen-preservation.md)
adds **121 cases** against A1763 main **1.1.4.9**:

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-clock-screen-preservation \
  python3 tools/firmware_analysis/emulate_gen2_clock_screen_preservation.py
```

Complete results and manifest reproduced byte for byte. The suite covers a
reachable hidden-enable restoration mismatch, pending-stage overwrite and
lossless scalar fields. Its 280/415-byte opaque buffers are synthetic; physical
storage, display and output hardware are excluded. These cases remain separate
from the combined runner's count.

## Native timer readiness and original diagnostic paths

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-native-output-readiness \
  python3 tools/firmware_analysis/emulate_gen2_native_output_readiness.py
python3 tools/firmware_analysis/audit_original_diagnostic_charging_paths.py \
  --output /tmp/original-diagnostic-charging-results.json \
  --manifest /tmp/original-diagnostic-charging-manifest.json
```

The [92-case Gen 2 suite](gen2-native-output-readiness.md) executes native setting
handlers, actual output-flag branches and off-task cleanup. The on-task body and
final DSP/physical output are excluded. The [131-case original suite](c1000-diagnostic-charging-audit.md)
executes diagnostic routing and selected module-request staging, excluding later
radio/event effects and actual main 1.7.1. Both complete result/manifest pairs
independently matched `expected_results/` byte for byte. They add no live
diagnostic or raw-register API and remain outside the combined 1,842-case count.

## AC countdown lifecycle and original DC-input qualification

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-ac-countdown \
  python3 tools/firmware_analysis/emulate_gen2_ac_countdown_roundtrip.py
python3 tools/firmware_analysis/emulate_original_dc_input_qualification.py \
  --output /tmp/original-dc-input-qualification-results.json \
  --manifest /tmp/original-dc-input-qualification-manifest.json
```

The [98-case countdown suite](gen2-ac-countdown-roundtrip.md) uses A1763 main
1.1.4.9. Normal early cancel/rearm preserves saved settings, while hidden volatile
state and pending stops prevent unconditional cancellation guarantees. The
[1,064-case original DC-input suite](c1000-second-input-qualification.md) uses
main 1.5.9 and selected DCDC code, tracing qualification/debounce and qualified
AC priority. It establishes no external source selector or installed-1.7.1
charging-rate behavior. Independent complete result/manifest pairs matched
`expected_results/` byte for byte; these counts remain outside the combined runner.
Use each linked note for exact filenames, image hashes and substitutes.

## Radio-local BLE-enable continuation

The [50-case radio suite](radio-ble-advertising-recovery.md) traces the normal
MQTT admission/dispatch/reply path and initialized A1763 BLE helper:

```sh
python3 tools/firmware_analysis/emulate_radio_ble_advertising.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/radio-ble-advertising
```

Complete result/manifest pairs match `expected_results/radio-ble-advertising-*`.
Wrong image size/hash and optimized execution fail before emulation. The
manifest includes both inherited replay dependencies. This adds no station
command or completed physical recovery trial. ACK is insufficient to establish
advertising; application flags do not expose the PAL state. Original/C2000
equivalence is unproved, and counts remain outside the combined runner.

## Radio initialization and query-provider continuation

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_ble_activation.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/radio-ble-activation
python3 tools/firmware_analysis/emulate_radio_native_info_queries.py \
  --output /tmp/radio-native-info
```

The [47-case initialization/activation suite](radio-ble-initialization-activation.md)
extends first-enable defaults, application BLE flag producers and the normal
provisioning callback. Configuration/network/OS boundaries remain explicit;
rollback and physical recovery are unproved. The
[23-case query suite](radio-native-info-queries.md) resolves MAC-provider
failure, Wi-Fi initialization and private network text. Its distinct-dispatch
cases establish boundary selection only, not controller inactivity.

Independent complete results and manifests match
`expected_results/radio-ble-activation-*` and `radio-native-info-*` byte for byte.
Both use the exact A1763 radio **0.3.3.0** image and synthetic providers;
optimized Python and wrong images are rejected. These 70 cases remain outside
the combined 1,842-case runner. The separate
[live `0003` validation](c1000-gen2-native-wireless-state-validation.md) sent
one read-only query; no `0024` station trial has been performed.

The [app HTTP-wrapper audit](c1000-ota-http-wrapper.md) is static instruction
analysis with no additional emulation-case count. It identifies a plaintext
OTA capture boundary; original main 1.7.1 acquisition remains pending.

## Radio network producers and original negotiation

```sh
PYTHONPATH=/tmp/solix-analysis-tools:tools/firmware_analysis \
  python3 tools/firmware_analysis/emulate_radio_network_producers.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output-dir /tmp/radio-network-producers
python3 tools/firmware_analysis/emulate_original_capability_negotiation.py \
  --output /tmp/original-capability-negotiation-results.json \
  --manifest /tmp/original-capability-negotiation-manifest.json
```

[31 radio producer cases](radio-network-state-producers.md) trace got-IP,
cached IPv4 and normal MQTT application callbacks on A1763 radio 0.3.3.0.
[39 original cases](c1000-power-method-and-negotiation-audit.md) execute
main-1.5.9 protocol-limit/session negotiation and bounded error branches.
Compare both complete result/manifest pairs with the corresponding
`expected_results/` fixtures. These 70 cases remain outside the combined
1,842-case runner; physical/asynchronous behavior and original main 1.7.1
are not established.

The [Android SDK inventory](android-sdk-native-boundaries.md) supplies a
separate tool accepting the exact externally retained APK pair. It produces
`android-sdk-boundary-{results,manifest}.json`, with no APK contents or secrets.
Its two identical static runs are not additional emulation cases. The
[AC-input-disable app audit](gen2-ac-input-disable-app-audit.md) is also static
instruction/object-pool analysis and adds no emulation count.

## Named action boundary and protected Android loaders

```sh
python3 tools/firmware_analysis/audit_gen2_iot_action_boundary.py \
  --output-dir /tmp/gen2-iot-action-boundary
python3 tools/firmware_analysis/inspect_android_loader_carriers.py \
  --base-apk /private/path/base.apk \
  --output-dir /tmp/android-loader-carriers
```

[48 action-boundary cases](gen2-iot-action-firmware-boundary.md) use the exact
bundled A1763 images, synthetic action/property shapes and a substituted
controller forwarder. Main firmware is inspected as data, with no setter
execution. Compare the full `gen2-iot-action-boundary-*.json` pair with
`expected_results/`; wrong images and optimized execution are rejected.

The [loader tool](android-loader-carriers.md) requires the exact privately
retained APK. It executes **3 bounded UPX stubs plus 256 pure protector byte
mapping cases**, with guest memory-only system-call substitutes and no Android,
JNI, host-call forwarding, station or network access. Complete result and
manifest pairs independently match `android-loader-carriers-*.json` fixtures.
Default output contains selected metadata only. Optional virtual-memory images
must remain in private local storage and are not rebuilt ELF/SDK files.

These 48/3/256 cases are separate from the combined 1,842-case runner.
The [Dart interceptor](gen2-dart-action-interceptors.md) and
[readable asset](android-readable-sdk-assets.md) audits are static evidence,
with no additional emulation count. No new charging setter follows from them.

The [pure string-initializer follow-up](android-loader-string-initializers.md)
uses the previous tool's private virtual images, verifies their exact hashes,
and regenerates function selection from relocation and exception metadata:

```sh
python3 tools/firmware_analysis/inspect_android_loader_strings.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --output-dir /tmp/android-loader-strings
```

Compare complete `android-loader-strings-*.json` outputs with `expected_results/`.
It adds **68 bounded pure native routines**, with memory guards, reset guest
state and no JNI/Android/system/device callbacks. Default output is selected
metadata only; it does not export decoded images or SDK code. These cases
remain separate from every earlier suite.

## Additional image integrity tools

These checks use the bundled vendor images and perform no emulation or device I/O:

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-radio-check \
  /tmp/solix-analysis-venv/bin/python tools/firmware_analysis/verify_radio_signature.py
/tmp/solix-analysis-venv/bin/python tools/firmware_analysis/extract_dsp.py \
  --output /tmp/solix-dsp-check
```

The radio checker verifies the original RSA-3072/PSS signature, image digest
and block CRC, then rejects modified digest/signature fixtures: three cases.
The DSP parser verifies all 159 nested record CRCs, rejects six corruption,
overlap and truncation fixtures, and exports sparse word-addressed big/little
endian images plus manifests. All inputs are hash-checked. These nine fixtures
are separate from the 1,842 instruction replay cases. DSP whole-component
checks and boot acceptance remain unresolved; radio signature success does
not establish the physical device's eFuse or flash-protection state.
