# SOLIX Gen 2 Bluetooth field notes

These notes record observations made on one C1000 Gen 2 (A1763) and one C2000 Gen 2 (A1783) on 2026-09-28 through 2026-09-30. They are a compatibility record, not a claim that every regional model or firmware behaves identically. No account ID, Bluetooth address, serial number, or phone capture is included here.

## Tested versions and outcome

| Device | Firmware | Session | Observed behavior | Final state |
| --- | --- | --- | --- | --- |
| C1000 Gen 2 | 1.1.4.3 | Legacy ECDH + AES-CBC | `0001` negotiation, `4100` subscription, live telemetry | 100% battery; AC on; DC off before update |
| Same C1000 Gen 2 | 1.1.4.9 | Prime ECDH + AES-GCM | Legacy opening request became silent/disconnected; Prime `4001` received `4801`; generated ID paired after short main button press; `4100` telemetry; AC/DC outputs, charge limits, and AC charging power verified | 100% battery; AC on; DC off; charge limits 100%/1%; AC charging power 1200 W after tests |
| C2000 Gen 2 | Main software 2.1.6.4 from live `F9` block | Prime ECDH + AES-GCM | Generated ID paired after short main button press; live telemetry, screen timeout, AC charging-power setpoint, upper charge cap, and actual capped charging verified; MQTT bridge tested | AC output remained on, serving servers; upper cap restored to 90%, lower limit stayed 1%, screen timeout restored to 30 s, and AC charging power restored to 1800 W |

The official Anker app performed the C1000 firmware update from 1.1.4.3 to 1.1.4.9. A user with 1.1.4.3 must select `legacy` in the browser or CLI config. The browser and Python package default to `prime` for current tested firmware. The C2000 main version was decoded directly from its live BLE `F9` block; it has not been cross-checked against the app's device information screen. Protocol selection for this C2000 is Prime only.

**Native MQTT update:** C2000 main 2.1.6.4 now connects directly to a local
TLS MQTT listener on the isolated network, including client-certificate
validation, saved-configuration reconnect, and status/telemetry requests.
This is separate from the BLE bridge. See [native MQTT findings](local-mqtt-investigation.md).
Native charging-power and mode/reserve/schedule writes are now verified too.
The [corrected Peak trial](c2000-corrected-peak-trial.md) now confirms active
Peak and battery discharge with mains connected. AC output stayed enabled;
restored Standard mode preceded the independently confirmed return to grid.

**2026-09-30 C1000 update:** main 1.1.4.9/radio 0.3.3.0 now works with native
MQTT using a generated local ID and certificates. Content-Length credential
framing and a 15-second startup grace resolved the latest setup/request failures.
Charging power, upper cap, reserve, active Peak battery supply, Mid-Peak grid
supply and guarded grid return were confirmed. Original settings were restored
over MQTT and independently checked over BLE. Earlier failed setup trials below
remain historical evidence. See [C1000 local MQTT findings](c1000-local-mqtt.md)
and [C2000 energy-report lifecycle](energy-report-lifecycle.md).

## Pairing and connection

Prime negotiation observed: `4001/4801`, `4003/4803`, `4029/4829`, `4005/4805`, `4021/4821` for P-256 key exchange, `4022/4822`, then `4027/4827` for registration. The registration payload contains a 40-character hexadecimal client ID. A newly generated ID produced response byte `09` on both stations. **One short press of the main power button**, followed by a `4027` retry on the same BLE connection, produced response byte `00`; telemetry then started after `4100`. Reuse of the ID worked on reconnect without another press. Do not hold the button: [Anker's C2000 guide](https://salesforce-knowledge-download.s3.us-west-2.amazonaws.com/000032532/en_US/000032532.pdf) distinguishes its short pairing press from a long power-off press.

The account ID was not recoverable from the initial registration capture alone because the session uses fresh ECDH and encrypted registration. A later C1000 HCI capture also contained encrypted telemetry with known plaintext in the *same session*; the repeated Prime GCM nonce made the app's account ID recoverable locally from subsequent provisioning writes. That value remains only in the ignored private capture. Local pairing with a generated ID made account ID recovery unnecessary on both stations. Either ID is a pairing credential: keep it in a private config file and reuse it. The old Anker app remained able to show the C2000 after local pairing, although live concurrent app/BLE operation was not fully characterized.

An active phone Bluetooth connection can prevent laptop BLE discovery. Stop the phone's Bluetooth connection before scanning or pairing from a laptop or Home Assistant node. The computer must be physically near the station or use a compatible Bluetooth adapter/proxy.

## Telemetry and commands

The custom GATT service is `8c850001-0302-41c5-b46e-cf057c562025`; command writes use characteristic `...0002...` and notifications use `...0003...`. Frames use `ff09`, little-endian length, three-byte pattern, two-byte command, payload, and XOR checksum. `030001` is negotiation, `03000f` is a request, and `03010f` is a response. Prime encrypts with AES-GCM and fragments larger telemetry responses; legacy C1000 uses AES-CBC. Both Gen 2 models need `4100` subscription (`a10121`) to start telemetry. Prime adds `a20a040100e3fbfcfe000000` and a timestamp TLV to the subscription.

| Command | Payload before protocol timestamp | Test result |
| --- | --- | --- |
| `4100` | `a10121` | Telemetry on both stations; read-only |
| `4101` | `a10121a2020101` / `a10121a2020100` | C1000 AC on/off verified on 1.1.4.9; final state restored to on |
| `4102` | `a10121a2020101` / `a10121a2020100` | C1000 DC on/off verified on 1.1.4.9; final state restored to off |
| `4103` | `a10121aa0201UUab0201LL`, where `UU` is the upper charging percent and `LL` is the lower discharge percent | Decoded from app writes and independently verified by setting upper 95%, then restoring 100%; lower stayed 1% |
| `4103` | `a10121aa0201UU`, where `UU` is the upper charging percent | C2000 upper cap verified 90→85→90% and 90→95→90%; lower discharge limit stayed 1%, AC output stayed on |
| `4103` | `a10121a40302SSSS` + `FD` millisecond timestamp, where `SSSS` is screen timeout in little-endian seconds | C1000 and C2000 both confirmed 30→60→30 seconds; C2000 AC output stayed on |
| `4101` | `a10121a40302WWWWab03020000` + `FD` millisecond timestamp, where `WWWW` is AC charging-power limit in little-endian watts | C1000 and C2000 both confirmed setting and restore; C2000 tested 1800→1700→1800 W and 1800→300→1800 W |
| `4090` / native `0090` | Corrected typed fields `A2=0101`, `A3=0100`, `A4=0100`, `A6=0101`, `A7=04010018` + `FD`; native uses `A1=22` | C2000 native MQTT verified one-slot storage, active Peak and discharge with mains connected. Standard clear uses `A2=0100`, `A6=0100`, `A7=0400`, retaining A3/A4 zero. Corrected activation via BLE alone is untested. Old A6=4/A7=0401010018 encoding was malformed. See the [live trial](c2000-corrected-peak-trial.md). |
| `4090` | `a10121a50201RR` + `FD` millisecond timestamp, where `RR` is backup reserve percent | C2000 reserve 10→85→10% verified in telemetry while AC output stayed on |
| `4090` | `a10121a2020101a3020100a4020100a6020104a7050401010018` + `FD` millisecond timestamp | C2000 full-field Time-of-Use schedule write verified: one Peak slot, 00:00–24:00; `A7` contains binary type `04`, slot count `01`, tariff `01`, start `00`, end `18`. `D9` confirmed the slot. Peak did not become active in this trial. The analogous Off-Peak tariff is `03`; a zero-slot `A7=0400` cleared the plan. Meanings of `A3`, `A4`, and `A6` remain uncertain. |

The live telemetry decoder currently extracts battery percentage/health/temperature, total output power, AC input power and presence, AC and DC output states and power, battery work status and time remaining, charge limits, and the configured C1000 AC charging power where present. The latter is `A4[5:7]` (little-endian): it changed from 1200 to 1000 W and back; `A3[5:7]` stayed at 1200 W. C1000 offsets are based on the [SolixBLE C1000 Gen 2 implementation](https://github.com/flip-dots/SolixBLE/blob/main/SolixBLE/devices/c1000g2.py) and live checks. The [C2000 Gen 2 decoder under review](https://github.com/flip-dots/SolixBLE/pull/72) identifies the shared `A7[4]` mains presence, `A3[1]` battery work status and `A6[7:9]` time remaining fields. The Python monitor retains all raw TLVs locally for later decoding.

### UPS input and battery state, tested live

On the C1000 with AC output **left on**, we captured 75 read-only status samples while its AC **input** was plugged in, unplugged briefly, then replugged. The local raw capture is `.solix-private/c1000-ups-transition-*.jsonl`; it is ignored by Git. `A7[4]` changed **1 → 0 → 1** exactly with the input lead, and `A6[3:5]` AC input watts changed roughly **85 → 0 → 85 W**. AC output remained enabled and supplied roughly 85 W throughout. `A3[1]` work status changed **0 (idle) → 1 (discharging) → 0**. During the unplugged interval, `A6[7:9]` was 182 in little-endian tenths of an hour, or an estimated **18.2 hours to empty**; that estimate was not checked against an actual discharge. A separate read-only C2000 sample showed `A7[4] = 1` with AC input connected and AC output still on. Its AC input was **never** unplugged, so a C2000 outage transition remains unverified on this hardware.

The library and browser now expose `ac_input_connected` (1 = mains present, 0 = absent), `battery_status` (`idle`, `discharging`, `charging`, or `unknown`), `battery_discharging` (0/1), and `time_remaining_minutes`. `time_remaining_minutes` is the device's estimate while charging or discharging and is 0 when idle or status is unknown. Use `ac_input_connected` for outage alerts, because zero input watts alone also occurs when the battery is full or charging is paused. Check station availability separately before interpreting the last reading. An explicit fault/alarm code, battery health diagnostics, and a C2000 live outage test are still missing.

On 2026-09-29, six additional **read-only** C2000 status samples from the HA node contained the `F9` version block and `C0` expansion block. Following the [C2000 Gen 2 field map under review](https://github.com/flip-dots/SolixBLE/pull/72), all six decoded main software `2.1.6.4`, controller `5.0.7.1`, inverter `5.0.7.0`, BMS `9.3.3.0`, and wireless module `3.3.0.0`. The same samples reported 50 Hz at `A4[7]`, 1800 W configured AC charging power at `A4[5:7]`, and no expansion battery in `C0`. The Python and browser decoders now expose these read-only fields with length checks; both were checked against all six saved samples. The raw samples, including device identifiers, remain only in `.solix-private/solix-c2000-readonly-20260929.jsonl`. No fault/alarm code was identified, and no output-control command was sent.

A separate C2000 read-only session received **nine spontaneous telemetry updates in 30 seconds** after the ordinary `4100` subscription, without a poll during that interval. AC output remained on. This confirms prompt push updates in that session; it does not establish an outage-notification latency or guarantee the same behavior after every reboot. We subsequently sent the [public C2000 `4057` realtime-latch request](https://github.com/flip-dots/SolixBLE/pull/72), `a10121a2020101`, in a separate session. The station had already emitted about six spontaneous updates in the preceding 20 seconds; it emitted four in the following 20 seconds. The request did not increase the observed update rate or alter the 90%/1% charge limits, 1800 W charging-power limit, battery idle state, mains presence, or enabled AC output. The latch may already have been armed from an earlier session, so this trial does not establish its effect on a fresh station. Its private timing log is `.solix-private/c2000-realtime-latch-results-20260929.json`.

The same [C2000 field map](https://github.com/flip-dots/SolixBLE/pull/72) identifies further read-only `A4` bytes. Against our retained live 34-byte `A4` block, the decoder now reports AC/DC output timer countdowns of 0 seconds, AC/DC power-saving modes off, device timeout 0 minutes (Never), fast charge off, and output-port memory on. These values fit the captured baseline, but their transitions were not independently exercised on this C2000. In particular, timer countdowns are current remaining times, and a display-on bit elsewhere in `A4` may describe the current screen rather than a saved preference. No C2000 AC-output or timeout control was sent for this decoding work.

### Safe C2000 screen-timeout test

On 2026-09-29, the HA node connected to the C2000 with its saved Prime client ID. Its 34-byte `A4` settings block reported screen timeout 30 seconds at offsets 16–17, matching the C1000 layout. A private C2000-only script sent `4103` with only `A1`, `A4=60 seconds`, and the app-observed `FD` timestamp. Telemetry confirmed **30→60→30 seconds** after restoring the baseline. During all three states, mains input was present, AC output remained enabled, battery stayed at 90%, and AC output power was about 393 W. The full result log is retained only in `.solix-private/c2000-display-timeout-results-20260929.jsonl`. The Python library and CLI allow this C2000 screen setting at the two tested values; its AC charging-power setting was verified separately below. This result does not show that the C1000 will reappear over Bluetooth or establish why it stopped advertising.

### C2000 AC charging-power limit

The same HA node sent the C1000-tested `4101/A4` charging-power payload to the C2000, with no `A2` AC-output switch field. At a baseline of 90% battery, 90% upper charge limit, mains present, and AC output enabled, telemetry confirmed **1800→1700→1800 W** and **1800→300→1800 W**. The AC output stayed enabled in every sample, including when its load rose from about 434 W to 1006 W during the 300 W trial. The battery stayed idle at its charge cap, so this first test proves the station accepted and reported the configured limit. The starting 1800 W value was restored. Raw results are retained only in `.solix-private/c2000-charge-power-results-20260929.jsonl`. The Python client, CLI, and MQTT bridge expose this C2000 setting from 300 to 1800 W in 100 W steps, with telemetry confirmation; 300, 1700, and 1800 W were live-tested, and intermediate steps follow the same observed encoding. A separate 500 W charging test is described below. C2000 lower discharge-limit, fast-charge, and output writes remain blocked in this library.

A final read-only C2000 check after the CLI and MQTT tests reported 90% battery, mains present, AC input and AC output both 410 W, AC output enabled, charging-power limit 1800 W, and display timeout 30 seconds. That private snapshot is `.solix-private/c2000-final-baseline-20260929.json`.

### C2000 upper charge cap and controlled charging

On 2026-09-29, the HA node sent `4103` with only `A1` and `AA`, without the C1000's `AB` lower-limit field. C2000 telemetry confirmed **90→85→90%** with the lower discharge limit fixed at 1%. In a second test, the station started at 90% battery, 90% upper cap, 1800 W configured charging power, mains present, AC output enabled, and roughly 400 W of AC load. We first set its charging-power limit to **500 W**, then raised its upper cap **90→95%**. The station reported `charging`; AC input increased to **923–927 W** while AC output remained **401–411 W**. The input/output difference of roughly 515–526 W is consistent with a 500 W charging limit plus conversion and operating overhead, but it is not a calibrated battery-side charge-rate measurement. AC output stayed enabled throughout.

We restored the cap **95→90%** and charging-power limit **500→1800 W**. After a short delay, read-only telemetry showed 90% battery and `idle`, with AC input and output both 396 W. A later check after the CLI and MQTT tests again showed `idle`, upper/lower limits 90%/1%, AC input and output both 360 W, AC output enabled, 1800 W charging-power limit, and 30-second screen timeout. Private logs are `.solix-private/c2000-charge-cap-results-20260929.jsonl`, `.solix-private/c2000-controlled-charge-results-20260929.jsonl`, `.solix-private/c2000-postcharge-final-20260929.json`, and `.solix-private/c2000-final-readonly-20260929.json`. The Python client, CLI, and MQTT bridge now expose the C2000 upper cap at 80–100% in 5% steps with telemetry confirmation. This cap controls whether mains charging can resume at the present battery level; it does not select battery discharge for AC loads or provide a Time-of-Use schedule.

## Charge-limit capture and remaining controls

The user changed the C1000 upper limit 100→90→100 and lower limit 1→5→1 in the Anker app. Two encrypted `4103` writes for each pair differed at only one byte: offset 6 changed by `0x3e` (90 XOR 100), and offset 10 by `0x04` (5 XOR 1). Because the app reused the same AES-GCM session nonce, the known timestamp prefix of its encrypted `4027` registration revealed the first eight bytes of `4103` without revealing the phone's registration ID: `a10121aa0201UUab`. The remaining bytes form `ab0201LL`. An initial laptop trial mistakenly used `A2`/`A3`, which did not change telemetry. The corrected `AA`/`AB` command then changed the upper limit to 95% and restored 100%, each confirmed in telemetry. The public Python method repeated the 95→100% test successfully.

The user also changed AC charging power 1200→1000→300→1200 W in the app. These were `4101` writes with `A4` watt values; the public Python method changed 1200→1000→1200 W, with telemetry confirmation, and later 1200→300→1200 W with a 771 W AC load. The `4101` packet also includes `AB` set to zero and an `FD` TLV containing `00` followed by a 13-digit Unix millisecond timestamp. The Python client and CLI expose these two controls along with display timeout and fast charge, described below; all four check telemetry after a write. The implementation limits choices to upper 80–100% in 5% steps, lower 1%, 5%, 10%, 15%, or 20%, and AC charging power 300–1200 W in 100 W steps. The app did not offer a 2% lower limit on this tested unit. Upper 80%, 95%, and 100%, plus charging power 300/1000/1200 W, were independently exercised from the laptop; the other choices follow the app's observed range and encoding and may vary on other variants.

### Other C1000 app writes decoded from the private capture

These were identified by the user's timed app sequence on firmware 1.1.4.9. Their direct laptop behavior still needs checking. `FD` in the table means the app appended `fd0e00` plus a 13-digit Unix millisecond timestamp.

| App action | Command and parameter bytes before `FD` | Observation |
| --- | --- | --- |
| Car charger output on/off | `4102 a10121a2020101` / `...00` + `FD` | Same command family as the tested DC output switch |
| Screen display off/on/off | `4103 a10121a2020100` / `...01` + `FD` | Final app action was off |
| AC output end time | `4101 a10121a30503` + uint32 little-endian seconds | 3600, 7200, 10800, 47100 (13 h 5 min), then 0/off |
| Smart AC output mode on/off | `4101 a10121a6020101` / `...00` + `FD` | Final app action was off |
| Device timeout | `4103 a10121a60302` + uint16 little-endian minutes + `FD` | 0 = Never; 360 = 6 h; user chose to keep Never |
| Output port memory off/on | `4103 a10121a8020100` / `...01` + `FD` | Final app action was on |

The [A1763 MQTT field map](https://github.com/thomluther/anker-solix-api/blob/main/src/anker_solix_api/mqttmap.py) describes Smart AC output mode as automatic AC-output shutoff below roughly 14 W. That is a low-load output feature, not a verified battery-versus-grid scheduling control. Its effect has not been tested on this C1000.

The app uses `4100 a10121a206040100031803fe04<Unix seconds>` for C1000 telemetry subscription in this capture. The Python Prime subscription currently uses a longer `A2` value observed on C2000; it also worked live on the updated C1000. Command codes are reused for different settings, so a command number alone does not identify an operation.

The [public A1763 command map](https://github.com/thomluther/anker-solix-api/blob/main/src/anker_solix_api/mqttmap.py) independently places the app-captured display switch at `4103/A2`, device timeout at `4103/A6`, and output-port memory at `4103/A8`. A private C1000-only script now prepares one reversible test of those three settings, records the starting values, confirms each write through telemetry, and attempts to restore each original value even if confirmation times out. Its prepared plaintext packets match the app-captured field layouts offline. **It has not sent a setting write:** on 2026-09-29 the saved C1000 did not appear in a 45-second HA-node scan, a direct BLE connection timed out, and the moved laptop saw no SOLIX station. The script and prospective result log are ignored by Git.

The retained C1000 mains-loss capture gives a useful caution about the display field. `A4[22]` changed from 0 to 1 about two seconds after AC input was unplugged, while `A7[4]` had already fallen to 0; the display bit stayed 1 after mains returned. It may report the **current screen state**, which a power event can wake, rather than a durable preference. Across the same 75 samples, `A4[8]` (smart AC output mode) stayed 0, `A4[23]` (port memory) stayed 1, `A4[14:16]` (device timeout) stayed 0/Never, and `A4[1:5]` (AC output timer) stayed 0. These match the app's final settings but do not independently verify their effects.

### Additional C1000 settings verified from the laptop

The published [A1763 MQTT command map](https://github.com/thomluther/anker-solix-api/blob/main/src/anker_solix_api/mqttmap.py) describes the same `A4` charging-power and `AA`/`AB` SoC-limit fields already verified through BLE. It also identifies `A4` for display timeout in command group `0103` and `A7` for fast charging in `0101`. We tested the corresponding BLE groups `4103` and `4101` on this C1000, adding the app-observed `FD` millisecond timestamp suffix:

| Setting | BLE payload before `FD` | Telemetry confirmation | Test |
| --- | --- | --- | --- |
| Display timeout | `4103 a10121a40302SSSS`, uint16 little-endian seconds | `A4[16:18]` | 30 → 60 → 30 seconds |
| Fast charge switch | `4101 a10121a7020101` / `...00` | `A4[21]` | Off → on → off |

Both writes changed the reported value and restored its original state. The C1000 remained at 100% battery with AC input present and AC output enabled throughout. Its display switch stayed off, and the AC charging-power limit stayed at 1200 W. Since the battery was full, the fast charge test confirms the switch and does not measure a faster charge rate. The Python library and CLI now expose both verified settings. The decoder also exposes C1000 device/display/AC/DC timeout fields, display mode and switch, port memory, and temperature unit from `A4`; their offsets match the published map and the observed baseline, but changes to each were not independently exercised.

Anker lists [time-of-use mode for the C1000 Gen 2](https://www.ankersolix.com/products/c1000-gen2) and [time-of-use, Storm Guard, and Fast Charging Plan for the C2000 Gen 2](https://www.ankersolix.com/products/c2000-gen2-2). Local C2000 mode, reserve, and schedule **storage** writes are now verified below; selecting a live tariff and battery-versus-grid discharge remain unresolved. The [A1763 command map](https://github.com/thomluther/anker-solix-api/blob/main/src/anker_solix_api/mqttmap.py) does not currently describe a C1000 Gen 2 Time-of-Use command; that is a limit of the map, not proof that the device cannot support one. Ultra Fast's setting switch is now verified locally as described above. The browser exposes verified C1000 AC/DC, charge limits, and AC charging power controls; the Python client and CLI also expose display timeout and fast charge. The HTTP server remains read-only. The C2000 Python client, CLI, and MQTT bridge expose its verified upper charge cap, screen timeout, and AC charging-power limit; its experimental mode, reserve, and schedule writes are documented below but not exposed as general controls. All C2000 AC/DC output controls remain blocked because it powers servers.

### Battery discharge while AC output stays enabled

[Anker's A1783 C2000 Gen 2 guide](https://support.ankersolix.com/s/article/Anker-SOLIX-C2000-Gen-2-Portable-Power-Station-User-Guide-A1783-Australia) says Standard mode passes connected grid power directly to the AC outputs. In Time-of-Use mode, a **Peak** period instead prioritizes solar, then the station battery, then grid for AC output, provided battery state of charge is above the backup reserve. Mid-Peak and Off-Peak periods prioritize grid for AC output. Thus Time-of-Use Peak is the documented way to discharge the battery while leaving both mains input and AC output connected.

Anker's [C2000 Gen 2 app guide](https://lp.ankerjapan.com/hubfs/aoos/manual/A1783Guide.pdf), page 8, explicitly says Time-of-Use requires both AC input and **Wi-Fi**. It describes Peak as battery-first, Mid-Peak as grid-only, and Off-Peak as grid supply plus battery charging. The guide does not specify whether internet access or Anker cloud contact is required. Our earlier inactive-tariff tests used malformed schedules, so they cannot distinguish Wi-Fi, clock or cloud requirements; see the [encoding audit](c2000-tou-encoding-audit.md).

The [public A1783 command map](https://github.com/thomluther/anker-solix-api/blob/main/src/anker_solix_api/mqttmap.py) identifies `D9[1]` as active tariff (0 none, 1 Peak, 2 Mid-Peak, 3 Off-Peak), `D9[2]` as usage mode (0 Standard, 1 Time-of-Use), and `D9[3]` as backup reserve percent. The saved C2000 `D9` block was `04 00 00 0a 5a 01...` (type byte, then no tariff / Standard / 10% reserve / 90% upper / 1% lower). A fresh **read-only** BLE snapshot repeated those five values while AC input and output both measured 357 W and AC output remained enabled; the full snapshot stays in `.solix-private/c2000-mode-audit-20260929.json`. The Python decoder exposes the three read-only mode metrics.

The public map **lists** a usage-mode request under MQTT command group `0090`, field `A2` (0 Standard, 1 Time-of-Use), and a backup-reserve setting at `A5`. Its separate multi-field Time-of-Use schedule request is marked as normally cloud-sent; the public API leaves schedule control disabled pending support. The analogous BLE command group is **`4090` on this C2000**: a guarded idempotent Standard write received `4890` response `00a10131`, and telemetry stayed Standard with AC output on. A subsequent `4090/A5` write changed backup reserve **10→85→10%**, with `4890` success responses and matching `D9[3]` telemetry.

With the temporary 85% reserve confirmed, a guarded `4090/A2=1` write changed `D9[2]` from Standard to Time-of-Use. For 20 seconds, `active_tariff` stayed `none`, battery status stayed `idle`, and AC input matched AC output at roughly 360–373 W. The retained baseline and final `D9` blocks had zero schedule slots and all-zero schedule bytes. Selecting Time-of-Use alone did **not** select a Peak period or discharge the battery in this trial. `4090/A2=0` then restored Standard mode, followed by reserve **85→10%**; both returned success and were confirmed in telemetry. A new BLE connection again showed Standard / no tariff / 10% reserve, 90% battery, AC input and output both 361 W, and AC output enabled. No AC-output switch packet was sent. Private logs are `.solix-private/c2000-standard-mode-probe-results-20260929.jsonl`, `.solix-private/c2000-backup-reserve-results-20260929.jsonl`, `.solix-private/c2000-tou-guarded-results-20260929.jsonl`, and `.solix-private/c2000-tou-postcheck-20260929.json`. A 45-second HA-node scan still found only the C2000, so the noncritical C1000 was unavailable for a mode or schedule trial.

On 2026-09-29, full `4090` requests with `A2`, `A3=0`, `A4=0`, `A6=4` and typed `A7` changed C2000 `D9`, while an `A7`-only request did not. **The later encoding audit corrects our interpretation:** `D9[6]` is the slot count and triplets start at `D9[7]`. Our supposed one-slot Peak payload `A7=0401010018` duplicated a count, producing first triplet `01 01 00` (Peak, start 1, end 0) with four slots declared by `A6`. Off-Peak and minute-format trials had the same layout error. Changing `A6` to 3 changed the declared count; it was not an unknown parameter. A clear request that retained `A6=4` and wrote `A7=0400` only zeroed the first tariff byte; it did not establish zero slots. See [the raw evidence and corrected candidate](c2000-tou-encoding-audit.md).

During the malformed Peak trial, **40 samples over 78 seconds** showed tariff `none`, battery `idle`, AC input/output roughly 376–390 W, and AC output enabled. These observations remain valid, but they do not demonstrate a failed valid all-day plan or a missing activation condition. Standard mode and reserve 10% were restored. The saved final read-only `D9` is 38 bytes and reports **count 4**, first triplet `00 01 00`; our earlier zero-count assertion was incorrect. Caps remained 90%/1%, charging power 1800 W, and AC output on. Private captures remain unchanged: `.solix-private/c2000-schedule-standard-results-20260929.jsonl`, `c2000-schedule-typed-results-20260929.jsonl`, `c2000-full-schedule-results-20260929.jsonl`, `c2000-peak-schedule-results-20260929.jsonl`, `c2000-peak-a6-three-results-20260929.jsonl`, `c2000-peak-minute-results-20260929.jsonl`, and `c2000-schedule-final-readonly-20260929.json`. The decoder now reads `tou_schedule_slot_count` from offset 6 and removes the incorrect `tou_schedule_parameter` metric. The noncritical C1000 was unavailable for these trials.

The Python Prime handshake previously sent `UTC0` and a zero offset in `4022`, including on the UTC HA node. The upstream [SolixBLE negotiation](https://github.com/flip-dots/SolixBLE/blob/main/SolixBLE/device.py) sends a local POSIX timezone and seconds west of UTC. The library now accepts IANA `timezone_name`; `Europe/Vienna` encoded as `CET-1CEST,M3.5.0,M10.5.0/3` and −7200 seconds during this test. A guarded trial still showed tariff `none`, idle and input approximately equal to output in **40 samples**, with AC output on. It reused the malformed schedule, so the result cannot establish behavior for a valid plan with that timezone. Standard mode and reserve 10% were restored; the historical zero-slot interpretation is superseded by the encoding audit. The private log is `.solix-private/c2000-vienna-peak-results-20260929.jsonl`.

The HA node then hosted a WPA2 AP in a network namespace with only `192.168.77.0/24` and **no default route**. An experimental C2000 `4024` using the C1000 field layout returned `4824=00`; association and DHCP were recorded. A further malformed Peak trial left tariff `none` and battery idle in 40 samples, with AC output on; Standard mode and reserve 10% were restored. Its failure cannot isolate Wi-Fi activation requirements. A passive 45-second capture contained only ARP, establishing that association alone did not start the API exchange in this attempt. Private logs are `.solix-private/c2000-vienna-wifi-peak-results-20260929.jsonl`, `c2000-wifi-dnsmasq-20260929.log`, `c2000-wifi-hostapd-20260929.log`, and `c2000-wifi-passive-20260929.pcap`.

An experimental C2000 `4025` packet pointed its API base URL at a plain HTTP recorder inside the same isolated AP and used model code `A1783` and `Europe/Vienna`. No `4825` reply arrived before the BLE timeout, but the AP captured four C2000 POST requests: `/equipment/devicemanage/get_mqtt_info`, `/equipment/devicerelation/bind_device`, `/equipment/devicerelation/check_relate_bind_device`, and `/equipment/help/dst`. The device queried `time.nist.gov` and sent an NTP request to the AP. A local NTP responder served a request after re-association; a further guarded trial using the same malformed Peak encoding still left the tariff `none` and battery idle in 40 samples. The recorder returned only generic `code=0` / empty `data` replies, and **no MQTT connection was observed**. This verifies that the C2000 can be provisioned to a no-internet AP through BLE and can contact a local API endpoint; it does not establish a working binding, MQTT session, or charging control. The AP and local services were stopped afterward. The station may retain the isolated SSID while that AP is off. Full request headers/bodies, packet capture, NTP events, and the final trial log stay only in `.solix-private/c2000-api-requests-20260929.jsonl`, `.solix-private/c2000-api-config-20260929.pcap`, `.solix-private/c2000-ntp-events-20260929.jsonl`, and `.solix-private/c2000-vienna-wifi-ntp-peak-results-20260929.jsonl`. No C2000 AC-output command was sent.

One further local-only test returned a complete `get_mqtt_info` structure with newly generated CA, client certificate/key, and a broker hostname resolved to the AP; the local API also returned affirmative binding fields and the correct DST timezone. A TLS MQTT listener was ready on port 8883. Across **two** setup attempts, the C2000 requested MQTT info, binding, binding check, and DST, then requested `/equipment/devicerelation/unbind_device` about **26 seconds** later. A focused 109-packet capture contained no broker DNS lookup or port-8883 traffic, and the MQTT listener logged no connection. These results show the locally fabricated responses did not complete the device's binding/setup state; they do not prove which field or validation failed. The request log and capture are retained only in `.solix-private/c2000-mqtt-api-requests-20260929.jsonl` and `.solix-private/c2000-mqtt-lab-20260929.pcap`. Disposable certificate material stays in the ignored private folder. The AP, temporary HA-node credentials, and test services were removed. C2000 telemetry still showed Standard mode and AC output enabled after each provisioning probe.

The initial C2000 setup used its generated BLE client ID as the `4024`/`4025` account field. A final isolated test instead used the **different 40-character account ID** recovered earlier from the user's phone logs, without printing or uploading it. The C2000 accepted `4024=00`, and its local `bind_device` request carried the real app account ID. The same four setup paths were followed by `unbind_device`; a 98-packet capture again had no broker DNS lookup or port-8883 connection. The account-ID mismatch alone therefore does not explain the failed local binding. The final request and NTP logs and capture remain in `.solix-private/c2000-app-id-api-requests-20260929.jsonl`, `.solix-private/c2000-app-id-ntp-events-20260929.jsonl`, and `.solix-private/c2000-app-id-mqtt-20260929.pcap`. The AP and all temporary HA-node copies of the account ID, pairing config, and generated broker credentials were removed. The C2000 may retain the isolated SSID, which is currently offline; its AC output stayed enabled and Standard mode remained selected.

A final **read-only BLE reconnection after AP shutdown** confirmed 90% battery, idle, Standard mode, no active tariff, 10% reserve, zero schedule slots, 90%/1% charge limits, 1800 W AC charging-power limit, AC input present, and AC output enabled at about 370 W. The temporary audit config on the HA node was then removed.

The other physical route is to remove the C2000's **AC input** while leaving AC output on, causing UPS battery operation. Its own mains-loss transfer has not been exercised on this server-backed C2000, so this is not yet a verified interruption-free automation. The tested 90% charge cap and 500 W charging-power limit did not select battery discharge while mains remained connected.

When asked to enter Time-of-Use on the tested C1000 through the Anker app, the user reported that the app requires connecting the station to Wi-Fi first. No Time-of-Use mode or schedule write was produced by the earlier Bluetooth-only app captures. This establishes an app setup requirement, not proof that every underlying schedule command is cloud-only. A C1000 local schedule implementation still needs a captured mode/schedule change and a safe C1000 verification; the C2000 storage result does not establish compatibility.

An offline inspection of a previously saved Anker Android app binary found Flutter symbols `changeToTouMode`, `setTouSystemParams`, and `action_set_tou_system_params`. The string search alone does not identify the target device family, transport, payload, or whether Wi-Fi/cloud setup is necessary. The later guarded C2000 `4090` tests established local mode, reserve, and schedule storage writes through BLE, but did not select an active tariff or cause battery discharge while mains remained connected.

The user prefers to leave both stations off Wi-Fi. We tested two Bluetooth-only workarounds on the noncritical C1000. First, at 100% battery and with its AC input and output connected, the charge upper limit was set **100% → 80% → 100%**. For ten seconds at the 80% cap, the station remained `idle`, drew **86–87 W from AC**, and supplied **86–87 W on AC output**. The lower limit remained 1%, AC output remained on, and the upper cap was confirmed restored to 100%. The private log is `.solix-private/c1000-charge-cap-bypass-*.jsonl`.

Second, with a noncritical **771–772 W AC load** attached, the C1000 AC charging power limit was set **1200 → 300 → 1200 W**. For ten seconds at the 300 W limit, AC input and output both remained **771–772 W**, and battery status stayed `idle` at 100% SoC. The station confirmed restoration to 1200 W with AC output still on. The private log is `.solix-private/c1000-charge-power-bypass-*.jsonl`. Together these observations show that neither a low maximum charge level nor a charging-power limit below AC usage forced battery discharge on the tested full C1000 while mains was present. They are not verified substitutes for Time-of-Use mode. Testing while the battery is below full could further characterize how much *additional* grid power the charger draws, but is unnecessary to explain the observed AC bypass at full charge.

## Isolated Wi-Fi provisioning and cloud endpoint investigation

On the C1000 only, the user entered an isolated 2.4 GHz AP's credentials in the Anker app twice. The AP was in a dedicated network namespace on the Home Assistant node, with only a Wi-Fi interface, loopback, and the AP subnet. It had no default route. The station associated, completed WPA2, obtained a DHCP lease, and asked DNS for `time.nist.gov` and `ankerpower-api-eu.anker.com`. With no DNS answer for either name, it made no HTTPS request and the app reported that it could not connect to the server. The C2000 was untouched. Full HCI, packet, DNS, and AP logs remain only in the ignored private directory.

The app's C1000 Prime writes contain two provisioning commands. The following
table records the initial plaintext reconstruction, not a verified wire order:

| Command | Plaintext TLVs | Observed response |
| --- | --- | --- |
| `4024` | `A1` Unix timestamp (4 bytes), `A2` 40-character account ID, `A3` SSID, `A4` `00`, `A5` `00`, `A6` Wi-Fi passphrase | `4824` byte `00`; the station then associated with the AP |
| `4025` | `A1` timestamp, `A2` same account ID, `A3` HTTPS API base URL, `A4` POSIX timezone, `C3` two-byte value, `A6` service name, `A7` model code, `A8` IANA timezone | Immediate `4825` byte `26`; exact meaning unconfirmed |

The app's `4025` API URL matched the hostname the station queried roughly one second after the `4825` reply. These fields were recovered from the local HCI trace by comparing encrypted traffic with known telemetry in the same Prime session. The account ID, SSID, passphrase, and complete decrypted requests remain private.

**Correction:** the firmware byte parser requires ascending tags. Emit
`A1 A2 A3 A4 A6 A7 A8 C3`; placing `C3` before `A6` silently loses the service,
model, and IANA timezone. Correcting this in our packet established C2000 MQTT.
The reconstructed opaque `C3` field and its placement are not confirmed official
app requirements. Earlier failed probes below used the old order.

The Python library then sent `4024` and `4025` directly from the laptop to the C1000. The station replied `00` and `26`, joined the same isolated AP, and obtained DHCP; AC output remained on. In the first direct test, sending `4024` alone was enough for association and DHCP. Repeating the full sequence with the **generated local BLE client ID** in `A2` also worked, so the original Anker account ID was unnecessary for local AP provisioning on this unit. The `solix-gen2 wifi-setup` CLI exercised that workflow with a password file; `wifi-join` exposes the independently observed `4024` only. This was tested with one WPA2 AP and firmware 1.1.4.9; other AP security types and firmware remain untested. The station's API binding remains incomplete.

When DNS mapped the official API hostname to the local server, the station opened TLS on port 443 and rejected its self-signed certificate with alert `unknown_ca` (48). No HTTP request was delivered over that connection. The library then supplied an `http://` URL for the isolated AP using the same `4025` field. The station accepted it and sent these requests to the local HTTP server:

| Request path | Request body fields observed | Count in one setup |
| --- | --- | --- |
| `/equipment/devicemanage/get_mqtt_info` | `device_sn`, `check_code` | Three |
| `/equipment/devicerelation/bind_device` | `device_sn`, `check_code`, `account`, `name`, `time_zone`, `wifi_ssid` | Three |
| `/equipment/devicerelation/check_relate_bind_device` | `device_sn`, `check_code`, `account`, `bt_ble_mac` | Three |
| `/equipment/help/dst` | Includes the local account ID | Observed on later retry |

The station also sent `user-id`, `gtoken`, `device-sn`, `check-code`, and `timestamp` HTTP headers. Their values and all full request bodies are stored only in `.solix-private/isolated-ap/`. A basic `{"code":0,"data":{}}` reply let the station proceed through these calls but did not complete setup. A later probe returned plausible MQTT connection fields pointing to a local broker; the station made no MQTT connection. During one AP association, short connection probes found no listener on ports 22, 80, 443, 1883, 8883, 8080, 8081, 9000, or 10000; other ports were not checked.

With the user's explicit permission, the laptop sent one captured **read-only** `get_mqtt_info` request to Anker's official API while the C1000 remained offline. Its successful response has `code`, `msg`, `trace_id`, and `data`; `data` contains `device_sn`, `thing_name`, `certificate_id`, `certificate_pem`, `private_key`, `public_key`, `endpoint_addr`, `aws_root_ca1_pem`, `origin`, and `country_code`. The complete response remains only in the ignored private directory. Despite their field names, `certificate_pem` and `private_key` were base64 text containing binary data rather than literal PEM text; the station's handling of that encoding is not yet known.

A second local API probe returned the real response shape with a local broker address and lab certificates, and added an explicit HTTP `Content-Length`. The C1000 made one request to each binding endpoint, then `/equipment/help/dst`, rather than three retries per endpoint; either the response fields or HTTP framing may explain the change. A local MQTT broker with a lab TLS certificate passed an independent TLS handshake, but the station neither queried its hostname nor opened a connection. We then repeated the test from the Home Assistant node's Bluetooth adapter, keeping the station's original encoded credential fields and changing only the broker endpoint and CA in the local API reply. The Home Assistant node could scan both Gen 2 stations, but only the C1000 was provisioned. We also tested the exact broker hostname from Anker's response with isolated DNS resolving it to the lab broker, retried with the app's real account ID in `4024`/`4025`, and tested a local binding reply containing affirmative status fields. Each variant reached the four local API paths once, but none caused a broker DNS query or MQTT connection.

The first DNS setup erroneously returned `REFUSED` for the station's `time.nist.gov` lookup. We corrected the isolated AP to answer that name locally and provided a working local NTP server. The C1000 sent an NTP request after its API calls; a second setup after that still made no broker DNS query or MQTT connection. The HTTP header named `timestamp` decodes as a date in the 1970s in these captures and varies even among adjacent calls, so its encoding is not established and it should not be treated as a Unix clock reading. The `4025` BLE reply timed out in these tests; the earlier `26` byte may describe a different failure stage, but its exact meaning is still unknown. With the user's permission, read-only `check_relate_bind_device` and one `bind_device` request were sent from the laptop to Anker's official API; both returned error code `10000` and no `data`, so neither revealed a successful binding response. The API emulator is a private probe separate from the read-only monitoring server. The AP namespace has no default route. Wi-Fi control and Time-of-Use over MQTT remain unverified; Bluetooth monitoring and verified C1000 controls work independently of Wi-Fi.

With separate explicit user approval, a narrowly scoped relay forwarded one fresh C1000 request per endpoint from the isolated AP through the Home Assistant node to Anker's official API. It accepted only the C1000 serial and cached each upstream response, so station retries caused no extra official requests. The live `get_mqtt_info` returned `code=0` and the credential fields above. The live `bind_device` and `check_relate_bind_device` each returned `code=10000` with no `data`, as the laptop replays had. The station returned BLE `4825=25`; it made no broker DNS query or MQTT TLS connection. Its AC output remained on. This is a correlation, not a confirmed meaning for BLE code `25`. The relay, AP, NTP, and MQTT probes were stopped after the private captures were saved, and copied credentials were removed from the HA node. Local MQTT monitoring and control remain unverified.

An offline comparison of the reconstructed app `4025` writes with our provisioning packet found that `C3`, `A6`, `A7`, and `A8` match exactly. The reconstructed `A4` timezone was `CET-1CEST,M3.S.V,M10.5.0/3`, whereas our tests used `CET-1CEST,M3.5.0,M10.5.0/3`. `M3.S.V` is an unusual rule; because the app plaintext was recovered using a reused GCM nonce and inferred keystream bytes, those two characters may be a reconstruction error. We therefore do not treat it as an established app requirement.

We repeated local C1000 provisioning with the reconstructed `M3.S.V` string and the same isolated AP and affirmative local API emulator. The station replied `4824=00`, then requested `get_mqtt_info`, `bind_device`, `check_relate_bind_device`, and `/help/dst` in the same order. Comparing the old and new request bodies showed only `bind_device.time_zone` changed. `4825` timed out, as in the prior local emulator test, and there was no broker DNS lookup. The C1000 AC output stayed on. This rules out the timezone difference as the cause of the local emulator's missing MQTT connection; it does not establish why the official binding endpoints returned `code=10000`. The AP and probe were stopped and the copied credentials removed from the HA node after the capture, which remains in the ignored private folder. Neither `4825=25` nor cloud `code=10000` has a confirmed meaning from these observations.

As of this investigation, [Anker's official local Modbus integration](https://github.com/anker-charging/ha-anker-solix-official#supported-devices) lists several other SOLIX products but neither C1000 Gen 2 nor C2000 Gen 2. Its Modbus transport is therefore not a verified substitute for this BLE work on these units.

We joined the C1000 to the isolated AP once more and tested direct TCP connects to ports 502 (Modbus), 80, 443, 1883, 8883, and 8080. The station obtained DHCP and kept AC output on, but port 502 actively returned `ECONNREFUSED`; none of the checked ports accepted a connection. This only covers those ports while this unit was in its unsuccessful network setup state. A separate local-only run supplied the full MQTT credential response shape, affirmative local binding replies, a local NTP server, and a TLS broker for over seven minutes. The C1000 made the same four API requests and two NTP requests, then disconnected from Wi-Fi. It did not reconnect or query the broker name during that window, and the broker saw no TLS or MQTT connection. The AP and probes were stopped afterward. The complete HA-node lab logs were retained in ignored private archives, then copied credentials and request logs were removed from the node.

The recovered 40-character app account ID also appears in an older Android bugreport text log from before the Wi-Fi capture. This independent occurrence makes a transcription error in the recovered ID less likely, though it does not prove that Anker's binding service accepted it. Cloud `code=10000` is a generic request failure in available evidence: the [same code and message were reported for an unrelated dynamic-price endpoint](https://github.com/thomluther/ha-anker-solix/issues/427). The binding failure's specific cause remains unknown.

The saved C1000 `/equipment/help/dst` request contains `device_sn`, `check_code`, `account`, and `city`. Earlier local probes answered it with an empty `data` object. With the user's approval, one captured request to Anker's official API returned HTTP 200 and API `code=0`, with `data.timezone` as a 26-character string. The complete response is private. Its timezone exactly matches the POSIX timezone already sent in our `4025` provisioning packet. The saved `bind_device`, `check_relate_bind_device`, and DST requests use the same account ID, device check code, `Gtoken`, and device serial; their per-request HTTP `check-code` and `timestamp` headers differ. DST succeeding with these credentials makes a general credential failure less likely, but does not explain why the two binding endpoints returned `code=10000`. The exact timezone match also weakens, without ruling out, the idea that the empty DST reply alone prevented MQTT.

We prepared a local replay with the official DST response and the prior MQTT and binding emulators, but the saved C1000 stopped advertising to the HA node and one direct BLE connection timed out. The nearby station advertising as `Anker SOLIX C1000` did not match its saved address and was not touched. The isolated AP was stopped, its Wi-Fi adapter restored, and copied credentials removed; the no-advertisement attempt's logs were archived privately. The DST response remains an untested variable in station-to-MQTT setup.

On 2026-09-29, a separate read-only C2000 connection from the same HA Bluetooth adapter succeeded and reported 90% battery, AC input present, AC output enabled, and 358 W AC output. This confirms that the adapter and Prime telemetry path still worked at that time; it does not explain the C1000's missing advertisement. The temporary C2000 pairing config was removed from the HA node after the check. No C2000 output-control command was sent.

### Device MQTT credential envelope

On 2026-09-29, offline analysis identified the encoding of `certificate_pem` and `private_key` in the saved C1000 `/equipment/devicemanage/get_mqtt_info` response. Unlike the account-facing `get_user_mqtt_info` endpoint, these device fields contain Base64-encoded **AES-256-CBC ciphertext**, with PKCS#7 padding:

```python
serial = device_serial.encode("ascii")  # 17 bytes on the observed units
key = (serial * 2)[:32]
iv = serial[:16]
```

Both fields decrypted successfully: the 1184-byte certificate ciphertext yielded a 1176-byte PEM certificate, and the 1680-byte key ciphertext yielded a 1675-byte PKCS#1 RSA private key. Both parsed with `cryptography`; their public keys matched, and re-encryption reproduced each original ciphertext exactly. `aws_root_ca1_pem` was already plain PEM. No account credential, request `check_code`, or cloud request was needed for this offline decoding. The serial-derived envelope should not be treated as protection from someone who knows the serial.

The implementation is in `solix_gen2.mqtt_credentials`, with a synthetic OpenSSL interoperability vector and malformed-input tests. Captured serials, certificates, private keys, and the exploratory scripts remain only in `.solix-private/`.

The earlier C2000 emulator supplied plain PEM in these two fields, so those trials did not isolate the binding response as the cause of failure. A corrected response encrypted locally generated credentials for the C2000 serial, used a PKCS#1 key and numeric certificate ID, and retained the same local binding/DST replies. The C2000 again requested the four setup endpoints and then `unbind_device` about 26 seconds later, without reaching the TLS broker. Its AC output remained enabled and Standard mode stayed selected. Credential encoding alone therefore did not complete setup; the remaining binding/setup requirements are unresolved.

The 26-second unbind timing was subsequently traced to the test session ending. A retry sending only `4025` after that unbind generated no HTTP requests. An uninterrupted `4024`→`4025` session then waited 100 seconds for `4825` and another 10 seconds before disconnecting. It received no `4825` and made no broker connection; `unbind_device` arrived approximately **112 seconds** after the initial API request, **2.1 seconds after BLE disconnect**. This strongly supports disconnect-triggered setup cleanup rather than a fixed 26-second binding timeout. Keep BLE connected while investigating setup completion. The full first-run archive is retained in `.solix-private/isolated-ap/credential-envelope-20260929/`.

### Recovered radio firmware and binding fields

The retained C1000 phone capture also contained the firmware-update transfer. Offline reconstruction of `402f` recovered **6740 unique 220-byte chunks**, with 120 identical retransmissions. The final transport chunk had 48 zero-padding bytes after the image's `ff` padding. Accounting for that padding produced an ESP image whose embedded **SHA-256 matches**. Its application descriptor reports `v0.3.3.0`; this is the radio application version, distinct from the station's user-reported 1.1.4.9 main firmware. The reconstructed image, session material, and annotated RISC-V disassembly remain private in `.solix-private/firmware-analysis/`. No firmware was flashed.

The recovered `device_check_bind_status_vSaaS` parser requires `data.relate_state` and `data.bind_state` and reads their numeric values. The emulator's earlier `is_bind` / `bind_status` guesses did not supply these fields. A C2000 trial added both exact fields with value `1`, retained the corrected encrypted credentials, and kept BLE open through the same 100-second observation. It still made no broker DNS query or port-8883 connection, and `4825` timed out. The fields are established for the recovered C1000 firmware; their sufficiency and the remaining C2000 setup conditions are not established. Evidence is retained in `.solix-private/isolated-ap/binding-state-20260929/`.

The radio firmware also contains a **Modbus TCP server** and an explicit enable/disable handler, with default port 502 and an optional port value. Its internal dispatch entry is `0x0066`; the handler expects `A1` with a one-byte enable value and optional `A2` with a two-byte port. Further offline tracing located the dispatcher under **function ID `0x10`**, separate from ordinary app commands (`0x0f`). **Do not send an ordinary app `4066` by analogy.** The bridge forwards requests to controller opcode `0067`, which is absent from the recovered C1000 main firmware's request table. Neither the alternate BLE route nor functional Modbus has been verified live; no Modbus-enable write was sent. See the [firmware findings](firmware-findings.md#modbus-radio-bridge-found-controller-support-missing-from-dispatch-table).

After both corrected-response trials, the isolated AP and services were stopped, complete logs were archived locally, and temporary credentials were removed from the HA node. The final read-only C2000 audit confirmed 90% battery, idle, Standard mode, no active tariff, 10% reserve, zero schedule slots, 90%/1% charge limits, 1800 W charging limit, 30-second display timeout, and AC output on, with input/output both 416 W. Neither trial changed charging settings or sent an output-control command. No new request to Anker's official API was made.

### Main-controller firmware analysis

Offline reconstruction also recovered the C1000 1.1.4.9 controller update package. The main controller passes its CRC-16/MODBUS check, the BMS passes CRC-16/XMODEM, and the display passes its byte-sum check after removing a repeating XOR mask. The controller contains explicit Time-of-Use readiness checks, a C1000 schedule layout subsequently reconciled with C2000 by the encoding audit, additional display/alert settings, disaster-preparation and timer-plan handlers, and energy accounting. In the normal radio path, the controller's Wi-Fi-ready notification requires both AP and MQTT connectivity; this is a stronger explanation for the earlier inactive tariffs than association alone. The recovered code is C1000 firmware, so applying that explanation to C2000 remains an inference. Full versions, addresses, limitations, and next checks are in [Gen 2 firmware findings](firmware-findings.md). This analysis used retained local data and sent no station or cloud requests.

### MQTT parser replay and HTTP framing comparison

Further offline execution of the recovered C1000 radio code accepted both the
retained official response and the generated lab response, including credential
storage validation and MQTT startup checks with synthetic identities. HTTP/1.0
was accepted. The plain HTTP receive loop does have a reproduced short-read
failure when `EAGAIN` follows a partial block; one-byte HTTP chunks avoid it in
the replay. A guarded C2000 hardware comparison served that framing and captured
the complete response, but still produced no broker DNS query or TCP connection.
Charge caps 90%/1%, charging power 1800 W, Standard mode, zero schedule slots, and
AC output on were unchanged. The AP was stopped and temporary HA credentials
removed after archiving. Detailed parser addresses, identity requirements,
29 replay cases, and limitations are in the
[native local MQTT investigation](local-mqtt-investigation.md).

### TLS credential replay and radio diagnostics

The recovered C1000 radio's actual mbedTLS parsers accepted both retained
credential fixtures. A separate 24-case replay covers direct parsing, flash
getter readback with NUL-inclusive lengths, and malformed-input controls.
The connector parses credentials before DNS/TCP, so missing broker traffic
alone cannot locate the failure.

The read-only radio query `0f/4020` → `4820` exposes HTTP, Wi-Fi, BLE disconnect,
MQTT, and reset codes. Its handler passed four offline checks; nine C2000 reads
succeeded, including four during another guarded isolated Wi-Fi setup. HTTP,
Wi-Fi, and MQTT errors remained zero despite no MQTT connection. Reset codes
became `255` after the first query. The Python library and CLI now expose this
query; zero codes do not prove connectivity or report battery/inverter health.
AC output, charge limits/power, and scheduling settings stayed unchanged.
Private captures were archived and the temporary AP stopped. See the
[TLS and diagnostic findings](local-mqtt-investigation.md) for addresses,
reply fields, timing, and remaining uncertainty.

### Native MQTT established after provisioning correction

The actual C1000 TLV parser `0x4204f9a6` explained the missing service ID:
our reconstructed `C3` placement prevented parsing the lower-numbered fields
after it. Three byte-parser replays reproduced this and verified ascending
order. A guarded C2000 trial with only that ordering changed stored
`anker_power`, completed TLS 1.2, subscribed to
`cmd/anker_power/A1783/{serial}/req`, and published live station telemetry.
Fresh BLE readback confirmed saved settings and unchanged power configuration.

Two subsequent AP restarts required no Bluetooth or provisioning. The latter
required a valid client certificate signed by the lab CA and succeeded.
MQTT `0100` returned `0900` status plus full telemetry; `0057` requested a
60-second stream, acknowledged with `0857`, followed by `0421` roughly every
three seconds. These are ordinary SOLIX frames carried as Base64 in a JSON
string inside the outer JSON `payload`, using TLS transport. The Python
`decode_mqtt_telemetry` helper handles telemetry and successful status replies.

The final trial decoded 15 readings, including its status reply, with mains
present, AC output on, Standard mode, no tariff, zero slots, caps 90%/1%, and
charging power 1800 W. It sent no charging/output/schedule write. The AP had
no internet route; all captures remain private. The temporary services were
stopped and HA credentials removed after verifying the archive hash.
See [native MQTT findings](local-mqtt-investigation.md) for envelope fields,
credential constraints, historical failures, and remaining work.

### Native MQTT control follow-up

On the same C2000/main 2.1.6.4, native radio queries `0027`/`0028` confirmed AP
and server connectivity. Native `0101` with only the `A4` charging-power field
changed **1800→1700→1800 W**, with successful `0901` acknowledgments and fresh
`0900` telemetry after each step. The battery remained idle at its 90% cap;
this verifies the setting, not charging current. The Python `NativeMqttCommands`
helper now builds status, stream, and charging-power requests.

A separate native `0090` trial verified reserve **10→85%** and mode
**Standard→Time-of-Use→Standard**, then sent the old malformed Peak encoding.
Twelve samples spanning 25.8 seconds showed no active tariff and idle battery,
with mains present and AC output enabled. Restore commands returned Standard,
reserve 10% and genuinely zero slots (`A6=0`); a fresh BLE session confirmed
these values, caps 90%/1% and charging limit 1800 W after AP shutdown. Bluetooth
discovery had failed during an earlier MQTT session, then succeeded after AP
shutdown. No output switch was sent. Private captures retain interrupted prechecks.

The [later encoding audit](c2000-tou-encoding-audit.md) identifies the duplicated
count as a concrete explanation for the invalid intervals. These failed trials
cannot isolate additional binding, readiness or clock requirements. See the
[detailed historical record](local-mqtt-investigation.md#time-of-use-with-mqtt-connected).

### BLE-to-MQTT bridge

As a usable local MQTT path, the Python package now includes an optional **BLE-to-MQTT bridge**. It publishes sanitized C1000/C2000 telemetry and availability to a local broker. It subscribes to four verified C1000 setting topics, and only upper charge cap, charging power, and screen timeout for C2000. It has no AC/DC output command. On the HA node, a disposable loopback broker received live C1000 telemetry; a 100%/1% charge-limit write through MQTT was confirmed by the station, and the bridge last will marked it offline when stopped. This does not mean the power station itself connected to MQTT. See the [Python MQTT bridge instructions](../python/README.md#local-mqtt-bridge).

On 2026-09-29, the same HA node ran a C2000-only bridge against another disposable MQTT listener bound to `127.0.0.1`. The listener received a live `solix_gen2/c2000/state` publish with availability true, 90% battery, mains present, AC output enabled, 343 W AC output, and the restored 30-second display timeout. The C2000 AC output remained on. The MQTT publication log is retained only in `.solix-private/c2000-mqtt-bridge-results-20260929.jsonl`. A follow-up loopback test subscribed only to the C2000 charging-power and display-timeout topics, sent an idempotent 1800 W charging-power command, and received a confirmed result. That result is retained in `.solix-private/c2000-mqtt-control-results-20260929.jsonl`. A subsequent loopback test sent an idempotent 90% upper-cap command and confirmed upper 90%, lower 1%, and AC output enabled; its log is `.solix-private/c2000-mqtt-cap-results-20260929.jsonl`.

We then verified the complete charging workflow through that loopback MQTT bridge. At 90% battery, idle, AC output on, and 90% upper cap, MQTT commands set charging power **1800→500 W** and upper cap **90→95%**; both returned successful telemetry confirmations. The station reported `charging`, with **872 W AC input** and **318 W AC output**, while AC output stayed on. MQTT restore commands set cap **95→90%** and power **500→1800 W**, again with successful confirmations. A separate direct BLE check found 90% battery, `idle`, mains present, AC output on, input/output both 355 W, and upper/lower limits 90%/1%. The private command/result log is `.solix-private/c2000-mqtt-charging-results-20260929.jsonl`. This establishes MQTT-to-BLE charging control through the bridge. Native C2000 MQTT monitoring was subsequently established as described above; those native trials did not send charging commands.

### Corrected native Peak activation

After the encoding audit, a bounded native MQTT trial on main **2.1.6.4** first
stored the corrected slot in Standard (D9 length 29/count 1), cleared it, then
raised reserve **10→85%** and selected Time-of-Use with Peak **00:00–24:00**.
Peak became active; three discharge samples had **0 W AC input** and
**851–897 W AC output**, with mains present and AC output enabled. SOC changed
91→90%. Standard/count 0 were restored before reserve 10%. A separate MQTT
connection confirmed settings but still reported discharge; a later independent
BLE check confirmed idle and equal grid input/output. No extra recovery or
AC-output write was sent. The [complete trial record](c2000-corrected-peak-trial.md)
documents the observation gap and remaining automation questions.

## Packaged tariff control and grid return

The [subsequent public CLI trial](c2000-offpeak-grid-return.md) on the same
C2000 main **2.1.6.4** verified `ap-service-set-reserve`, corrected `ap-service-set-tou`,
and `ap-service-grid`. Peak supplied **900–982 W** from the battery with zero AC input.
Native tariff **3** (`off_peak`, A7=`04 03 00 18`) restored grid input after
approximately **4.84 seconds** and idle after **6.89 seconds** in this trial.
The command confirmed three fresh grid samples, cleared Standard/count 0,
then confirmed three more. Original reserve 10%, caps 90/1%, power 1800 W,
fast charge off and AC output on were checked through independent MQTT/BLE.
These are observed timings, not transfer guarantees; no waveform was measured.

The guarded control socket, optional authenticated HTTP gateway and terminal
dashboard expose reserve/plan/grid operations. Activated plans persist until
changed. Multi-slot/timed operation and reserve-floor behavior remain untested.
The [101-case firmware follow-up](tariff-energy-followup.md) also recovered
partial binary energy reports through a logging API; passive decoding is
available, but units/reset behavior and C2000 equivalence remain unverified.

## Local data and privacy

The subsequent [native MQTT reconnect and tariff follow-up](c2000-mqtt-reconnect-and-tariff.md)
verified controller `0089`, two AP reconnections, and longer unsuccessful Peak
trials with reserve headroom using the old malformed schedule encoding.
The [corrected trial](c2000-corrected-peak-trial.md) subsequently verified active
Peak and discharge with mains present. The [Python isolated AP/MQTT tool](isolated-ap-mqtt.md)
now packages the local bootstrap, monitoring and charging-power workflow.
Its live C2000 test confirmed native **1800→1700→1800 W** settings while AC
output stayed on; an acknowledgement alone is not used as confirmation.

The workspace's `.solix-private/` directory contains local observations, C1000 post-update handshake/Prime/control records, retained phone bugreports and extracted HCI traces, a private copy of the installed app binary, the isolated AP's packet/DNS/HTTP logs and experiment scripts/results, and a working private CLI config with both paired IDs. `.solix-local-ids.json` also holds the original C2000 ID. Both paths are ignored by Git, and the private folder and files are owner-readable only. These captures, credentials, and IDs are for local research and deployment, not GitHub. The earlier phone bugreport files deleted before the request to retain logs cannot be recovered retroactively. After the offline Wi-Fi tests, the AP and its DNS, HTTP, TLS, and MQTT probes were stopped; its Wi-Fi adapter was returned to the Home Assistant node's normal namespace. The phone's Bluetooth was restored to its original on state.

The [Python README](../python/README.md) documents installation, CLI pairing, HTTP endpoints, a Home Assistant REST example, and the private config file needed for a server on the HA node.

## Verification status

The Python protocol tests passed; the public `SolixMonitor` setting methods changed and restored charge limits, AC charging power, display timeout, and fast charge on the live C1000. The CLI confirmed idempotent writes of the two new settings. C2000 upper-cap control was verified through the Python client, CLI, and local MQTT bridge; a 500 W charging-power setting and a temporary 95% cap caused the station to report charging while AC output remained on. The same charging/restore sequence was then verified end to end through MQTT. Guarded C2000 `4090` tests confirmed backup-reserve and usage-mode writes, plus accepted schedule bytes. The later audit found those schedule bytes malformed. A subsequent corrected native MQTT trial verified Peak storage, activation and battery discharge with mains present; restoration and delayed grid-return observations are documented separately. The `wifi-setup` CLI independently connected the C1000 to the isolated AP using only its generated local BLE ID and received `4824=00`, `4825=26`; HTTP requests confirmed AP connectivity. The read-only HTTP server was exercised against the live C2000, returning JSON status, an event stream, and Prometheus metrics. The Vue/TypeScript browser app builds successfully with Prime pairing and C1000 setting controls, but its Web Bluetooth UI has not been exercised against a live station in a browser. The tested C1000 ended at upper/lower 100%/1%, AC charging power 1200 W, display timeout 30 seconds, fast charge off, AC output on, and DC output off; the user chose Device Timeout = Never. The C2000 ended idle in Standard mode with no active tariff, 10% reserve, zero schedule slots, upper/lower limits 90%/1%, AC charging power 1800 W, display timeout 30 seconds, and AC output on. The temporary AP is off, so its stored isolated SSID has no current route. No C2000 output-control write was sent.
