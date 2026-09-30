# Local energy reporting: enable switch and lifecycle

## Evidence

Investigation on **2026-09-30** executed **140 lifecycle cases** and **26
point-switch cases** against retained **C1000 Gen 2 main 1.1.4.9 / radio
0.3.3.0** instructions. Original report callbacks, connection predicates,
radio JSON validation, business callback, TLV encoder and controller flag
handler ran in Unicorn. JSON node access, sensors, UART/queue transport,
logging and persistence were substituted. No flash or power electronics ran.

The separate live target was **C2000 Gen 2 main 2.1.6.4**, on the HA node's
isolated AP. Its firmware binary remains unavailable. All captures, credentials,
scripts and replay results are owner-only under `.solix-private/`.

## Analytics enable switch

The station fetches `/equipment/agreement/get_device_point_switch`. Both the
recovered C1000 code and the C2000's captured request identify point **20001**.
A successful local reply is:

```json
{"code":0,"msg":"success","data":{"param":[{"param_name":"20001","param_value":"1"}]},"trace_id":""}
```

Radio callback `0x4201f34c` requires `code` and `msg`, reads the first `param`
entry, checks the exact five-character name and accepts string `"0"` or `"1"`.
Missing `param` reports failure/disabled; an empty array selects enabled. Wrong
names, numeric values and malformed entries fail. Later entries are ignored.
Use an explicit entry instead of relying on the empty-array behavior.

Registered callback `0x4203e302` emits internal function **10**, opcode
**0014**, raw TLV `a1 01 00/01`. Controller handler `0x0800bf34` stores a
normalized boolean at **0x200007ba**, logged as `burial onoff`. Its settings
block at `0x20001d40` stayed unchanged in four replay cases. This is an
analytics-report switch; these bytes contain no AC-output or charging field.

The earlier API placeholder returned `data:{}`, which disables this switch in
the recovered code. The tool now returns the complete schema: reporting is
off by default and **`ap-service-run --energy-reports`** selects `"1"`.
It is independent of `--allow-control`. Changing the server option takes
effect only when the station fetches the endpoint again; tool shutdown does
not establish a device-side flag reset.

## Trigger, retry and completion

General accumulator `0x0802df3c` performs an energy sample every ten callback
invocations. Registration uses raw period 1000; ten-second sampling is a
timing inference until independently calibrated. Report-enable getter
`0x08018554` reads `0x200007ba`. State `0x20000164.bit23` inhibits reports;
the complete meaning of that state bit is unresolved.

Queue helper `0x08013d94` accepts either the network-ready predicate
`0x0800d1d8` or the separate connection bit tested by `0x0800cfd4`.

| Pending byte `0x20000249` | Report attempt |
| --- | --- |
| 0 | Every energy sample while reporting and connection gates allow it |
| 1 | Only sample indices divisible by 60; nominally ten-minute boundaries |

The queue return becomes the pending byte. Across 121 synthetic samples,
return 1 queued at indices **1, 60, 120**; return 0 attempted every sample.
Descriptor fields were completion `0x08029331`, wrapper `0x08011ae9`, builder
`0x0802db31`, raw timeout 50, function **0f**, opcode **0401**.

Completion `0x08029330` clears pending only when its status is zero and the
separate connection bit is clear. It does **not** clear the general energy
counters. A report is therefore not evidence of a fresh counter epoch.

## Persistence and rollover

The report sample counter at `0x20000260` checks its `>8640` rollover only
at 60-sample consolidation boundaries. Tested counts 8640–8642 did not flush
or reset; **8700** reset the sample counter and invoked persistence helper
`0x08015c88`. This counter rollover is separate from the energy-counter reset.

Static continuation shows `0x0802a9ac` copying the 0xd8-byte general accounting
block into tracking RAM. Flash routine `0x0802de70` uses a `trcP` record with
length/checksum, and initialization `0x080175fc` loads stored accounting data.
Reset routine `0x08029868` clears the accounting block and schedules its
persistence. These traces suggest persistence across restarts, but the exact
reset callers and hardware behavior need validation. No flash routine was
executed against a station.

## Live capture

The first attempt reused the saved AP profile without provisioning. It saw no
station association or MQTT session within four minutes; after cleanup, BLE
still confirmed the protected power baseline.

A second attempt resent the **same** saved Wi-Fi/API profile, supplied the
explicit analytics-enable reply, and established local TLS MQTT. The C2000
then posted `charging_pps_series_c_0009` to
`/equipment/logging/upload_pb_events`. The existing partial decoder recognized
all four accounting groups. No Anker request or internet route was involved.

The protected baseline is Standard mode, no tariff periods, AC output on,
mains present, reserve 10%, charge limits 90%/1%, charging power 1800 W and
fast charge off. Monitoring does not send power-setting commands. Two reports arrived **540.005 seconds** apart. Standard AC-input and AC-output
counters each increased by **58 raw units**, and AC-output duration increased
by one. The 103 telemetry samples between their HTTP receipt times averaged
**350.9 W**, approximately **52.6 Wh** over that interval. The raw increment is
about 10% larger than this estimate. A nominal ten-minute calculation would
produce about 58.5 Wh, but this is not sufficient calibration: firmware timing,
report construction versus delivery times and model differences must be
resolved. Other decoded counters were unchanged in this interval.

The bounded capture retained **231 fresh status samples** over about 20
minutes, plus three final native readings. Every protected power setting stayed
at baseline and AC output remained reported on. Immediately after AP shutdown,
BLE was temporarily absent; a later separate BLE connection confirmed the
baseline again before a short second native session served `20001="0"`. That
session's three final native readings also matched baseline. The disable API
reply was delivered; the flag itself has no established public readback.

Both AP sessions stopped and their private archives were copied locally with
SHA-256 verification. No native output-switch or charging-setting command was
sent during this investigation. MQTT request opcodes were status `0100` and
readiness `0089`; the only BLE configuration writes were same-profile Wi-Fi/API
bootstrap `4024`/`4025`. No waveform-level continuity measurement was made.

## Remaining limits

See [report wire format](tariff-energy-followup.md) for known protobuf fields.
Raw values are not yet safe as HA lifetime-energy entities: physical scaling,
restart/reset behavior, backup-group meaning and counter epochs remain partly
unverified. The six native `0503/state_info` words are a different unresolved
format. An incoming `0401` query has not been established and is not sent.

## Reproduction manifest

Private combined manifest `energy-lifecycle-followup-sha256.json` SHA-256:
`4f9abb5c40b344cda381487ceb5062a93047e1bb9d4c1bbee3d79383249228cf`.
It covers both offline scripts/results and the verified live archives.
