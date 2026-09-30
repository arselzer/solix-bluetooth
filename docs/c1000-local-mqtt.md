# C1000 Gen 2: local identity, MQTT and tariffs

## Device and baseline

Tests on **2026-09-30** used **A1763 / C1000 Gen 2**, main **1.1.4.9**,
radio application **0.3.3.0**, from the HA node's Bluetooth and dedicated
isolated Wi-Fi adapter. The C2000's power settings were untouched.

The C1000 started at 100% battery, idle, with mains present, AC output on,
DC output off and about 48 W AC load. Settings were Standard, tariff `none`,
zero slots, reserve 10%, charge bounds 100%/1%, charging power 1200 W,
fast charge off and device timeout Never. Completed trials recorded a baseline
and restoration checks. Output state is telemetry evidence; inverter waveform
continuity was not measured.

## BLE storage works; activation needs more

Corrected `4090` requests stored an all-day Peak slot, changed reserve
10%→85%, and selected Time-of-Use with Peak, then Mid-Peak. Both modes were
observed for 30 seconds. Mode, slot triplets and reserve read back correctly,
but **active tariff stayed `none`**, battery stayed idle and grid supplied
the load. Off-Peak, Standard/count 0 and reserve 10% were restored;
three final samples confirmed the baseline.

The recovered controller's network-readiness requirement is consistent with
this observation. A stored plan or mode label alone does not establish
battery discharge. See [firmware gates](firmware-findings.md).

The decoder now exposes C1000 D9 tariff/mode/reserve/count fields. Its F9
version block has a type byte followed by seven reversed four-byte slots;
the observed main and radio values are exposed as `software_version` and
`software_version_module`. This differs from the C2000 layout.

## Local MQTT bootstrap

The profile used the C1000's **generated local BLE pairing ID**, different
from the retained Anker app account ID, and newly generated local CA,
server certificate, client certificate and key. No Anker request or internet
route was used. The 40-character account field remains part of the protocol;
it is a local identity here, not an omitted authentication field.

| Credential HTTP framing | Live result |
| --- | --- |
| One-byte chunks, previously used for C2000 | Only `get_mqtt_info`; no MQTT within 240 seconds |
| Same response with Content-Length | Binding/DST completed; TLS 1.2 with required client certificate, subscription and live `0421` telemetry |

Only framing changed between those trials. The packaged API now chooses
Content-Length for C1000 and retains the proven C2000 chunk workaround.
Offline parsing accepted both fixtures, so emulator success did not predict
this live framing difference. Provisioning returned `4824=00`; successful
setup returned `4825=00a10400000000`.

An immediate `0100` after SUBACK received no `0900`; the ordinary request
timeout closed the connection. After a passive connection and 15-second
settle, three status variants using the local ID succeeded: A1=`22`, A1=`21`,
and A1=`22` with a millisecond timestamp. Two variants using the former app
ID were ignored. This confirms local-ID command authentication, not anonymous
MQTT. Initial `0421` already contained readiness A1=`34`.

The tool waits **15 seconds from C1000 subscription** before requests,
including polls and explicit controls. This is an observed conservative grace,
not a measured minimum. Startup timing and simultaneous BLE provisioning were
confounded; the exact cause of the early loss remains unresolved. C2000 timing
is unchanged. Writes are never automatically retried after a timeout.

## Confirmed native controls

A trial using the packaged SDK, model-specific framing and startup grace
confirmed these writes through fresh native status:

- Charging power **1200→1000→1200 W**.
- Upper charge cap **100→95→100%**, preserving lower limit and reserve.
- Backup reserve **10→85→10%**, preserving outputs and charge bounds.
- All-day Peak: **17 samples over 35 seconds**, active `peak`, battery
  discharging, AC input **0 W**, AC output about **48–49 W**, mains present.
- All-day Mid-Peak: **17 samples over 35 seconds**, active `mid_peak`, battery
  idle, AC input/output about **48 W**. SOC remained above the 85% reserve;
  this does not test recharge below that floor.

The guarded return-to-grid command cleared the plan to Standard/count 0.
Three final native samples confirmed grid supply and restored settings;
independent BLE confirmed the same baseline after AP shutdown. No AC-output
switch command was included. SOC remained reported at 100% in this short test.

Charging-power/cap setters change stored limits. Because the battery began
full, this trial does not measure charging-rate enforcement or cap hysteresis.

## Reconnect, reserve floor and two slots

The same AP profile was restarted **without Bluetooth or provisioning**.
The station reconnected and accepted native requests after the startup grace.
The immediately preceding independent BLE audit supplied the saved baseline;
three fresh native samples confirmed it before any setting writes.

With reserve **100%** and reported SOC **100%**, all-day Peak remained active
but supplied the load from grid: **18 samples over 35 seconds**, idle battery,
about 48 W input/output. This validates the at-floor behavior at a full battery;
it does not establish discharge hysteresis or the transition at a lower floor.

A two-slot plan, `mid_peak:0:12,peak:12:24`, read back with count 2 and selected
Mid-Peak. The host hour was **08** in the profile's configured **Etc/UTC** zone.
Eighteen samples over 35 seconds showed idle battery and about 49 W grid/load.
This checks two-slot storage and selection within an interval, not a boundary
crossing, overnight scheduling or timezone accuracy.

Standard/count 0, reserve 10%, original caps/power and enabled AC output were
restored and confirmed in three native samples plus independent BLE.

## Radio diagnostics

The read-only `4020/4820` query also works on this C1000. The first query after
the chunked failure reported HTTP `-1`, Wi-Fi `8`, BLE disconnect `531` and
MQTT `0`; later captures reported HTTP/MQTT `0`, Wi-Fi `201` and reset bytes
`3` or `255`. Codes are retained as observations, with no new interpretation.
Successful MQTT coexisted with retained nonzero Wi-Fi codes, so these words
must not be treated as current connection booleans or inverter/battery faults.

## Offline follow-up

The exact new and known-good credential fixtures passed **36** actual-code
bootstrap/parser checks: six callback/storage/startup, twelve embedded PEM
parser/getter, two HTTP-body loop, and sixteen endpoint/country/startup cases.
Three further cases forwarded status, readiness and stream requests through
the incoming radio path:
`42027c90 → 4203b120 / 42026cf6 / 4203b4f4 → 42026f4e → 42027364 → 42043a18 → 420439cc`.

Account and serial comparisons are exact; missing/mismatched identities drop
before forwarding. ROM libc, JSON, AES/Base64, allocation, flash transforms,
OS/tasks and UART framing were substituted. These tests did not model actual
UART/MCU execution, TLS handshake or network timing. The private manifest's
SHA-256 is `993a45122c4deffeebe5ad8271a49210a8e64749ef844d16461c17a059c8a935`.

## Evidence and limitations

Private archives retain input snapshots, BLE notifications, provisioning
replies, API/MQTT frames, packet captures and restore samples.
Verified SHA-256 digests:

- BLE tariff storage: `5f308239e3c53ab969f08e8dd92fc2d70d9566b602abdc30ea90ab073d7020e0`
- Chunked bootstrap: `5bc5a36381d30c5cb11441bcfbb7cf4faa7aa3c3df47a947f26de629569f1676`
- Content-Length bootstrap: `366333b7ba64a222855cb402b95bd527c4c191d78227a2138294949265967c52`
- Delayed read-only variants: `12bd39195baef6e32ecee55c808e6dc5b37d676ea9304c23073a4914f8cca320`
- Native controls/restoration: `84a3e9f7e496717d76bfa7bcde0e5cb4541078e06bc02c111a83bb353bfa67bb`
- Reconnect/floor/two-slot trial: `446d6aaa368e5c543ae1a709f8d3cb63c7f38be891f6d236459f7a1e1a7813ba`

These tests do not establish C2000 MQTT with a generated ID, Anker app account
recovery, arbitrary AP internet behavior, timed slot boundaries, lower reserve-floor
transitions or compatibility with every firmware version. See
[AP service setup](isolated-ap-mqtt.md) for commands and safeguards.

## Additional native general settings

Temperature units and the off-grid alert switch were individually changed and
restored on the same firmware, with final independent BLE confirmation.
The [general settings reference](c1000-general-settings.md) records exact TLVs,
readback offsets, firmware side effects, offline replay counts and live results.
