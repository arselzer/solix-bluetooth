# Original C1000: Fast current limits and charging requests

Offline investigation dated 2026-10-01 using public **A1761 main 1.5.9**.
The station's current main 1.7.1 image is unavailable. Results below are
controller instructions executed with synthetic RAM, ending at captured DSP
queue records. They do not measure charging power or execute DSP firmware.
No hardware, network, cloud, output or persistent storage was accessed.

This adds to the [Fast retention analysis](c1000-fast-status-retention.md)
and [charging-path investigation](c1000-bypass-firmware-followup.md).

## Fast has a separate current table

The saved charging ceiling and the battery-current ceiling are separate.
Fast uses a temperature/charging-segment table at **`08028bba`**, selected by
`08017374` and called through the cached-temperature helper `080256a0`.
The policy takes the lower current limit calculated for the BMS's lowest and
highest temperatures.

| Cached segment | Below 10°C | 10–14°C | 15–19°C | 20–49°C | 50–99°C |
|---|---:|---:|---:|---:|---:|
| 0 | 600 | 1500 | 3000 | 3210 | 1500 |
| 1 | 600 | 1500 | 2400 | 3090 | 1500 |
| 2 | 600 | 1200 | 1800 | 2400 | 1500 |
| 3 | 600 | 1200 | 1200 | 1500 | 1500 |
| 4 | 600 | 1200 | 1200 | 1500 | 1500 |

These are raw descriptor-current values. The actual firmware diagnostic
multiplies them by **10** for `ChgCurr`, while multiplying descriptor voltage
by **100** for `ChgVot`. For example, descriptor current 3210 and voltage 392
print 32100 and 39200. This is consistent with the surrounding BMS mA/mV
convention, supporting a battery-side request of **32.1 A at 39.2 V**. The
replay verifies the numeric scaling; it does not calibrate physical current.
These are not mains-current limits or safe operating temperature ranges.

## SOC initializes a segment; it does not continuously select it

Getter `080173b8` uses thresholds **80, 90, 95, 99, 101**. At initialization,
valid SOC values map as follows:

| SOC | Initial segment |
|---|---:|
| 0–79% | 0 |
| 80–89% | 1 |
| 90–94% | 2 |
| 95–98% | 3 |
| 99–100% | 4 |

The active segment is cached at `2000041b`. Charging policy subsequently
advances it using voltage and repeated-sample conditions. In a bounded Fast
state-2 replay with SOC held at 50%, temperature 25°C, and maximum cell
voltage 3565 mV:

- Pack voltage **39199 mV** leaves segment 0 through seven callbacks.
- **39200 mV** advances to segment 1 on callback six; the current request
  changes from 3210 to 3090 on callback seven.

The change follows `0801454a..080145dc`. The active segment therefore cannot
be reconstructed from SOC alone. No callback frequency or wall-clock delay
is assigned here.

## Near-full Fast can request less current than Normal

The complete policy and existing-session DSP request producer were replayed
at 25°C, state 2, a 1000 W saved ceiling, and variant byte 0:

| SOC used to initialize segment | Normal descriptor `[power, voltage, current]` | Fast descriptor |
|---|---|---|
| 50% | `[910, 392, 2340]` | `[1400, 392, 3210]` |
| 85% | `[910, 392, 2340]` | `[1400, 392, 3090]` |
| 92% | `[910, 392, 2340]` | `[1400, 392, 2400]` |
| 97% | `[910, 392, 2340]` | `[1400, 392, 1500]` |
| 99% | `[910, 392, 2340]` | `[1400, 392, 1500]` |

Thus a higher Fast power allowance can coexist with a **lower current
ceiling**, particularly in segments 3/4. A near-full comparison might show
little increase, or a decrease, without disproving successful Fast control.
Temperature, voltage-stage transitions and BMS limits add further constraints.
The table records selected controller requests, not their realized power.

Synthetic variant 1 was also checked: at 25°C Normal selects power 910,
voltage 568 and current 1170; Fast selects power 1000, voltage 568 and the
same segment-table current. That branch is not a separate device validation
and should not classify hardware by name.

### Temperature history also matters

The helpers have hysteresis near downward temperature boundaries:

| Sequence | Replayed current requests |
|---|---|
| Normal, 25 → 24 → 23°C | 2340 → 2340 → 1500 |
| Fast, 20 → 19 → 18°C | 3210 → 3210 → 3000 |

At a one-degree crossing, the previous limit can survive. Seeded cold-state
lookups and a running station with temperature history need not immediately
produce identical values. Do not diagnose a failed setting from an expected
instantaneous watt change based only on current SOC and temperature.

## Changing D1 while Fast is active

One complete synthetic round trip executes the real Fast and charging-ceiling
handlers, policy and DSP queue builder:

| Step | Saved D1 | E5 | Requested power word |
|---|---:|---:|---:|
| Normal baseline | 1000 | 0 | 910 |
| Fast on | 1000 | 1 | 1400 |
| Set saved ceiling to 300 | 300 | 1 | 1400, unchanged |
| Fast off | 300 | 0 | 273 |
| Restore saved ceiling | 1000 | 0 | 910 |

Changing D1 alone while Fast remains enabled produces no changed descriptor
and no new register-4 queue record in this case. Disabling Fast uses the
**then-current saved ceiling**; it does not restore a remembered pre-Fast
value. Every step preserves output flags, complete F8, timer state and the
other protected settings. The complete original snapshot is restored at the
end; persistence is substituted in the emulator.

A low saved D1 value is therefore **not a reliable input-power safeguard
while Fast is enabled**. This follows the original 1.5.9 path and needs a
separate 1.7.1 behavior check before production assumptions.

## What reaches the DSP, and what is still unknown

Producer `0801f570` passes the selected descriptor through `0802461c` into
register 4. For the 50%/25°C/variant-0 example:

```text
Normal: 380, 392, 234,  910, 0, 280
Fast:   380, 392, 321, 1400, 0, 280
```

The first word is measured BMS pack voltage divided by 100. The second is
the voltage request. The third is descriptor current divided by 10, with
the previously established floor/clamp. The fourth is the selected power
allowance, capped at 1600 by the producer. Word four in zero-based indexing
retains its seeded cached value (zero here); the final word is 280. Their
electrical meanings are not assigned.

The descriptor's printed current and the DSP current word consequently
differ by another factor of ten. Treating the wire word 321 as 321 amperes,
or 3210 as the same units as measured BMS current, would be incorrect.

No absolute mains-capacity limit, accounting for simultaneous AC-output load,
DSP-side enforcement rule, or conversion efficiency was established here.
The known ×0.91 expression is firmware arithmetic, not a measured efficiency.
For a live comparison, measure fresh input/output powers and protect the
physical source independently. Retain the E5, D1 and whole-F8 restoration
checks; a useful rate comparison also needs enough charging headroom and
stable conditions. None of this provides a command for battery-only operation.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_fast_power.py \
  --image firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-fast-power.json
cmp /tmp/original-fast-power.json \
  tools/firmware_analysis/expected_results/original-fast-power-results.json
```

**50 new cases pass**: 25 table lookups, 20 complete policy-to-queue cases,
two temperature-history sequences, two voltage/segment sequences and one
complete saved-ceiling/Fast restoration sequence. The image SHA-256 is
checked; the expected-result manifest records every imported helper hash.
Only synthetic results and public firmware metadata are published. Private
exploratory data is retained separately.
