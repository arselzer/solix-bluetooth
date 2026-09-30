# C1000 Gen 2: charging limits, reserve and reconnect

## Scope and baseline

Tests on **2026-09-30** used A1763 main **1.1.4.9**, radio **0.3.3.0**, an
isolated AP on the HA node, and the C1000 chain's noncritical test load. The
C2000 was untouched. No Anker request or internet route was used.

Baseline: AC on, mains connected, DC off, Standard/no tariff, reserve 10%,
charge bounds 100%/1%, charging power 1200 W, fast charge off, Device Timeout
Never, port memory on, display timeout 30 seconds, brightness 1, Celsius and
off-grid alert off. Load was about 180 W. Commands omitted AC-output switch
fields. Fresh native snapshots and independent Bluetooth checked restoration.
Telemetry does not measure inverter waveform continuity.

## Native fast charge

The packaged MQTT setter confirmed off→on→off at full SOC and below full SOC.
Two fresh readbacks and a later three-second sample confirmed retention. Full
A4/D9 configuration and AC/DC/mains states were protected. This establishes
the flag, not a calibrated fast-charge rate.

Fast charge can wake the LCD: runtime A4[22] changed 0→1, then returned to zero
after the configured timeout. This is allowed for fast/power writes while
saved brightness A4[18], timeout and port memory remain exact. A4[18] is now
named `display_brightness`; the former `display_mode` label was incorrect.
Enabling fast charge requires Standard/no active tariff and fresh mains.

## Discharge to a reserve floor

With reserve 95% and all-day Peak, SOC fell **100→95%** over approximately
20 minutes. Mains remained present; AC input was zero, output about 180 W,
and telemetry reported battery discharge. At 95%, supply returned to grid
while Peak remained active. Twelve samples over 60 seconds held SOC 95%,
idle battery and roughly equal input/output power.

This observes a non-full reserve transition and hold, not long-term hysteresis
or an independently measured SOC. Standard/no slots and reserve 10% were
restored. Charging below the reserve, larger loads and other firmware remain
separate tests.

## Lower charging limits

At SOC 95%, fast off and Standard mode, a native **100 W** limit persisted
through 18 samples over 90 seconds; SOC rose to 96% and battery status stayed
charging. After one 1221 W transition sample, input stabilized at **290–293 W**
with output **179–181 W**. The roughly 110 W difference includes charging and
operating/conversion overhead; input power includes the passthrough load.
It is not a calibrated battery-side measurement.

The preceding 200 W write persisted but the first private confirmation guard
stopped on runtime LCD wake. Only charge-power bytes and A4[22] changed; the
1200 W baseline was restored. The public helper now protects full fresh
configuration while accepting that runtime change. The library's C1000 Gen 2
range is **100–1200 W**, in 100 W steps; C2000 remains **300–1800 W**.
Zero is not a charging-pause command, and a limit below load does not by itself
establish battery-only supply with mains connected.

The follow-up used the **public setters**. Prime readback confirmed
1200→200→100→1200 W with full protected configuration. Native charging then
held 200 W for 90 seconds below full SOC, reporting **389–390 W input** and
**180–181 W output**; SOC rose 97→98%. At 100 W, another 90 seconds reported
**291–292 W input** after one 389 W transition sample, with output 180–181 W
and charging status retained. Both rates were exercised in Off-Peak as well
as the earlier Standard 100 W trial. These are station telemetry readings,
not independent wall-meter or battery-current measurements.

## Cached-profile reconnect and energy

Restarting the same isolated AP without Bluetooth, provisioning or a button
press reconnected the station in **13.52 seconds**. A read-only 720-second
capture obtained 140 complete fresh snapshots with baseline settings intact.
This checks network recovery under load with Device Timeout Never; it does
not test idle sleep or a power cycle.

Two cumulative energy reports differed by 19 raw AC input/output units over
370 radio-timestamp seconds. Bracketed status samples integrate to 18.38845 Wh.
This is compatible with nominal Wh accounting and integer report rounding;
physical calibration remains unverified. See [energy analysis](gen2-energy-counter-investigation.md).

## Clock caveat and retained evidence

The first partial-hour trial selected Off-Peak before its expected UTC hour;
it did not prove a boundary transition. [Firmware replay](gen2-schedule-clock-audit.md)
shows why a UTC profile can retain an earlier local offset while FE timestamps
remain correct. All-day reserve tests are independent of this ambiguity.

The follow-up explicitly configured **Europe/Vienna**, currently UTC+2, and
stored Peak 00–18 / Off-Peak 18–24. From 15:45 UTC, telemetry showed Peak and
battery supply. The first post-boundary sample at **16:00:01.506 UTC** showed
Off-Peak/transitioning; **16:00:06.559** confirmed grid supply and charging.
AC remained enabled. This is an observed natural local-hour transition,
without changing the host clock or substituting NTP time. Polling was about
five seconds; no subsecond switching precision is established.

The helper now rejects activating hourly C1000 plans with a currently
zero-offset timezone; inactive storage and all-day tariffs remain available.
After the trial, three native snapshots and independent Bluetooth confirmed
Standard/no slots, reserve 10%, power 1200 W and all protected baseline
settings. Final SOC was 99% and charging. The local API served reporting flag
`20001=0`; owned AP processes/namespaces were removed and the Wi-Fi adapter
was addressless with no default route.

Private captures retain the failed attempts, raw traffic, scripts, baselines,
readbacks and final audits under the `c1000-native-extended*`,
`c1000-native-reconnect-energy-*` and `c1000-native-low-power-boundary-*`
directories in `.solix-private/`. Public documentation contains sanitized
observations only. No firmware was installed.
