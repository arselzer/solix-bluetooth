# Original C1000: charging-limit behavior in the test chain

Live investigation on **2026-10-01**, original **A1761 main 1.7.1 / radio
0.3.3.0**, supplied by **C1000 Gen 2 main 1.1.4.9**. The original AC output
powered the expendable HP switch. C2000 was neither connected nor changed.
These tests used the public Prime Bluetooth charging-power setter, not native
MQTT or Fast. Fast stayed off.
Power values below are reported station telemetry, without independent meter
calibration or measured conversion efficiency.

## Baselines and protected readback

Original baseline: AC on, DC off, charging ceiling **1000 W**, full SOC,
zero output countdowns and eleven protected preferences plus the whole F8.
Upstream baseline: mains connected, AC on, **1200 W** charging ceiling,
Standard mode, no Time-of-Use slots and seventeen protected settings plus
complete D9/A4, allowing only the dynamic LCD-activity byte to vary.

Only the upstream Gen 2 AC output was briefly switched during discharge
preparation. The original AC output remained enabled in every complete
snapshot. This is telemetry evidence, not a waveform-level continuity test.
Settings were restored to each recorded baseline; the tests never wrote an
original AC-output switch, reserve, tariff, Fast flag or BMS-state field.

## Preparatory attempts

| Attempt | Complete snapshots | Setting writes | Observation |
|---|---:|---:|---|
| Short outage, then 100/300 W comparison | 24 | 5 | SOC remained 100%; no charging-rate conclusion |
| Bounded longer outage | 25 | 4 | SOC remained 100% through the outage; aborted comparison and restored settings |
| Independent later read | 4 | 0 | SOC 99%, BF=2, input 737–738 W versus output 115–121 W; both AC outputs on |
| Opportunistic headroom check | 4 | 0 | SOC had returned to 100%; stopped before any write |
| Keep 100 W through longer outage/reconnection | 37 | 4 | SOC remained 100%; no sustained below-full charging indication, so 300 W comparison withheld |
| Read-only wait for normal recharge | 31 | 0 | No eligible charging window within 120 seconds; no setter sent |

The short outage lasted approximately **105 seconds between output requests**.
During six 100 W samples, original input/output matched around 115–116 W
after an initial zero-input report; during six 300 W samples both stayed
116 W. Stored limits and restoration passed, but full SOC prevented a useful
charging comparison.

The longer preparation stopped at the six-minute check, with approximately
**388 seconds between upstream off/on attempts**, including confirmation and
charging-ceiling restoration. Eighteen outage samples still reported SOC 100%,
zero input and 115–121 W output. A later independent read established below-full
charging only after supply restoration. This observation does not identify why
SOC changed later or establish an instantaneous battery-flow meaning for BF.

The final preparation kept 100 W across approximately **371 seconds between
upstream off/on requests**. Its twelve subsequent 100 W samples still showed
SOC 100% and BF=1: the initial input was zero, then input/output matched
115–116 W. It restored 1000 W without sending 300 W. The following 120-second
read-only wait found no SOC 95–99/BF=2/input-above-output combination and sent
no command. Thus this round does **not** validate charging-rate enforcement.

Across the six stages, **125 complete snapshots and 13 setting writes** are
retained: seven original charging-ceiling writes and six upstream AC writes.
Every stage ended with protected preferences and full F8/D9/A4 restoration,
with no restoration errors. Final original input/output were both 115 W;
upstream input/output were both 180 W, mains connected, AC enabled, Standard.
Both charging ceilings were restored to 1000/1200 W and Fast remained off.

Setting the original ceiling to 100 W below its roughly 115 W AC load did
not establish forced battery supply with mains present. The saved charging
preference should not be treated as a hard total-input ceiling or a discharge
command on the basis of these observations. A useful next rate test needs a
stable, naturally below-full battery, independent input/output measurement and
both ceilings restored afterward; another brief full-battery outage alone is
insufficient evidence.

## Scope

The [older controller-to-DSP replay](c1000-fast-current-limits.md) distinguishes
normal charging allowance from Fast and from simultaneous output load. Its
saved-watts ×0.91 arithmetic is not a measured efficiency or a total mains
ceiling. The retained live captures must be interpreted using fresh SOC,
actual input/output readings and charging phase; a saved D1 change alone does
not demonstrate charging-rate enforcement or battery-only operation.

Owner-only BLE notifications, negotiated session material, scripts, baselines,
failed preconditions, write journals and restoration checks remain in ignored
`.solix-private/charging-rate-20261001/`. Public records contain observations
without device/account identities. No new force-discharge control is exposed.
Complete private archive SHA-256:
`f5b8c0a34231759f2e3bbf214b0f6febc2da15726e123ac3cef23cf2b57dfdc4`.
