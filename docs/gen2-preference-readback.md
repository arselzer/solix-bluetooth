# Gen 2 saved-frequency and Smart-mode telemetry

Offline audit dated 2026-10-01 of **A1763 C1000 Gen 2 main 1.1.4.9**.
No device command or new connection was made. The normal A4 settings block
can expose three additional read-only fields through both BLE and native MQTT:

| SDK metric | A4 offset | Saved RAM / actual getter |
|---|---:|---|
| `ac_output_frequency_setting_hz` | 7 | `20001d6d`, `0801a580` |
| `ac_power_saving_mode_enabled` | 8 | `20001d67`, `0801a5ac` |
| `dc_power_saving_mode_enabled` | 13 | `20001d68`, `0801a5dc` |

Offsets include the value's type byte `04`. Serializer `0801a018` places
these getters into the full **34-byte** A4. The frequency byte is the saved
AC output setting: setter `0802b2d8` writes the same address, and the settings
loader accepts 50/60. It is **not a measurement of mains or inverter frequency**.
Smart flags report saved 0/1 configuration independently of output state.
Their asynchronous low-load policy can subsequently turn outputs off; see
the [preference investigation](gen2-preference-candidates.md).

The SDK/browser require the complete type/length before decoding these fields.
Other frequency values or Smart bytes produce `unknown`, replacing an earlier
valid value when metrics are merged. Absent or malformed blocks add no value;
partial telemetry can retain previous settings. Receiving an A4 does not
establish physical output behavior or the freshness of every controller cache;
see the [UART completion limits](gen2-uart-request-worker.md).

## Interfaces and C2000 naming correction

The fields appear in Python JSON status and the terminal/browser displays.
Home Assistant offers diagnostic sensors, disabled by default: C1000 Gen 2
frequency setting and Gen 2 AC/DC Smart flags. The initial readback audit exposed
no setters. A subsequent [native C1000 Gen 2 DC Smart trial](c1000-gen2-native-dc-smart-validation.md)
validated a guarded setter on main 1.1.4.9. The later
[native AC Smart trial](c1000-gen2-clock-ac-smart-validation.md) adds its separate
AC-off guard. Frequency and C2000 Smart remain read-only; neither C1000 setter
is exposed over Gen 2 BLE.

C2000's A4[7] previously appeared as `ac_input_frequency_hz`. Its physical
meaning has not been established from C2000 firmware. It now appears as
**`ac_frequency_raw`**, without assigning input/output or measurement semantics.
Consumers of the old JSON/Prometheus key must update their field name; no alias
is emitted. The browser removes that old label from restored cached telemetry.
This correction does not extend A1763 settings semantics to A1783.

## Verification and reproduction

Six retained read-only C1000 Gen 2 snapshots all contain type04/full34-byte A4,
frequency byte 50 and AC/DC Smart bytes 0/0. Captures and identifiers stay private;
this only checks agreement with the baseline, not a live settings round trip.

[24 synthetic instruction cases](../tools/firmware_analysis/emulate_gen2_preference_readback.py)
execute real getters/A4 serialization with frequency bytes 0/49/50/60/61/255
and both Smart flags. They preserve all 400 saved-setting bytes and the
input/output word. No handler, transport, persistence or output policy runs.
Inherited memory/timer substitutes remain documented in the
[reproduction guide](firmware-analysis-reproduction.md).

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-preference-readback \
  python3 tools/firmware_analysis/emulate_gen2_preference_readback.py
cmp /tmp/gen2-preference-readback/gen2-preference-readback-results.json \
  tools/firmware_analysis/expected_results/gen2-preference-readback-results.json
```

The exact bundled main-image hash is enforced. Python and browser regressions
cover wrong type/length, unsupported values, model isolation and native status
and incremental envelopes. Home Assistant API contracts retain the new values
while rejecting frequency and C2000 Smart commands. Later
C1000 Gen 2 native AC/DC Smart contract tests cover their separate guards; standalone
contracts do not simulate HA.
