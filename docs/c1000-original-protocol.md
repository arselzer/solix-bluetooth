# Original C1000 (A1761): ready for hardware testing

## Support status

The Python package now has a separate `Model.C1000` / `c1000` profile for
the original C1000/C1000X, A1761. **No original C1000 was connected or
tested during this implementation.** Synthetic tests verify packet shapes,
model guards, configuration, and decoder behavior. Earlier browser support
claims do not establish that this new Python implementation works on hardware.

The profile uses the legacy P-256/AES-CBC handshake and `4040` status request.
It needs no Prime client ID. C1000 Gen 2/A1763 remains `c1000_gen2`, with its
existing firmware-dependent protocol and separately verified controls.

## Telemetry and conflicting references

The implementation follows the overlapping fields in
[SolixBLE C1000 at 03bf48f](https://github.com/kb1ibt/SolixBLE/blob/03bf48f/SolixBLE/devices/c1000.py)
and the [A1761 MQTT map at c2f8769](https://github.com/thomluther/anker-solix-api/blob/c2f8769/src/anker_solix_api/mqttmap.py).
The browser's historical table has conflicting field labels and is not
used as a fallback.

| Typed TLV | Meaning prepared for testing |
| --- | --- |
| A2 / A3 / A4 | AC/DC countdowns; remaining time in 0.1 hours |
| A5 / A6 | AC input/output watts |
| A7 / A8 / A9 / AA | USB-C1/C2 and USB-A1/A2 watts |
| AE / B0 | DC input / total output watts |
| B2 | Packed DC status and output watts |
| B3 / BD / BE | Main version code / main and expansion temperature |
| C1–C5 | Main/expansion charge, health, expansion count |
| D0 / D1 / D2 / D3 | Serial / charging limit / device timeout / display timeout |
| D7 / D8 / D9 / DC / DE | AC/DC switches / display brightness / light mode / display switch |

`AF` is called total input in one source and photovoltaic power in another;
it remains raw. `BC` is exposed only as `charging_source_code`, not a battery
charge/discharge state. Mains presence and battery discharge are not inferred
from watts. Health, version formatting, and all field transitions still need
hardware confirmation. Every raw TLV remains available for comparison.

## Controls available for testing

| Setting | BLE command | A2 value | Allowed values |
| --- | --- | --- | --- |
| Display timeout | `4046` | `02` + uint16 LE | 20, 30, 60, 300, 1800 seconds |
| Display brightness | `404c` | `01` + byte | 0–3 |
| Display enabled | `4052` | `01` + boolean | 0/1 |
| Light mode | `404f` | `01` + byte | 0–4 |
| AC charging power | `4044` | `02` + uint16 LE | 100–1000 W, 100 W steps |
| AC/DC output enabled | `404a` / `404b` | `01` + boolean | 0/1 |

All bodies also carry `A1=21` and `FE=03` + Unix seconds LE32. BLE command
numbers and application identifier differ from their MQTT counterparts.
These commands are reference-derived, **not newly verified controls**.
The generic `send_command` path permits only status for this model; dedicated
control methods validate types and ranges. There is no opt-in flag. Use the
standard `set_ac_charging_power`, `set_display_timeout`,
`set_ac_output_enabled`, and `set_light_mode` methods, or
`set_c1000_setting(setting, value)` for the full table. CLI power/display/AC/light
commands and `c1000-setting` expose the same controls. The MQTT bridge exposes
`ac_charging_power`, `display_timeout`, `ac_output`, and `light_mode`.

Explicit nonzero or empty device acknowledgement status raises an error.
Successful completion requires each expected field to be freshly decoded
after the write and match the requested value. Neither cached metrics nor a
success acknowledgement alone proves a change. This detects protocol errors
and readback mismatches; it does not verify physical behavior beyond telemetry.

## First hardware test

```bash
solix-gen2 add --name original --model c1000 --address AA:BB:CC:DD:EE:04
solix-gen2 monitor --name original
# After recording the current display timeout and output states:
solix-gen2 set-display-timeout --name original --seconds 60
# Additional C1000 settings use the same confirmation path:
solix-gen2 c1000-setting --name original --setting display_brightness --value 2
```

First compare battery, temperature, power, switches, and firmware against the
app/display. Start with a display setting, confirm fresh telemetry, then send
the recorded baseline value to restore it. The `c1000-setting` CLI prints its
baseline and refuses a write if that setting is absent. A timeout does not prove that the
device ignored the command; reconnect and inspect before retrying. Keep
captures private. Output-switch tests require a noncritical load; none are
performed automatically by monitoring or the HTTP server.
