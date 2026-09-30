# Tariff policy and passive energy-report findings

## Evidence and limits

Offline analysis on **2026-09-29** executed **101 additional cases** against
recovered **C1000 Gen 2 main 1.1.4.9 / radio 0.3.3.0** instructions. Sensors,
backup states and some platform helpers were substituted; power electronics
and physical transitions were not emulated. C2000 firmware is unavailable.
Its separately [verified tariff-3 grid return](c2000-offpeak-grid-return.md)
does not validate every recovered C1000 behavior.

## Tariff and reserve behavior

| Wire value | Public name | C1000 firmware log name | Recovered policy |
| --- | --- | --- | --- |
| 0 | None / Standard | — | Ordinary charge branch; a no-charge path can retain internal supply mode |
| 1 | Peak | Peak | Charge below reserve bound; above it, battery supply depends on SOC/load hysteresis |
| 2 | Mid-Peak | OffPeak | Charge below reserve bound; otherwise select grid with zero battery-charge request |
| 3 | Off-Peak | SuperOffPeak | Ordinary charging branch and grid selection, subject to cap/enable gates |

Policy `0x08014d58` uses `max(reserve, lower_limit + 5)`. Active tariffs clear
fast charge. Reserve stayed unchanged in 32 replay cases. Eight TLV-handler
cases showed A5 setter `0x0802b550` stores any byte, even 0/255: firmware
acknowledgment supplies no range guarantee. The client therefore enforces
5% steps and `lower + 5 <= reserve <= upper`. Cap setters can clamp reserve.

## Two distinct accounting systems

- Tariff-only accumulator `0x0802d880`, RAM `0x200018f8`, tracks solar-to-battery,
  grid-to-battery, battery-to-load and tariff durations. It clears its working
  RAM every 120 active callbacks after passing sums to another helper.
- General accumulator `0x0802df3c`, RAM `0x200032c0`, groups AC/DC input/output
  energy and enable durations by Standard, active tariff and two backup variants.
  These supply the general report below; they are not the named tariff-only sums.

Nominal timer periods suggest general 10-second energy samples and `sum / 360`
report values in Wh, with durations in 10-minute units. **Actual scheduler
timing, physical scaling/calibration, on-device persistence/reset behavior and
C2000 equivalence remain unverified. The later [report lifecycle trace](energy-report-lifecycle.md)
identifies completion, retry and persistence branches offline.** Do not publish these as lifetime HA energy
statistics yet.

## Binary report and transport

Builder `0x0802db30`, descriptor `0x08033c64`, and encoder `0x08022a14` emit
**protobuf binary**, not MCU JSON. Three actual-encoder cases included zero,
distinct counters and sums above 2^32. The [protobuf wire specification](https://protobuf.dev/programming-guides/encoding/)
describes the varint and length-delimited representation used by the decoder.

| Top-level tag | Group | Nested tags |
| --- | --- | --- |
| 18 | Time-of-Use | 3 AC input, 4 AC output, 7 DC input, 8 other output energy |
| 19 | Standard | Same |
| 20 / 21 | Backup variants, phase meaning incomplete | Same, plus unresolved tag 9 |

Nested duration tags: 1 AC-output enable, 2 AC-charge enable, 5 other-output
enable, 6 DC-charge enable. Additional top-level/callback fields remain partial;
for example 23.1 SOC, 23.5 DC-input power, 23.6 raw DC voltage. They are not
included in the public partial decoder.

Outbound app-function `0f`, opcode `0401`, has A1=`1a` when network-ready;
A2 uses a **two-byte little-endian length**, including type `04`, followed by
protobuf. A3 names `charging_pps_series_c_0009`. This differs from ordinary
one-byte-length TLV parsing. Fourteen wrapper cases and five radio-dispatch
cases verified the route. Static radio continuation wraps the binary as
base64 at `events[0].params.payload`, alongside private device/account fields,
then selects **`/equipment/logging/upload_pb_events`**, HTTP task `0x16`.

## Tool support and next checks

The isolated API now retains this logging request locally, decodes known groups
into private `energy_report` records and returns a minimal local acknowledgment.
That acknowledgment is a local implementation; Anker's response schema has not
been captured. No such request was observed in the short live C2000 trial.
No incoming `0401` query is established, so the tool sends none.

Offline use:

```python
import json
from solix_link.energy_report import decode_energy_events

reports = decode_energy_events(json.loads(private_request_body))
# Raw counter values only; units_verified is False; identity fields omitted.
```

Parsing is bounded, skips unknown fields and rejects truncated/invalid wire
data. It is deliberately absent from normal telemetry and HA energy entities.
The packaged decoder matched all three retained actual-encoder replay cases
(zero, distinct groups and large-u64 inputs); this validates the recovered
counter schema, not its physical units.
An opt-in `ap-service-run --energy-reports` now returns the recovered analytics
point-switch schema; power controls still require `--allow-control`. See the
[report lifecycle findings](energy-report-lifecycle.md) for its firmware gates
and live capture results. Next, compare
counter deltas with measured power/time, and determine restart/reset behavior.
The six native `0503/state_info` energy words remain separate unknowns.

## Reproduction retained privately

Scripts, disassembly, inputs and replay results remain under
`.solix-private/firmware-analysis/`, owner-only and ignored. Combined manifest
`tariff-energy-followup-sha256.json` SHA-256:
`bb4e2172e27c81024305a4b6659b4f5e24518b66127e086fcb37cd919e5fdf04`.
No firmware binary, private identifier or phone capture is published.
