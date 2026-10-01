# Original C1000: SOC reporting and charging near full

Offline investigation, 2026-10-01. This uses the public **A1761 1.5.9
bundle**: MainMcu 1.5.9, MainBMS 0.0.33 and SubBMS 0.0.5. The installed
station runs MainMcu 1.7.1; its image and installed BMS versions were not
obtained here. No device, network, output or persistent setting was accessed.

The [live charging-rate trials](c1000-charging-rate-validation.md) recorded
100% through several minutes of discharge, a later 99% charging observation,
and another 100 W test with input approximately matching the output load.
The mechanisms below establish possibilities in the older public firmware.
They do **not** establish the cause or duration of those live observations.

## What C1 actually contains

The controller does not calculate a new capacity percentage when it serializes
C1. It copies a BMS estimate through this chain:

| Stage | MainMcu address / location |
|---|---|
| Incoming primary BMS body, uint16 SOC at `+32` | `080134c8..080134cc` |
| Primary BMS cache, one byte | `200005d4+23` |
| Getter index 6 | `0801679c` |
| Status information cache | `08010c9a..08010ca0`, `20001374+3d` |
| Typed C1 field | serializer prefix `08009164..0800924a` |

C2 separately uses the expansion cache (`200005d4+5b`, getter index 7).
Actual parser/getter/serializer replay with primary 100 and expansion 50
produces **C1=`01 64`, C2=`01 32`**. There is no averaging in these MCU
copy/getter/serialization blocks. This does not audit every upstream BMS
capacity calculation or every app presentation rule.

MainBMS writes its cached uint16 SOC at `200011c4` into body `+32`
(`08008350..08008354`). SubBMS normally does the same from `20000b10`
(`08005b5e..08005b62`), but its `08005b70..08005b82` branch overrides the
**serialized** SOC to 100 when raw byte `20000974` is zero, without changing
the cached SOC. The meaning of that mode byte is unresolved; do not treat
an expansion value of 100 as unconditional proof of a full battery.

A freshly received status therefore confirms fresh transport and the current
reported estimate. It does not force the BMS to recalculate physical capacity.
Neither a BMS update interval nor end-to-end latency is established here.

## Rounding, a full-related latch, and capacity history

Both BMS images calculate a capacity ratio in tenths, then round its last
decimal digit upward at 5:

```text
tenths = floor(1000 * remaining / full)
candidate = floor((tenths + 5) / 10)
```

This is actual arithmetic at MainBMS `08009d0c..08009d46` and SubBMS
`0800772c..08007764`. For a synthetic full capacity of 100000, remaining
99499 gives candidate 99; 99500 gives candidate 100. Values here are raw
capacity units; this replay does not derive elapsed runtime from them.

The final reporting blocks add another condition:

- MainBMS: `0800a1d8..0800a1ee`, latch at `20001164+2b`.
- SubBMS: `08007ae4..08007afa`, latch at `20000ab0+2b`.
- Candidate 100 is reported as **99 unless the latch equals `5a`**.
- Candidates 98 and 99 remain unchanged in the tested cases.

There is also an alternate ratio in the MainBMS non-charge branch at
`0800a1a4..0800a1d6`. It uses a retained denominator at `20000018`, rounds
and caps that estimate at 100, and keeps the greater of that result and the
earlier candidate. With remaining 99000, full 100000, the earlier candidate
99, and the full-related latch set:

| Retained denominator | Final reported SOC |
|---:|---:|
| 100000 | 99 |
| 99000 | 100 |
| 98000 | 100 |

These denominators are deliberately seeded. Their complete learning,
calibration and previous-discharge histories are **not simulated**. The
example establishes a history-dependent reporting path, not a measured
station capacity error.

Another history branch runs on charge-phase entry. If the BMS phase is 2
and its entry flag is clear, it replaces remaining capacity with
`reported_SOC * full_capacity / 100` and sets the flag. MainBMS executes
this at `08009ce2..08009d0a`; SubBMS at `08007702..0800772a`. Synthetic
reported SOC 100, remaining 98000 and full 100000 rebases remaining to
100000 on that entry. Phase 1, or an already-set entry flag, leaves 98000
unchanged. Phase 2 is itself a latched state; see the
[BMS phase investigation](c1000-battery-phase-firmware.md).

## The full-related latch has its own clearing policy

MainBMS `0800af14..0800af4e` and SubBMS `0800882c..08008864` clear the
`5a` latch when an external counter is at least **201** and one of these
tested conditions holds:

- The capacity candidate is below 97.
- The cached reported SOC is below 100.
- Both remain at least those thresholds, but a cell-voltage cache is below
  3320 and the BMS phase is zero.

When candidate/report/cell/phase conditions instead preserve the latch, the
code resets that counter to zero. At counter 200, the tested clearing
conditions leave the latch set. This fragment does not increment the timer;
an independent path does. **201 is not a duration**, and the 97 comparison
is not evidence that charging universally restarts at 97%.

MainBMS additionally ORs the latch into outgoing status byte `+54`, bit 0
(`0800835e..0800836c`). MainMcu copies that bit into selected-BMS cache
`+31` (`080134ce..080134d4`). The original raw status bit can also set it;
clearing the SOC latch alone is therefore not proof that the outgoing bit
clears. Other raw-status producers, hardware conditions and protections
remain relevant.

## What the MCU does with that flag and a 100 W setting

The complete MainMcu charging callback `08014118` was replayed with Normal
mode, a synthetic existing charging gate, no expansion, and SOC held at 99
or 100. Setting BMS `+31` to 1 selects internal policy state 7. Its flag
clearing path at `08014b5a..08014b78` returns to state 0 when the flag clears.
This happens with either seeded SOC. On the next callback, the tested
100-SOC case enters state 2; the 99-SOC case remains in its startup path.
These deliberately independent flag/SOC seeds are not claims that every
combination persists on physical hardware.

While state 7 is held, the replay produces:

| Saved D1 | Descriptor `[power, voltage, current]` |
|---:|---|
| 100 W | `[91, 384, 1500]` |
| 300 W | `[273, 384, 1500]` |
| 1000 W | `[910, 384, 1500]` |

The power word uses the previously established `D1 × 0.91` path. The
voltage request here differs from the state-2 request documented in the
[current-limit investigation](c1000-fast-current-limits.md). A nonzero
charging descriptor does not establish realized battery current: BMS
permissions, actual pack voltage and DSP enforcement still matter.

The evidence supports calling D1 a **saved charging-power setting feeding
the MCU charging request**. It does not prove a total AC-input budget or
an exact battery-side watt limit. No simultaneous-output-load subtraction
was established in this path; the DSP's enforcement has not been executed.
The live 100 W setting with approximately 115 W passing through to the load
must not be represented as a verified 100 W total-input cap.

## Practical remaining uncertainty

- Do not use SOC 100 alone to classify bypass, battery flow or a failed
  charging-power write. Confirm the setting from fresh telemetry, and
  distinguish that from observing its electrical effect.
- A useful rate comparison needs a sustained interval of fresh input power
  above output power and charging headroom. Neither a fixed six-minute
  discharge nor one 99% sample guarantees that interval.
- The installed 1.7.1 MCU and BMS versions, exact capacity history,
  voltage/protection state and periodic update cadence remain unknown.
- No new recharge-threshold setting, AC-input-disable command or safe
  forced-discharge mechanism was found by this work.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_soc_recharge.py \
  --firmware-dir firmware/c1000_original/1.5.9 \
  --output /tmp/original-soc-recharge.json
cmp /tmp/original-soc-recharge.json \
  tools/firmware_analysis/expected_results/original-soc-recharge-results.json
```

**78 new cases pass:** 16 rounding, 12 final-latch, 14 latch-clear,
6 retained-denominator, 3 primary serialization, 6 expansion-override,
6 phase-entry resynchronization, 6 latch-to-MCU-flag, 3 independent C1/C2,
and 6 complete charging-policy sequences. All three input image hashes are
checked. The manifest records the new tool, imported helpers, dependency
file and expected-result hashes. BMS fragment stops are explicitly hooked
so a cached Unicorn translation cannot run past a reused stop address.
Only public images and synthetic RAM/results are involved.
