# Gen 2 disaster-preparation and Storm Guard investigation

## Scope and result

This is **offline C1000 Gen 2 (A1763), main firmware 1.1.4.9** evidence.
No device, network, BLE, cloud or electrical-output command was sent. It does
not establish C2000 Gen 2 behavior or original C1000 compatibility. In
particular, original C1000 command `005e` has a different meaning.

The [instruction replay](../tools/firmware_analysis/emulate_disaster_plan.py)
passes **762 synthetic cases**. An active manual disaster-preparation or
automatic Storm Guard window can override the normal charge-power ceiling and
TOU discharge policy, while retaining the controller's AC-output enabled
state. This is a charging-policy override, **not proof of uninterrupted AC or
physical charging on a station**. A zero BMS current allowance still results
in zero requested charging current.

Two consequences matter before exposing this as a control:

- Active plans temporarily request BMS charge/discharge bounds of **100%/1%**,
  regardless of the saved bounds or the plan's supplied maximum-SOC byte.
- Disabling the manual plan also invalidates automatic windows covering the current UTC time, even with their switch off.
  A one-bit restore is therefore insufficient when such windows exist.

The initial static suspicion that manual disable was ineffective was disproved
by instruction replay: **both manual and automatic switches honor zero**.

## Native `005e` fields

The recovered application-command handler is `0800c0a8..0800c282`. The replay
uses the real TLV parser and this handler; session authentication, MQTT routing
and the radio's treatment of this command are outside this suite.

Offsets below are within a TLV value and include its type byte. Ordinary
one-byte settings use type `01`; the plan record uses type `04`.

| Field | Recovered meaning |
| --- | --- |
| A3 | Required selector, value `3`. Missing or other values are acknowledged without modifying settings. |
| A4 | Required option: `0` manual, `1` automatic, `2` clear all disaster records and both switches. Other nonzero values also follow the automatic branch; do not expose them as supported choices. Missing A4 with A3=3 returns without the normal ACK. |
| A5 | Optional switch. Zero disables; every tested nonzero value is stored as enabled. |
| A6 | Automatic-record count, capped at three. Manual storage does **not** require A6=1: the handler consumes A7 when present even if A6 is zero or absent. |
| A7 | Manual record, or automatic slot zero. |
| A8/A9 | Automatic slots one and two. |
| AA | Value `1` cancels the currently active plan and processes windows covering the current UTC time. It is not an isolated switch operation. |

Each A7–A9 record has **11 value bytes**:

```text
04 maximum_soc minimum_soc start_u32_le end_u32_le
```

The controller copies `value[1]`, `value[3:7]` and `value[7:11]` into its
nine-byte stored record. It skips `value[2]`, the supplied minimum-SOC byte.
The maximum byte is stored but does not limit charging in the policy/BMS
branches replayed here: synthetic values 0, 80 and 100 produce the same active
override. This is narrower than claiming that the byte has no consumer anywhere
in the complete firmware.

Manual storage occupies `20001ec4..20001ecc`; automatic storage starts at
`20001ea9`, with three nine-byte records. Switches are `20001ecd` and
`20001ece`. Setters `0802b430`/`0802b478` request persistence. When any selected
automatic record is supplied, the handler replaces **all three slots** from
its zero-initialized temporary array. Missing slots are cleared. Count zero
without a record update leaves the previous automatic records intact.

## Activation, clock and expiry

Selector `08018aac..08018c78` returns a twelve-byte active record at `200018ec`:
the nine-byte stored record, kind (`1` manual or `2` automatic), active flag and
automatic-slot index.

Activation requires:

1. The calendar-validity getter `08015450` reports a year later than 2024.
2. The corresponding switch getter `0801a61c` is enabled.
3. Start is nonzero, end is greater than start, and `start <= now <= end`.

The comparison clock is the real RTC getter plus the stored signed offset:
`08029cbc() + 0801a6b0()`. The separately recovered normal time-sync path stores
RTC as UTC minus that offset, so the resulting comparison is UTC under that
path. This suite runs RTC and offset reads, but substitutes calendar conversion
with a synthetic year; it does not validate real clock synchronization.

Initial selection scans enabled automatic slots in order, then manual. An
already-active matching record is retained before this scan, so adding an
overlapping automatic record does not necessarily replace an active manual
selection immediately. Cached validation requires `end > now`; at exact end,
the subsequent fresh scan selects the same inclusive-end window again.
At end plus one second it becomes inactive. The tested selector accepts a very
distant future end; no duration limit was found in this selector. No claim is
made about app-side or other validation.

## D9 readback and restoration limits

Actual D9 builder `080190d4` appends the following nineteen-byte tail after its
seven-byte prefix and `3 * tou_period_count` bytes of TOU periods. Index zero
of the full D9 value is type `04`; therefore the tail begins at
`7 + 3 * D9[6]`.

| Tail offset | Length | Meaning |
| ---: | ---: | --- |
| 0 | 1 | Active disaster kind: 0 inactive, 1 manual, 2 automatic |
| 1 | 1 | Manual switch, normalized boolean |
| 2 | 1 | Automatic switch, normalized boolean |
| 3 | 4 | Stored manual start, little endian |
| 7 | 4 | Stored manual end, little endian |
| 11 | 4 | Currently active start, little endian |
| 15 | 4 | Currently active end, little endian |

This D9 tail does **not** include all automatic records, their stored maximum
bytes or the manual maximum byte. It cannot by itself prove that no dormant
automatic window exists or reconstruct the full stored configuration.

The actual cancellation helper `080093f4` tests whether a saved window covers the current UTC time,
`start <= now < end`:

- Manual A5=0 disables manual and replaces every automatic record covering now, even with its switch off,
  with sentinel `64 ff ff ff ff ff ff ff ff`.
- AA=1 first replaces the active record with that sentinel. Cancelling an
  automatic record can also disable manual and invalidate other automatic records covering now.
- A4=2 clears the complete 38-byte record/switch region. It is destructive to
  saved future plans; it is not a generic restoration command.

Natural expiry changes active selection without rewriting the stored plan.
An expired manual record is still different configuration from an initially
empty record.

## Charging and BMS consumers

### Charging policy `08014d58`

The replay executes the original charging-policy instructions with synthetic
BMS readings and captures the final charge descriptor before transport.
For both manual and automatic active plans:

- The normal charging branch is selected during Standard, Peak, Mid-Peak and
  Off-Peak. In particular, active backup bypasses the Peak/Mid-Peak suppression
  branch around `080150e4..08015156`.
- The internal power member uses the same **1400** constant as fast charge,
  instead of the configured power multiplied by approximately 0.88. This is
  an internal descriptor value, not an observed wall-power measurement.
- No fast-charge setting needs to be enabled. The saved power, cap, floor,
  reserve and other persistent settings remain byte-for-byte unchanged in the
  replay.
- BMS current allowance remains authoritative. Synthetic allowance zero
  produces zero requested current even with the override active. The
  synthetic AC-charge-available flag must also be set for an AC descriptor.
- Existing nonzero output-mode bits at `20000164[5:4]` become mode 2; an
  initially disabled output stays disabled. The enabled boolean is preserved,
  but this is not preservation of every internal output-mode bit.

The synthetic network-ready and debounced-power-gate values do not gate
disaster selection directly. Testing them independently deliberately bypasses
their physical producers; it cannot establish that a station will charge with
an invalid AC input or a real fault.

### Periodic BMS limit update

The replay enters the limit-update tail of `08023e0c` at `08023f3e`, after the
unrelated event-processing prefix. It runs the actual policy/getters and BMS
descriptor builder, replacing allocation and queue delivery.

Active disaster selects **upper 100, lower 1** in the first two bytes of BMS
descriptor `f015`, even when saved bounds are 80/5 or 90/1. The third member
remains the saved lower bound in Standard; with an active tariff it is the
reserve limited by the effective upper bound in the valid configurations
tested. For example:

| Saved upper/lower/reserve | Mode | No disaster | Active disaster |
| --- | --- | --- | --- |
| 80/5/75 | Standard | `80,5,5` | `100,1,5` |
| 80/5/75 | Peak | `80,5,75` | `100,1,75` |

The earlier initialization path `0800dc50` has different behavior: it sends
the saved upper/lower and computes the third member. The periodic tail is why
observing only that initializer would miss the override. Neither replay models
BMS acceptance, battery protections, DSP operation or physical charge flow.

Other statically identified consumers use active kind for event records
(`0800d0c8`), display indicators (`0802cac6`) and energy-accounting group
selection (`0802df5c`). Those complete event/display/energy paths were not
replayed by this new suite.

## Practical next step

This provides a concrete local charging-override lead for **C1000 Gen 2 only**.
It is not ready to expose as a simple pause/resume or reserve-respecting switch:
it can exceed the saved charging cap and configured charge rate.

Before a bounded physical trial, record fresh full D9/A4/settings and establish
that there are no automatic windows that cancellation could destroy. A test
would need a short manual window, explicit acceptance of charging toward 100%
at the fast ceiling, fresh active-kind and real charge-flow confirmation, an
expiry deadline, and complete configuration restoration. D9 alone cannot
provide the missing automatic-record backup. Do not use clear-all or cancel
against an unknown schedule, or infer AC continuity from this emulator.

No C2000 trial, raw internal-register write, clock change, or output-switch
operation is proposed by this investigation.

## Guard on existing tariff controls

For **C1000 Gen 2**, the native MQTT tariff-activation path now requires a
fresh status response decoding `disaster_preparation_active` as integer `0`.
Manual or automatic active status, an unknown kind, or missing telemetry is
refused before the `0089` readiness query and any `0090` activation write.
Cached inactive status cannot authorize activation. This also applies when
return-to-grid would activate an Off-Peak plan.

Storing an inactive plan or clearing to Standard remains available with the
existing complete-baseline checks. The guard sends no `005e` command and
does not change disaster records or switches. C2000 behavior is unchanged;
this firmware evidence is specific to C1000 Gen 2.

The check establishes inactivity at the baseline observation only. It cannot
prevent an already configured future backup window from becoming active
during a tariff plan. Synthetic protocol tests exercise active manual and
automatic frames, unknown and missing readback, stale cached status, allowed
inactive status, and Standard/storage clearing. They do not establish live
Storm Guard behavior or physical charging outcomes.

## Reproduction and limitations

Input is the published, hash-checked C1000 Gen 2 image:

```text
MainMcu-decoded.bin
SHA256 21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9
```

Use the requirements and external firmware override in the
[reproduction guide](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-disaster-replay \
  python3 tools/firmware_analysis/emulate_disaster_plan.py
cmp /tmp/solix-disaster-replay/disaster-plan-results.json \
  tools/firmware_analysis/expected_results/disaster-plan-results.json
```

Cases: 256 switch getters, 72 wire/storage combinations, 28 selector/clear
cases, 120 clock-window/D9 cases, 22 priority/cancellation/expiry cases,
216 charging-policy combinations, 12 synthetic gate/output-mode cases and
36 periodic BMS-limit cases. Results and their adjacent manifest are entirely
synthetic. The standalone tool does not modify the shared replay runner.

Real parser, handler, storage access, switches, selector, clock reads, D9
serializer, charging policy and selected BMS-update tail execute. Inherited
flash, response transport, timers, memory-copy routines, LCD delivery, battery
sensors, calendar conversion, descriptor destination and history/event
boundaries are substituted. No physical safety mechanism, fault producer,
battery/DSP firmware, radio delivery or real scheduler is reproduced.

The [complete-readback follow-up](gen2-persistent-plan-followup.md) provides
16 additional replay cases: different saved configurations can have identical
D9 status. Inactive status alone cannot justify a reversible backup write.
