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
