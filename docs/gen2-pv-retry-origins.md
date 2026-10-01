# C1000 Gen 2: local origins of the solar retry flag

## Finding and scope

There is a concrete controller path from an **existing brightness command** to
the PV weak-light retry mechanism. On **A1763 / C1000 Gen 2 main 1.1.4.9**, writing
the already saved, nonzero brightness level can mark a locked MPPT state for a
retry. It does not require changing an output switch or inventing a command.

This is a firmware-derived effect, verified by **59 new synthetic replay
cases**. It is **not a physically verified PV recovery procedure**. No panel,
station, app or network was accessed, and no recovery API was added. Original
C1000 and C2000 behavior cannot be inferred from this binary.

The [passive weak-light metric](gen2-weak-light-observability.md) provides the
readback needed for a future controlled check. These cases extend the previous
getter/timer investigation by executing the external settings handler, the
low-SOC helper and the downstream retry consumer.

## Known external entry points

| Entry | Actual path | Condition and effect |
| --- | --- | --- |
| Native `0103`, A3 = `01 <level>` | Handler `0800c530` → `0802b490` → `0802af48(1)` | Any nonzero brightness value reaches the user-action helper when locked, including the current level. Public supported levels remain 1–3. |
| Native `0103`, A2 = `01 <on>` | Handler → `08010580` → `0802a1ec`, then `0802af48(1)` | Only an exact 0/1 request that changes the actual display state reaches this path. Writing the current display state does not. |
| Other tested settings | Same `0103` handler | Temperature, off-grid alert, Device Timeout and an empty settings request do not set the flag in the tested conditions. |

The command table entry at `08032e78` associates `0103` with `0800c530`. The
brightness setter stores the nonzero level at `20001d6c`, requests persistence
and marks a display event **even if the saved level already equals the request**.
It skips the separate LCD-data-change notification in that same-value case.

The replay covers source bytes `21` and `22` through the actual controller
parser/handler. It does not replay radio authentication or transport. The
existing native brightness API already sends same-value requests and protects
saved settings/output states; that API's ordinary brightness operation was
[previously live validated](c1000-native-preferences-validation.md). No earlier
brightness trial established PV recovery from a locked physical state.

## User action becomes a retry request

`0802af48(1)` sets bit 3 of `2000039a` and calls `08011730(1)`. It does not itself
clear the reported lock at bit 1. The weak-light timer callback `08030418` later
sees bit 3, calls the internal mode-0 clear function and sets bit 2 through
`0802af38(1)`.

Under the tested global state, the sequence is:

```text
locked:       lock=1, user_action=0, retry=0
brightness:   lock=1, user_action=1, retry=0
timer:        lock=0, user_action=0, retry=1
```

For saved levels 1/2/3 and synthetic SOC 2/50/100, this path preserves the entire
saved settings block and the controller output-state word. It still requests
persistence and display activity. Timer scheduling and actual display delivery
are substituted; the test manually invokes the callback. Existing global
guards can defer or suppress the clear path, so neither its completion time
nor unconditional success is established.

### Retry has an actual consumer

Initialization at `0800ecf8` registers the second input-debounce record with:

- Active predicate `08025ffc`: clears retry bit 2, then reads module bit 5 at
  `20003f4c`.
- Inactive predicate `08026078`: returns true if retry bit 2 is set, otherwise
  tests the inverse of that module bit.
- State callback `0802601c`: updates a derived input flag and marks an event.

The actual debouncer at `080254e4` uses threshold byte 200 for this record. With
the module bit held high and the record initially active, a retry request leads
to an inactive transition on callback **202**, consumes the retry bit on the
next active-predicate evaluation, and returns active on callback **404**. With
no retry request and the same module bit, it remains active. Eight sequences
cover both initial states, both module-bit values and both retry values.

These are invocation counts, not measured time. The registration's period
argument is 10, but scheduler delivery and real hardware timing were not
executed. The producer of the module flag and downstream MPPT power actions
were also excluded. This proves that the flag can make the controller cycle an
input state; it does not prove that a panel will supply power afterward.

## Low-SOC side effects matter

The real helper `08011730` takes an additional branch at **SOC ≤1**. Replaying
SOC 0/1/2 shows that the low branch:

1. Calls `0800e2ec`, which restarts three timer-service boundaries when their
   configured IDs are present.
2. Calls `0802bc84(0)`, which clears masks `0800` and `0004` in the peripheral
   register at `40010400` through `080176b8`.

The register writes execute against synthetic MMIO; the timer services are
recorded substitutes. This is not evidence of an AC-output toggle. The complete
physical effects of those timer/peripheral changes were not established, so
the brightness-based retry candidate should not be presented as safe at an
unknown or near-empty SOC. At synthetic SOC 2/50/100 this additional branch was
not entered.

## Bounded next validation

A useful future test requires an already connected suitable PV source, a
naturally observed `pv_weak_light_locked=1`, fresh SOC comfortably above 1%, and
complete settings/output readback. Retain the existing brightness guard:
Standard mode, no active tariff and no active clock-screen transfer/schedule.

One write of the existing nonzero brightness value is the candidate. Observe
the lock, input state and power afterward, while confirming saved configuration
and output states. Allow display activity to settle. Do not repeatedly write
brightness, toggle outputs, force internal flags, or treat a cleared lock as
proof of resumed charging. A zero-lock baseline cannot validate this effect.

No new user action is required now; the current SDK remains a brightness API.
An automatic recovery feature would need a locked-state physical trial,
side-effect confirmation and explicit error/timeout handling first.

## Reproduction

Use the [existing offline firmware/dependency setup](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-pv-retry \
  python3 tools/firmware_analysis/emulate_pv_retry_origins.py
cmp /tmp/solix-pv-retry/pv-retry-origins-results.json \
  tools/firmware_analysis/expected_results/pv-retry-origins-results.json
```

Cases: **36 same-brightness command chains, 8 display-switch cases, 4 unrelated
command checks, 3 low-SOC boundaries, 8 retry-consumer sequences**. Actual ARM
parser, settings handler, setters, helper, weak-light timer, telemetry getter
and selected debounce record execute. LCD delivery/off callback, settings
persistence, ACK/refresh transport, backup registers, logging, memory copy/clear
and timer scheduling are substituted. Five unrelated debounce records are
disabled to isolate the traced record. No app, radio transport, charging
actuator or physical PV producer executes.

The tool checks input `MainMcu-decoded.bin` against SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
The accompanying manifest records helper hashes, runtime versions, result hash
and substitutions. All published inputs/results are synthetic or derive from
the already published firmware; no phone data or credentials are included.
