# Original C1000: bounded native charging-rate test plan

Proposed follow-up for **A1761 main 1.7.1 / radio 0.3.3.0**, prepared on
2026-10-02. This is a procedure, **not a completed rate experiment**. The
latest original baseline is SOC 100%, input/output 0 W, AC enabled, DC
disabled, Fast off and saved charging power 1000 W. The HP test switch is
disconnected. That idle state cannot validate rate enforcement; no setting
write is needed while waiting for a useful charging interval.

## Established controls and open measurement

Native **`0044`** charging power and **`005e`** Fast both passed setting
readback/restoration on installed 1.7.1. The
[native power trial](c1000-original-mqtt-followup.md#original-setting-builders-and-readback)
used 1000→900→1000 W; the [Fast trial](c1000-native-fast-validation.md)
ran at full SOC. Neither established a sustained physical rate comparison.
The [previous chain tests](c1000-charging-rate-validation.md) could not retain
enough below-full charging headroom. A ceiling below the AC load did not
establish battery-only operation with mains connected.

Use the existing original-model native service and SDK setters, which publish
once and require **two fresh complete status confirmations**, protecting all
eleven preferences and the full 21-byte F8. Original connected firmware may
suppress setter ACKs; a missing ACK is not permission to retry.

## Eligibility before the first write

One controller owns the station connection. Record the device/firmware,
initial settings, both outputs, timers, raw full F8 and an independent upstream
baseline. Preserve all output switches, Smart modes, timers, Wi-Fi and IDs.
Keep other app/HA/policy writers inactive on both stations during the
comparison; per-command SDK locking does not reserve the whole experiment.

Require three complete fresh original reports spanning at least 30 seconds:

- AC remains enabled, DC and Fast remain at their recorded baseline, and
  AC/DC output countdowns are zero. No communication or protection error is
  reported. If AC Smart is enabled, arrange a stable test load that avoids
  its ordinary auto-off; do not change Smart, whose supported setter requires
  AC off. A zero countdown alone does not reset Smart inactivity history.
- Battery SOC is preferably **20–94%**, without a rapid change. At 95–99%, a
  brief exploratory observation may be useful, but saturation can invalidate
  the comparison before its second window.
- Reported AC input is consistently above AC output by at least **100 W**.
  This is an experiment's eligibility margin, not a calibrated battery-power
  measurement. BF=2 is supporting evidence only: the BMS phase is latched.
- The physical AC supply is known connected and independently adequate for
  the chosen normal rate plus simultaneous load. An upstream station's saved
  charging ceiling is not an output-capacity limit.

The original has no verified mains-presence or reserve/tariff setting suitable
for the Gen 2 HA policy. Do not substitute BC, BF or zero A5 as proof of mains,
bypass or an outage. Allow headroom to arise naturally or from a separately
arranged physical test; this procedure sends **no output-switch command**.

## Normal-rate comparison: 200 → 500 → 200 W

With Fast confirmed off, call `set_ac_charging_power(200)` once, then 500 once,
then 200 once. These values stay within the public original SDK domain
100–1000 W in 100 W steps; choosing 200 also avoids depending on the older
[1.5.9 loader's 100 W discrepancy](c1000-saved-charge-validation.md).
No reboot is part of this test.

After each confirmed setter, record fresh reports for at most 60 seconds:
discard the first ten seconds for a proposed settling allowance, then retain
at least five full samples. Compare the median AC input, output and their
difference in all three windows, alongside SOC, BF, Fast, D1 and upstream
input/output. Retain sample times and raw reports privately. The return to
200 W helps distinguish a reversible response from a changing battery stage.
Do not translate input-minus-output into exact battery watts or efficiency.

Abort the comparison if freshness/confirmation fails, protected settings or
AC enablement change, output becomes unexpected, SOC reaches 100%, a fault
appears or the observed charging interval disappears. A flat result during
saturation/source limitation is inconclusive. A reversible watt change is
charging-rate evidence, not proof of a hard total-input limit or bypass stop.

Restore the recorded charging ceiling once after the bounded comparison,
requiring the same full confirmations. If a setter fails after publication,
first obtain independent fresh status; do not blindly retry the command or
start another experiment. If a safe restoration cannot be confirmed, retain
the uncertain state and stop. Finish with three independent complete status
reads matching the whole original/upstream protected baseline.

For an already running private AP service, the normal SDK CLI is:

```sh
solix-link ap-service-set-charge-power \
  --directory /path/to/private/ap-service --name original --watts 200
```

The service must already explicitly allow control; use the same model-specific
setters through HA/API if that controller owns the connection. This document
does not start a second station client or activate an automation.

## Fast is a separate follow-up

Only after a useful normal-rate result, consider Normal→Fast→Normal with the
saved normal ceiling unchanged and an independently adequate physical source.
Lower SOC, preferably below 80%, gives a more useful comparison. The public
[1.5.9 current replay](c1000-fast-current-limits.md) shows Fast can bypass the
saved normal ceiling and request less current near full; it does not validate
1.7.1 rates. Use at most the same 60-second observation windows and restore
the original Fast flag and all settings. Input loss previously cleared Fast:
stop the experiment on an outage, without automatic re-enable or retries.

Physical Fast rates, reboot retention and a supported internal
charging-pause/battery-only control remain open. The
[diagnostic-table audit](c1000-diagnostic-charging-audit.md) supplies narrower
offline limits, not an alternative live control path.
