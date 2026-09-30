# C2000 packaged tariff controls and grid return

Command examples use current `ap-service-*` names; the recorded trial
preceded the rename.

## Result and versions

On **2026-09-29**, the public `solix-link` CLI on the isolated HA-node AP
activated Peak, observed battery supply with mains present, selected Off-Peak
to restore grid supply, then cleared the plan and restored the baseline.
The C2000 Gen 2 / A1783 reported main **2.1.6.4**, controller **5.0.7.1**,
inverter **5.0.7.0**, BMS **9.3.3.0**, and wireless **3.3.0.0**.

The namespace had no default route. No cloud request, AC-output switch,
output timer, firmware write, or arbitrary register write was sent. AC output
remained enabled in every recorded sample. Waveform continuity and server
transfer behavior were not independently measured.

## Baseline and trial

Baseline: **90% SOC**, battery idle, mains connected, AC output enabled,
**997/997 W** AC input/output, Standard/no tariff/count 0, reserve **10%**,
upper/lower **90/1%**, charging limit **1800 W**, fast charge off, no AC timer.

1. `ap-service-set-tou --mode standard --period peak:0:24` stored one slot without
   activating it. Fresh D9 length was 29, count 1, triplet `01 00 18`.
   Clear to Standard/count 0 was also confirmed.
2. `ap-service-set-reserve --reserve 85` changed only reserve.
3. `ap-service-set-tou --mode time_of_use --period peak:0:24` selected Peak. Three
   discharge samples reported **0 W input**, **900, 963, 982 W output**, SOC 90%.
4. `ap-service-grid --timeout 20` selected wire tariff **3**, named `off_peak` in the
   public API, using an all-day triplet `03 00 18`. Input/output first became
   **965/965 W** approximately **4.84 seconds** after that write; battery idle
   was first observed at **6.89 seconds**. It required three consecutive fresh
   grid samples, cleared Standard/count 0, then required three more.
5. The trial restored reserve 10% and verified every protected setting. No
   charging-power/cap change or charging fallback was needed.

`ap-service-grid` completed roughly 16.5 seconds after the last Peak discharge sample.
Its per-phase timeout excludes transport overhead; this is an observation from
one trial, not a guaranteed recovery deadline.

## Encoding and confirmation

Native `0090`/`0890` uses A1=`22`, timestamp FD, typed A2 mode, zero A3/A4,
A6 count and A7 type `04` followed by tariff/start/end triplets. A7 has **no
embedded count**. Reserve writes use only A5, in addition to A1/FD.

The packaged endpoint validates up to six non-overlapping whole-hour slots,
requires fresh complete D9, readiness prefix `34` before activation, mains
and AC output on, fast charge off, no active AC timer, and reserve between
`lower + 5` and upper. Confirmation preserves output and charging settings.

The flow classifier requires mains/output enabled and a positive output load.
Grid confirmation uses AC input at least output minus 20 W and battery
idle/charging; discharge requires zero AC input and battery discharging.
An acknowledgment or mode field alone is insufficient.

## Independent checks and limitations

A new MQTT connection verified idle/grid samples **353/353, 894/894,
1003/1003 W**. After AP shutdown, a fresh BLE connection verified **313/313,
306/306, 306/306 W**. Both confirmed SOC 90%, Standard/no tariff/count 0,
reserve 10%, caps 90/1%, 1800 W, fast charge off, and AC output on.

This adds a verified Off-Peak recovery to the [earlier corrected Peak trial](c2000-corrected-peak-trial.md).
Multi-slot schedules, timed transitions, reserve-floor transitions, long-term
operation, C1000 equivalence and charging-rate enforcement during Off-Peak
remain untested. A stored/activated plan persists until changed; stopping the
tool is not a plan reset. Failed confirmation can leave changed settings.

## Retained evidence

The private archive contains source snapshot, CLI records, raw BLE packets,
MQTT/API traffic, packet capture, baseline, trial and independent checks.
All outgoing requests were decoded: **52 status reads, 9 TOU/reserve writes,
2 readiness reads**, with no other native commands.

Local directory: `.solix-private/isolated-ap/tou-controls-20260929-2105/`.
Result archive SHA-256:
`55a556e6a82194ce829bcb3b9639301f82a834244a33c4fb8d682c503b86bd31`.
It was copied and hash-verified before remote credentials/artifacts were
removed. Owned services/namespace were removed and the dedicated adapter left
DOWN and addressless; host routes were unchanged. Private directories/files
use 0700/0600. No raw capture or credential belongs in Git.
