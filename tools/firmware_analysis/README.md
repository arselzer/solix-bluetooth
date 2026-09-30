# Offline C1000 firmware replays

These Python scripts execute selected retained C1000 Gen 2 main-controller
instructions in Unicorn with synthetic inputs. They never connect to a device.
The combined runner has **1,842 cases**: 1,063 settings, 20 alert, 245 charging, 212 feature
candidate, 147 additional telemetry and 155 timeout cases. Radio/DSP integrity tools provide separate checks.
Additional standalone suites below have their own counts and are not included
in that total.

See [reproduction instructions](../../docs/firmware-analysis-reproduction.md)
for the required firmware hash, setup, tested versions and substituted services.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/run_replays.py --output /tmp/solix-replays
```

The bundled image in `firmware/c1000_gen2/1.1.4.9/` is checked before emulation.
Use `SOLIX_FIRMWARE_DIR=/path/to/firmware` to override the directory; it must
contain the exact `MainMcu-decoded.bin`. These tools do not download firmware.

`expected_results/` contains synthetic replay output, not device captures. The
runner compares every result with those files and writes a manifest of runtime,
firmware and source hashes. `verified-run.json` records the successful published
run and exact source hashes. Individual suite scripts accept
`SOLIX_ANALYSIS_OUTPUT` as their output directory. Do not use Python `-O`.

The helper modules are not device-control tools. They contain no BLE, MQTT, SSH,
HTTP or capture-ingestion code. A successful replay confirms the bounded code
path and its stated assumptions; it does not replace testing actual firmware
and hardware behavior.

For separate radio signature and sparse DSP integrity checks:

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-radio-check \
  python3 tools/firmware_analysis/verify_radio_signature.py
python3 tools/firmware_analysis/extract_dsp.py --output /tmp/solix-dsp-check
```

The DSP command exports word-addressed binary files for offline analysis. No
tool flashes a station. Vendor images have separate [notices](../../firmware/README.md).

## Original C1000 v1.5.9

The separate A1761 input is publicly downloaded vendor firmware, not the
tested station's installed 1.5.1 image. Run its extractor and actual ARM
handler replay independently:

```sh
python3 tools/firmware_analysis/extract_c1000_original.py --output /tmp/a1761-images
python3 tools/firmware_analysis/emulate_c1000_original_commands.py \
  --output /tmp/a1761-commands.json
python3 -m unittest discover -s tools/firmware_analysis -p test_c1000_original_package.py -v
python3 tools/firmware_analysis/emulate_c1000_smart_policy.py --output /tmp/a1761-smart.json
```

Seven extractor tests and **1,317 handler cases** cover integrity, decompressed
dispatch tables, fast-charge bit preservation, Smart mapping/serialization,
charge-power readback clamping and a library information reply. Persistence,
ACK transport and hardware are substituted; no direct local MQTT or physical
charging is established. See the [original investigation](../../docs/c1000-legacy-network-investigation.md).

The separate [Smart-policy suite](../../docs/c1000-smart-auto-off-policy.md)
has **1,347 cases**, including inherited counters, nominal timing, countdown
guards and power-cache serialization. It does not operate any device output.

The [bootstrap-state suite](../../docs/c1000-bootstrap-state-investigation.md)
adds **1,165 cases** for ACK handling, internal module messages, startup retry
state and timer registration:

```sh
python3 tools/firmware_analysis/emulate_c1000_network_state.py \
  --output /tmp/a1761-network-state.json
```

Its main-1.5.9 evidence does not decode the installed radio's HTTP/MQTT parser
or establish a safe remote reconnect command. The installed main remains
1.5.1; the newly offered 1.7.1 update has not been installed in this record.
The [app OTA capture audit](../../docs/c1000-app-ota-capture-investigation.md)
keeps app inputs, capture data and its separate two framing cases private.

## Energy accounting continuation

`emulate_energy_counters.py --output /tmp/gen2-energy-counters.json` adds
**49 actual-instruction scenarios and 1,800 arithmetic checks** separately
from the combined Gen 2 runner. It covers port getters, discrete sampling,
scheduler gaps, SysTick setup, report division and duration remainders.
See [the timing and calibration limits](../../docs/gen2-energy-counter-investigation.md).

## Schedule clock continuation

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-clock-check \
  python3 tools/firmware_analysis/emulate_schedule_clock.py
cmp /tmp/solix-clock-check/schedule-clock-results.json \
  tools/firmware_analysis/expected_results/schedule-clock-results.json
```

These **47 separate cases** combine actual clock synchronization, plan parsing,
tariff selection and D9/FE serialization with synthetic RTC and gate inputs.
See [UTC-offset retention](../../docs/gen2-schedule-clock-audit.md).

## Preference, LCD schedule and disaster-plan continuations

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-preferences \
  python3 tools/firmware_analysis/emulate_preference_candidates.py
python3 tools/firmware_analysis/emulate_timer_plan.py \
  --output /tmp/solix-clock-screen.json
cmp /tmp/solix-clock-screen.json \
  tools/firmware_analysis/expected_results/timer-plan-results.json
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-disaster \
  python3 tools/firmware_analysis/emulate_disaster_plan.py
```

- [Preferences](../../docs/gen2-preference-candidates.md): **1,060 cases** cover
  brightness, the ambient-light stub, raw language storage and Smart settings.
  Raw acceptance does not establish supported values or safe output behavior.
- [LCD clock screen](../../docs/gen2-timer-plan-investigation.md): **60 cases**
  establish `0091` as display/theme scheduling, not charging scheduling. Timer,
  display and asset-transfer boundaries are substituted. These are separate
  from the 47 tariff-clock cases above.
- [Disaster plans](../../docs/gen2-disaster-plan-investigation.md): **762 cases**
  cover manual/automatic activation and policy. Active plans request effective
  BMS limits 100%/1% and the internal fast-charge ceiling; cancellation can
  invalidate other windows. Saved settings alone are not the effective limits.
  No live actuator or physical AC-continuity guarantee is established.

These suites require the hash-checked C1000 Gen 2 main 1.1.4.9 image. Their
results and source hashes are independent of `verified-run.json`; use their
documented commands and substitution limits rather than adding their counts
to the combined runner's reported total.
