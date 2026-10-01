# Original C1000: BMS battery-phase producers

Offline investigation dated 2026-10-01. Inputs are the public **A1761 main
1.5.9 package**, including **Main-BMS 0.0.33** (2024-11-22) and **Slave-BMS
0.0.5** (2024-11-28), both identifying GD32E230. BMS images load at
`08002000`. The package version is not the BMS component version.

The original station now runs main 1.7.1. Its installed BMS components were
not identified or read in this work. No hardware, network, cloud, DSP, ADC,
output, firmware update or flash operation was performed. This extends the
[BF/C0 readback trace](c1000-fast-status-retention.md#useful-monitoring-candidate-bf-and-c0)
with actual BMS producers and main-controller consumers.

## Codes 1 and 2 identify latched battery phases

Both BMS images produce the same values from signed current:

| Raw state | Evidence and recommended interpretation |
|---|---|
| 0 | Cleared/no indicated charge or discharge phase; also produced by initialization and a status-mask reset |
| 1 | Latched discharge phase, entered from the negative-current magnitude |
| 2 | Latched charge phase, entered from the positive-current magnitude |
| 4 | Produced by special branches; meaning remains unassigned |
| Other | Preserve as unknown raw codes |

These are **not instantaneous battery power measurements**. In particular,
0 does not establish healthy idle operation, a connected grid, or bypass.
The status-mask reset sets 0 while leaving nonzero current caches unchanged.
The replay deliberately preserves that counterexample.

## Current split and delayed transitions

Signed pack current is at BMS RAM `2000005c`. Under the synthetic digital
conditions used for this replay, positive values of at least **35 mA** go to
the charge-current magnitude; values at most **−35 mA** become the positive
discharge-current magnitude. Values −34 through +34 clear both. Additional
digital-condition branches can suppress readings below 600 mA; the replay
asserts the relevant bits in `2000007c` and does not model those GPIO paths.

The main controller's embedded `packCurr=%dmA` diagnostic and unscaled field
copy establish the unit. ADC calibration and electrical accuracy are outside
this investigation.

The phase producer then applies a separate **200 mA** threshold:

| Synthetic condition | Exact producer result in both BMS images |
|---|---|
| +200 mA, counters initially zero | State 2 on sample 101; still 0 at sample 100 |
| −200 mA, counters initially zero | State 1 on sample 101; still 0 at sample 100 |
| ±199 mA from state 0 | No active phase after 102 samples |
| State 2, then zero current and zero counters | Clears to 0 on sample 101 |
| State 1, then zero current and zero counters | Still 1 at sample 8,000; clears on 8,001 |
| 60 qualifying, 10 zero-current, then 51 qualifying samples | Enters the corresponding phase after the last sample |

The entry counters rise by one on qualifying samples and fall by one on
nonqualifying samples. Thus entry does not require 101 consecutive samples.
After entry, counters are capped at 100. The zero-current examples isolate
the normal producer fragment; other outer-loop policies may clear or replace
the phase sooner. The same fragment clears a seeded state 4 after 8,001
zero-current samples, without establishing the meaning of that state.

**No wall-clock latency is claimed.** The producer's execution frequency and
outer-loop scheduling were not established. Nor does 35 or 200 mA specify a
station-level watt threshold: pack voltage, filtering and other paths matter.

## Producer-to-controller evidence

| Item | Main BMS | Expansion BMS |
|---|---|---|
| Signed-current split | `08008f4a..08008fea` | `0800673e..08006890` |
| Normal phase producer | `0800b70a..0800b852` | `08008edc..0800902a` |
| State RAM, `uint16` | `200003bc` | `200003bc` |
| Charge-current magnitude | `2000046c` | `20000434` |
| Discharge-current magnitude | `200008b8` | `2000087c` |
| Charge/discharge entry counters | `200003c2` / `200003c4` | `200003c4` / `200003c6` |
| No-charge/inactive counters | `200003cc` / `200003c6` | `200003ce` / `200003c8` |
| Status serializer fragment | `0800833c..0800835e` | `08005b8a..08005ba6` |

Both serializers emit the state at payload **`+0x34`**, discharge magnitude
at **`+0x40`**, and charge magnitude at **`+0x44`**. The main BMS's expansion
reply path forwards the stored expansion payload (`08007b4a..08007b54`);
that forwarding path was inspected, not replayed as a complete serial exchange.

Main-controller `080134aa` branches on state 2: it selects `+0x44` for its
pack-current cache; other states select `+0x40`. It stores the state byte at
main/expansion BMS `+0x25`. The previously established getter/serializer path
exposes those bytes as BF/C0 typed values `01 code`.

The consumer also gives independent direction evidence. Embedded diagnostics
label capacity fields **rm** and **fc**. With synthetic remaining capacity
1,000 mAh, full capacity 4,000 mAh and current 1,000 mA:

- State 2 takes `(full − remaining) × 10 / charge_current`, yielding 30
  tenths of an hour to full.
- The other branch takes `remaining × 10 / discharge_current`, yielding 10
  tenths of an hour to empty.

The actual calculation instructions run in the replay. Entering those
calculation blocks directly tests their arithmetic; it does not establish
that an unknown state is safe to label as discharging. Main-controller
`0801683c` additionally compares power direction with state 2 before choosing
whether to expose a remaining-time estimate.

## Implications for monitoring

Keep the existing raw `battery_state_code` and
`expansion_battery_state_code`. If adding semantic fields, use a separate
**battery phase** label: 1=discharge phase, 2=charge phase, 0=no indicated
phase, others=unknown. Preserve the raw value and expose the delayed nature
in documentation. Require independent expansion-presence information for C0.

Do not set an instantaneous `battery_status` or infer mains/bypass solely
from BF/C0. A separate main 1.7.1 baseline reported BF=1 while AC input and
output both reported 115 W. Delayed phase clearing is one possible explanation;
this offline work does not establish the cause of that particular observation.
Use fresh power measurements, freshness checks and independently verified
input flags for operational decisions. These findings add monitoring meaning,
not a command to choose battery discharge or disable charging.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_bms_state.py \
  --image-directory firmware/c1000_original/1.5.9 \
  --output /tmp/original-bms-state.json
cmp /tmp/original-bms-state.json \
  tools/firmware_analysis/expected_results/original-bms-state-results.json
```

**59 new synthetic cases pass**: 26 current-entry/serialization/consumer
cases, eight zero-current decay cases, four interrupted-entry sequences,
sixteen status-mask resets, one real producer of state 4, and four remaining
estimate arithmetic cases. Real BMS instructions run with synthetic RAM and
bounded entry/stop addresses. Unmapped hardware and RAM-only write guards
prevent peripheral operations. No whole-device timing or fault response is
simulated.

All three image hashes are checked before use. Expected results and the
dependency/hash manifest contain only synthetic values and public firmware
metadata. Exploratory probes and annotated disassembly remain private.
