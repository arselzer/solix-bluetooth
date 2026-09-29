# C2000 native MQTT reconnect and tariff follow-up

## Scope

Tests on 2026-09-29 used the C2000 Gen 2, main firmware **2.1.6.4**, on the
isolated HA-node AP. AC input and output stayed on. Raw captures, configuration,
credentials and reproducible runners remain in ignored owner-only directories.
No external Anker API request or firmware write was performed.

## Reconnect and controller readiness

After a fresh BLE baseline, the known local network configuration was reapplied
while the AP started. Native status arrived about eight seconds after broker
startup. The AP was then interrupted twice without stopping the local services:

| AP interruption | First fresh native status after AP restart |
| --- | ---: |
| 15 seconds | 62.01 seconds |
| 60 seconds | 10.00 seconds |

Both reconnections recovered telemetry. This is two successful bounded tests,
not a maximum reconnect guarantee. The earlier four-minute passive failure
cannot establish a permanent failure: recovered C1000 radio retry code has
jitter and increasing delays. Ten offline executions reproduced its raw delay
selection, including random ranges 13–26 and 41–310, increasing values up to
3600, and counter reset behavior. The OS scheduler and the C2000 equivalent
were not emulated; those raw values are not a measured C2000 wait bound.

The read-only native controller request **`0089` → `0889`** succeeded on the
C2000. Pattern `03 00 0f`, `A1=22` and typed `FE` Unix seconds produced:

| Field | Observed response |
| --- | --- |
| Leading result | `00` |
| `A1` | `34` |
| `A2` / `A3` | `01 01` / `01 00` |
| `A4` / `A5` / `A6` | `01 28` / `01 0f` / `01 0f` |
| `FD` | 16 bytes; opaque/private, intentionally omitted |

This matches the recovered C1000 handler's shape and ready prefix. The exact
meaning of `A2`/`A3` is still unknown. Radio `0027` reported AP connected and
Ethernet disconnected; `0028` reported server connected. Final fresh BLE
telemetry confirmed the full unchanged baseline and AC output on after AP
shutdown. The first BLE attempt failed discovery; the second succeeded.

## Clock and separate power gate

Three local NTP exchanges were served in the reconnect test. Thirty-one native
telemetry timestamps matched capture receipt within **−3.94 to −0.75 seconds**.
That establishes current station-reported UTC, not the internal tariff clock.

Recovered C1000 main code builds `FE` from RTC plus a stored signed offset,
while its tariff selector uses the RTC directly. Radio `0026` supplies UTC and
offset separately; the controller stores the offset and synchronizes RTC with
UTC minus that offset. A correct `FE` alone therefore does not establish the
RTC/local-hour interpretation. The live station kept its Europe/Vienna rules;
neither an arbitrary time nor an internal flag was written.

Twelve additional offline executions of the original C1000 inverter-state
getters confirmed that the active predicate requires status byte bit 0 set,
the low 14 fault bits at offset `+2` clear, byte `+4` zero and inhibit bit 0 at
`+8` clear. Firmware fault logging labels the middle fields "inv fault". A
debounced poller updates the separate power-gate bit after ten consistent
samples. Its physical meaning and C2000 equivalent remain unverified. Static
reference searches did not identify a directly exposed standard telemetry field
for this complete condition; AC-output telemetry alone cannot prove it.

## Longer Peak trials with reserve headroom

Recovered C1000 discharge logic compares SOC against
`max(backup reserve, lower discharge limit + 5)`, with another 5–10 percentage
points of hysteresis and a load-dependent middle region. The previous 85%
reserve at 90% SOC may have lacked sufficient start headroom. This does not
explain why its tariff remained inactive.

A new guarded test used 90% SOC, **75% reserve**, a Peak slot **00:00–23:00**
covering both current UTC and Vienna local hours, and separately tested schedule
parameters **4 and 3**. Readiness and the reported timestamp were checked first.
Each variant was observed for about 90 seconds with fresh requested status:

| Parameter | Fresh observations | Active tariff | Battery | AC output |
| --- | ---: | --- | --- | --- |
| 4 | 43 | `none` throughout | 90%, idle | on throughout |
| 3 | 43 | `none` throughout | 90%, idle | on throughout |

Both variants acknowledged and reported the stored plan, Time-of-Use mode and
75% reserve. **Neither activated a tariff or discharged the battery.** Standard
mode, empty plan, parameter 0 and reserve 10% were restored through MQTT and
confirmed by fresh status, then independently through BLE after AP shutdown.
Charge limits remained 90%/1% and charging power 1800 W throughout. The trial
had early-stop guards for confirmed discharge or SOC ≤85%, plus a BLE restore
fallback that was not needed.

This reduces the likelihood that the earlier failure was only insufficient
reserve headroom or choosing parameter 4 instead of 3. Controller power-state,
clock propagation and C2000-specific schedule semantics remain open. Accepted
`0090` writes must still not be advertised as working scheduled discharge.

## Retained artifacts and next work

- `.solix-private/isolated-ap/reconnect-control-20260929/`: input manifest,
  reconnect timing, successful `0889`, MQTT/API/NTP/packet captures and final BLE.
- `.solix-private/isolated-ap/peak-headroom-20260929/`: guarded runners, both
  parameter trials, restoration, archive hashes and independent final BLE.
- `.solix-private/firmware-analysis/emulate_reconnect_period.py` and
  `emulate_power_getters.py`: real-code offline executions and JSON results.
- `.solix-private/isolated-ap/packaged-lab-20260929/`: installable-tool validation.

The [parallel offline follow-up](mqtt-power-offline-followup.md) subsequently
established that a complete C1000 all-day slot matches every RTC hour, even
invalid-looking RTC values. Clock error alone therefore cannot explain the
earlier full-day C2000 failure if its selector is equivalent. Further work should
prioritize safely observed inverter-state transitions and C2000 plan semantics,
ideally using the noncritical C1000 for physical transitions. Keep C2000 AC
output on. The [packaged local endpoint](isolated-ap-mqtt.md) makes the verified
connection and charging-limit paths reusable without exposing tariff experiments.

## Packaged-tool hardware validation

The installable Python CLI subsequently established local AP/API/NTP and native
MQTT telemetry on the same C2000. Its owner-only control socket returned the
expected readiness prefix and confirmed charging power **1800→1700→1800 W**
using a fresh status before the write and after each acknowledgement. AC output
stayed on. Final BLE attempt one failed discovery; attempt two confirmed the
complete original power baseline. The AP namespace was removed and its adapter
left DOWN. Remote private copies were removed only after matching the local
archive hash; all three package-test attempts remain retained locally.

Two packaging errors were caught during those attempts: the radio sends paths
with a double leading slash, and the root CA field must be plain PEM while the
client certificate/key fields are wrapped. Both were corrected and covered by
tests. The final package suite has **157 passing Python tests**, including
interactive navigation, MQTT/TLS exchange, stale data, control confirmation,
timeout isolation, native HTTP authentication and AP preflight/cleanup behavior.
