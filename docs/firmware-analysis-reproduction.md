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
[24 normal preference-readback cases](gen2-preference-readback.md).
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
