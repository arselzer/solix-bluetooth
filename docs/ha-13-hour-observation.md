# Completed three-station gateway observation

## Result

The bounded read-only observation started on October 2 and its completed
capture was reviewed on **2026-10-03**. It contains **1,560 scheduled samples
over 13 hours**, at 30-second intervals, from the authenticated local gateway.
The observer sent no commands or recovery requests.

| Station | Returned snapshots | Empty/unavailable caches | Maximum age of a reported sample | Nonempty settings changes |
| --- | ---: | ---: | ---: | --- |
| C2000 Gen 2 / main 2.1.6.4 | 1,559 | 3 | 5.231 s | None |
| Original C1000 / main 1.7.1 | 1,559 | 3 | 5.394 s | None |
| C1000 Gen 2 / main 1.1.4.9 | 1,559 | 3 | 9.457 s | One planned AC-countdown sample |

There was **one HTTP `URLError`**, 3.413 seconds after the recorded native-RSSI
runtime upgrade. Each station's three empty-cache readings occurred 4.854,
16.352 and 20.755 seconds after recorded runtime upgrades. These are observed
maintenance correlations; empty caches do not establish changed device settings.
The one nonempty changed-settings reading matches the
[early-cancelled countdown trial](c1000-gen2-native-ac-countdown-validation.md).
All populated reports retained AC enabled. No unexpected protected-setting
change was found.

A separate read-only check on October 3 confirmed fresh telemetry for all three
stations, AC enabled and the recorded protected settings restored. Native
capability counts remained **5 / 9 / 16**, respectively. No station setting,
identity, firmware or charging automation was changed during this review.

## Limits and retained evidence

This measures sampled gateway availability. It does not prove electrical
continuity, capture every connection event between samples, validate whole-host
or station power-cycle recovery, or establish days of idle availability.
The stations were already provisioned; this is not an account-free setup test.

Complete samples, interventions, input hashes and the repeatable analysis helper
remain in restricted, ignored `.solix-private/runtime-followup-20261003/`.
Analysis uses the observer's actual `error_type` field, separates empty caches
from nonempty configuration changes, and checks totals against its final summary.
No raw station identities, credentials or telemetry journals are published.

Next useful reliability checks are an idle multi-day observation and controlled
host/adapter recovery on noncritical hardware. C2000 output switching remains
excluded.
