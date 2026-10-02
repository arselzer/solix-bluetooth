# C1000 Gen 2 native AC countdown validation

## Live result

On 2026-10-02, the noncritical **A1763 C1000 Gen 2 main 1.1.4.9** passed a
bounded native MQTT timer trial on the shared isolated AP. The saved baseline
had AC on, DC off, Smart off, Standard mode and both countdowns zero.

| Step | Fresh AC remaining seconds | AC enabled |
| --- | --- | --- |
| Baseline | 0 | 1 |
| Arm ten minutes | 600 | 1 |
| Ten-second observation | 600, 598, 598, 594, 594, 594 | 1 throughout |
| Cancel early | 0 | 1 |
| Three later reports over 15 seconds | 0 | 1 throughout |

The wire audit found exactly two setting requests: native `0101`, A3 values
`03 58 02 00 00` and `03 00 00 00 00`. Both included source A1=`22` and the
native millisecond timestamp. Neither included output-switch A2, charging power,
frequency, Smart or Fast fields. Original C1000 and C2000 received only status
requests; their reported AC states and protected settings were unchanged.
Complete first/final A4 matched after allowing its normal display-activity flag;
D9 preferences matched excluding its leading runtime selector bytes.

All three stations subsequently reported fresh telemetry and AC enabled. Raw
requests, reports, baseline, restoration evidence and service backups are retained
privately. No account identity, credentials or phone capture are published.

## Library and operator CLI

`NativeMqttCommands.ac_countdown()` builds the request;
`LocalMqttServer.set_ac_countdown()` confirms it under the existing control lock.
The running AP worker must already allow controls. Select the station explicitly:

```sh
solix-link ap-service-set-ac-countdown \
  --directory /path/to/ap-service --name test-c1000-gen2 --seconds 600
solix-link ap-service-set-ac-countdown \
  --directory /path/to/ap-service --name test-c1000-gen2 --seconds 0
```

Only C1000 Gen 2 main **1.1.4.9** is accepted. Positive values require fresh
complete status, AC on, AC Smart off, Standard/no active tariff and both timers
inactive. The SDK permits zero or **600–86400 integer seconds**; only 600 and
zero were tested live. This range is an operator restriction, not a verified
firmware range. Cancellation can be attempted while a timer is active.

Two correlated fresh reports must confirm the write and protect other A4/D9
settings, mains presence and outputs. An uncertain ACK, unexpected timer
progression or changed setting fails without a retry or automatic rollback.
The private Unix socket exposes the command; HTTP, HA and advertised gateway
capabilities do not. C2000 and other models are rejected before device I/O.

## Limits and next trial

This validates timer storage, short progression and early cancellation, **not
physical expiry, relay continuity or a full 600-second duration**. Output telemetry
is logical state. No independent electrical measurement or display comparison
was collected in this trial.

The [98-case lifecycle replay](gen2-ac-countdown-roundtrip.md) preserves all
415 saved bytes in its normal sequences, but cancellation changes hidden
checkpoint state and active sampling can change Smart history. Zero cannot
revoke an already queued stop, and a zero status report cannot prove its absence.
Cancel well before expiry. A deliberate expiry needs a confirmed expendable
load, independent output observation and an explicit restoration plan. It must
never be tested on the C2000 powering servers.
