# Original C1000 (A1761): Bluetooth support and open questions

## Support status

The Python package has a separate `Model.C1000` / `c1000` profile for
the original C1000/C1000X, A1761. An original C1000 was tested on 2026-09-30:
monitoring and twelve settings passed fresh readback and restoration checks.
Its B3 version code is **151**; a later device-originated local HTTP request
independently reported main **v1.5.1** and radio **v0.1.3.0**.
See [the chain test](c1000-chain-validation.md) for exact values and limits.
The C1000X sibling remains untested. Later that day, an official update completed
and **v1.7.1** was independently confirmed over Prime/AES-GCM Bluetooth.
Monitoring and six restored controls passed on that version: charging power,
Device Timeout, screen timeout, brightness, light and temperature units.
All 11 protected settings and the complete F8 block matched the baseline. See the
[isolated update capture](c1000-original-update-network.md).

On 2026-10-01, a [DC Smart trial and public-SDK repeat](c1000-prime-dc-smart-validation.md)
added a seventh verified Prime Bluetooth preference. Both directions require
fresh DC-output-off telemetry; only the intended F8 mode byte may change.
The [subsequent independent native DC Smart trial](c1000-native-dc-smart-validation.md)
also passed, bringing native MQTT to seven controls with the same DC-off guard.

The default profile uses the legacy P-256/AES-CBC handshake tested on 1.5.1,
without a Prime client ID. The updated test unit requires explicit `prime`
selection and a persisted 40-character client ID; its status request is still
`4040` and its telemetry uses the original-model map. The successful live tests
used the retained app identifier; generated-ID pairing on this model has not
yet been independently validated. C1000 Gen 2/A1763 remains `c1000_gen2`, with its
existing firmware-dependent protocol and separately verified controls.

## Telemetry and conflicting references

The implementation follows the overlapping fields in
[SolixBLE C1000 at 03bf48f](https://github.com/kb1ibt/SolixBLE/blob/03bf48f/SolixBLE/devices/c1000.py)
and the [A1761 MQTT map at c2f8769](https://github.com/thomluther/anker-solix-api/blob/c2f8769/src/anker_solix_api/mqttmap.py).
The browser's historical table has conflicting field labels and is not
used as a fallback.

| Typed TLV | Decoded meaning |
| --- | --- |
| A2 / A3 / A4 | AC/DC countdowns; bounded remaining-time estimate in 0.1 hours |
| A5 / A6 | AC input/output watts |
| A7 / A8 / A9 / AA | USB-C1/C2 and USB-A1/A2 watts |
| AE / B0 | DC input / total output watts |
| B2 | Packed DC status and output watts |
| B3 / BD / BE | Main version code / main and expansion temperature |
| BF / C0 | Raw main/expansion BMS state codes; strict `01 + byte`, without inferred UPS labels |
| C1–C5 | Main/expansion charge, health, expansion count |
| D0 / D1 / D2 / D3 | Serial / charging limit / device timeout / display timeout |
| D7 / D8 / D9 / DC / DE | AC/DC switches / display brightness / light mode / display switch |
| DD / E5 | Fahrenheit display preference / fast-charge switch |
| F8 | Type01 length3 or type04 length21; DC/AC modes at offsets 1/2: 1=Normal, 2=Smart |

`AF` is called total input in one source and photovoltaic power in another;
it remains raw. `BC` is exposed only as `charging_source_code`, not a battery
charge/discharge state. Mains presence and battery discharge are not inferred
from watts. The tested unit reported plausible SOC, temperature, health and
output power. Health was not independently measured. Every raw TLV remains
available for comparison.

The LCD showed 99.9 with mains connected while A4 contained a larger estimate;
other connected samples contained `ffff`. The decoder retains
`time_remaining_raw` and reports `time_remaining_minutes="unknown"` for values
above 999, including `ffff`, to clear a previously cached numeric estimate.
During an upstream outage, values 153–178 decoded to 15.3–17.8 hours; this is
an estimate, not a measured discharge duration. A5 stayed zero during bypass
and BC stayed zero with and without upstream power. Neither field establishes
mains absence; A5 may describe battery charging rather than all AC input.

## Controls

| Setting | BLE command | A2 value | Allowed values |
| --- | --- | --- | --- |
| Display timeout | `4046` | `02` + uint16 LE | 20, 30, 60, 300, 1800 seconds |
| Display brightness | `404c` | `01` + byte | 0–3 |
| Display enabled | `4052` | `01` + boolean | 0/1 |
| Light mode | `404f` | `01` + byte | 0–4 |
| AC charging power | `4044` | `02` + uint16 LE | 100–1000 W, 100 W steps |
| AC/DC output enabled | `404a` / `404b` | `01` + boolean | 0/1 |
| Device Timeout | `4045` | `02` + uint16 LE | 0=Never; 30,60,120,240,360,720,1440 minutes |
| Fahrenheit display preference | `4050` | `01` + boolean | 0=Celsius, 1=Fahrenheit |
| Fast charge | `405e` | `01` + boolean | 0/1 |
| DC/AC Smart mode | `4076` / `4077` | `01` + boolean | 0=Normal, 1=Smart |

All bodies also carry `A1=21` and `FE=03` + Unix seconds LE32. BLE command
numbers and application identifier differ from their MQTT counterparts.
Live-tested pairs were timeout 30/60 s, brightness 2/3, display off/on,
light 0/1, charging power 1000/300 W, and both directions of AC/DC output.
The later [MQTT bridge test](c1000-bridge-charging-and-bypass.md) also verified
1000/100 W with outputs kept on. Charging power below the reported load did
not establish forced battery operation while AC input remained supplied.
Other table values remain reference-derived.

The later [preferences trial](c1000-preferences-validation.md) confirmed
Celsius→Fahrenheit→Celsius, fast off→on→off, and both Smart→Normal→Smart
cycles. **The upstream inverted Smart command values disagree with this
hardware:** wire 0 selects Normal/status 1, wire 1 selects Smart/status 2.
An independent original-model 1.5.9 firmware replay agrees. Do not send status
value 2 as a command. Smart mode can automatically stop an output at low load;
the short loaded trial verifies its configuration, not its shutdown threshold.
The [1.5.9 Smart-policy replay](c1000-smart-auto-off-policy.md) shows that
enabling Smart can inherit an already accumulated low-load interval.
The generic `send_command` path permits only status for this model; dedicated
control methods validate types and ranges. There is no opt-in flag. Use the
standard `set_ac_charging_power`, `set_display_timeout`,
`set_ac_output_enabled`, and `set_light_mode` methods, or
`set_c1000_setting(setting, value)` for the full table. CLI power/display/AC/light
commands and `c1000-setting` expose the same controls. Dedicated SDK methods
also include `set_temperature_unit`, `set_fast_charge_enabled`,
`set_ac_power_saving_enabled` and `set_dc_power_saving_enabled`.
The bridge and authenticated gateway expose those preference settings with
semantic boolean values; the gateway has no AC/DC output-switch command.

Explicit nonzero or empty device acknowledgement status raises an error.
Successful completion requires each expected field to be freshly decoded
after the write and match the requested value. Neither cached metrics nor a
success acknowledgement alone proves a change. This detects protocol errors
and readback mismatches; it does not verify physical behavior beyond telemetry.

## Using the profile

```bash
solix-link add --name original --model c1000 --address AA:BB:CC:DD:EE:04
solix-link monitor --name original
# After recording the current display timeout and output states:
solix-link set-display-timeout --name original --seconds 60
# Additional C1000 settings use the same confirmation path:
solix-link c1000-setting --name original --setting display_brightness --value 2
```

First compare battery, temperature, power, switches, and firmware against the
app/display. Start with a display setting, confirm fresh telemetry, then send
the recorded baseline value to restore it. The `c1000-setting` CLI prints its
baseline and refuses a write if that setting is absent. A timeout does not prove that the
device ignored the command; reconnect and inspect before retrying. Keep
captures private. Output-switch tests require a noncritical load; none are
performed automatically by monitoring or the HTTP server.

## MQTT and missing functionality

The **BLE-to-MQTT bridge** supports charging power, display/device timeout,
temperature units, fast charge, Smart modes, AC output and light.
Here the station communicates over Bluetooth; the bridge connects
to your broker. The HTTP gateway exposes charging power, display timeout and
light, with no AC-output API command.
The complete original-C1000 TCP MQTT/Paho/BLE write and restoration path is now
verified; station Wi-Fi is unnecessary for the bridge.

The upstream A1761 map includes cloud MQTT commands. The later
[local native trial](c1000-original-mqtt-followup.md) established isolated
bootstrap, mutual TLS and six confirmed native controls on main **1.7.1 /
radio 0.3.3.0**; [DC Smart later added a seventh](c1000-native-dc-smart-validation.md).
The AP service supports the original 16-character serial and
original provisioning layout. This trial used the existing account ID;
generated-ID setup remains unverified. The earlier main 1.5.1/radio 0.1.3.0
[Wi-Fi trial](c1000-original-wifi-validation.md) remains historical evidence.

Connected original firmware can suppress setting ACKs and route `0040` status
through full `0405` reports. The server sends each verified setter once and
confirms two fresh complete reports, preserving other settings and F8 bytes.
Network-only `0407` messages cannot satisfy these checks.
Device Timeout is now exposed through the SDK, CLI, bridge and gateway/HA.
The [timeout trial](device-timeout-behavior.md) confirmed legacy `4045/A2`
and D2 readback for 720→0→720 minutes while keeping AC output on. Never
disables the saved timeout; it cannot guarantee uninterrupted radio access.

Open work includes generated-ID original native setup; output timers; actual fast-charge rate
and Smart-mode low-load behavior; expansion-battery data;
charge/discharge limits if supported; and reliable mains/battery-state mapping.
The reference lists additional commands, but their BLE numbers and physical
behavior need validation. No reserve or tariff capability has been established
on the original C1000.

The [charging-producer follow-up](c1000-bypass-firmware-followup.md) found separate
internal charging/output gates but no supported external command selecting
battery power while retaining AC output. A zero ceiling is not charge pause.
