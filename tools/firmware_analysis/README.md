# Offline C1000 firmware replays

These Python scripts execute selected retained C1000 Gen 2 main-controller
instructions in Unicorn with synthetic inputs. They never connect to a device.
There are **1,842 cases**: 1,063 settings, 20 alert, 245 charging, 212 feature
candidate, 147 additional telemetry and 155 timeout cases. Radio/DSP integrity tools provide separate checks.

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
