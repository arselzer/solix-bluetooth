# C1000 Gen 2: DSP startup timing and fault exits

## Finding

The DCDC controller's mode-1 path does not start conversion merely because a
start request was accepted. It waits for two periodic conditions, accumulates
100 qualifying invocations, requires a run-request value of exactly 1, and then
issues two protected peripheral write intents before entering mode 2.

A fault found in this continuation **exits to mode 4 and clears the request**.
It does more than reset the counter. Clearing that fault is therefore not yet
proof that charging resumes automatically.

These findings extend the [previous MPPT command trace](gen2-pv-retry-actuation.md)
with **60 new offline replay cases**. The input is the published DCDC component
from **A1763 / C1000 Gen 2 main firmware 1.1.4.9**. No station was accessed.
Original C1000 and C2000 behavior are outside this evidence.

All DSP addresses below are **16-bit word addresses**. RAM and peripheral reads
are synthetic, and recorded peripheral writes are not physical measurements.

## Continuation after a start or stop request

The replay executes actual instructions at `087464..0874a1`, including the
periodic getter `089528` and fault getter `08919b`.

| Check or transition | Exact behavior |
| --- | --- |
| Event word bit 0 is clear | Return without advancing this continuation. Other nonzero event bits do not substitute for bit 0. |
| `aaae.bit6` is clear | Return without advancing the counter or checking `ad17` in this continuation. |
| Both periodic conditions hold and `ad17 != 0` | Clear counter `acf3`, set mode `acea=4`, clear request `acf1`, and record previous mode 1 in `aceb`. |
| Both conditions hold and `ad17 == 0` | Advance counter `acf3`, saturating at 100 for the tested normal range. |
| Counter reaches 100 but request differs from 1 | Keep mode 1 and counter 100. Values 0 and 2 both block startup. |
| Counter reaches 100 and request equals 1 | Issue the peripheral writes below, clear the counter, set mode 2 and save previous mode 1. |

The fault branch `08748c → 087494` bypasses the cleanup call and reason assignment
used by the higher-priority event branch. In this fragment it leaves the previous
reason word `acef` unchanged. This is a code-path distinction, not proof that
cleanup is absent from the full state machine.

### The wait can accumulate before the request

The counter advances even while the run request is 0. A sequence of 120
qualifying invocations without a request leaves it at 100. Setting the request
to 1 then reaches mode 2 on the **next qualifying invocation**. Consequently,
“100 ticks after the command” is not an accurate universal description.

Other sequence cases establish:

- Starting from counter 0 with request 1 takes 100 qualifying invocations.
- Fifty invocations with either periodic condition missing add nothing.
- A fault after 60 qualifying invocations resets the counter and exits mode 1;
  the replay stops there rather than inventing a return to mode 1.

These counts have **no established wall-clock unit**. They do not specify a
physical retry duration or a timeout suitable for an automatic recovery API.

## Where the periodic pulse comes from

Getter `089528` returns `aaae.bit6`. The timer routine's actual tail at
`0894a5..0894b4` clears that bit on every invocation. When its upstream
quotient-change indicator is true, it increments divider counter `aabb`; the
fifth such change resets that counter and sets bit 6 for that invocation.

Eight cases execute this tail with different divider values and both states of
the quotient-change indicator. Static preceding code compares a division-by-100
result with its saved value. The replay begins **after** that comparison: the
timer source, interrupt frequency, dispatcher ordering and possibility of
missing a pulse are not executed. No seconds or milliseconds are inferred.

## What the peripheral writes establish

The successful path at `087479..087482` executes:

```text
EALLOW
word[4097] |= 0004
word[4197] |= 0004
EDIS
```

The replay records both write values and confirms that the protected-access
flag surrounds them. It then observes the actual mode-2 assignment at `087487`.
This closes the prior trace's gap between a stored run request and attempted
peripheral action.

The harness models these addresses as ordinary synthetic words. It does not
model register side effects, output polarity, converter hardware or subsequent
mode-2 regulation. The writes are therefore evidence of firmware intent, not
proof that switching starts or PV/battery power becomes positive.

## Event priority, stop behavior and fault scope

Additional cases start at the selected-event fragment `08744c`, then execute
the continuation and actual cleanup callees where reached:

| Event bits | Observed effect in mode 1 |
| --- | --- |
| Bit 6 | Preempts start/stop processing, executes cleanup, selects mode 6/reason 4, clears request. |
| Bit 1 without bit 6 | Preempts start/stop processing, executes cleanup, selects mode 4/reason 12, clears request. |
| Start bit 3 | Sets the request if the previously established fault predicate accepts it. A counter already at 99 can reach mode 2 in the same invocation. |
| Stop bit 4 | Clears the request. At the threshold, mode remains 1 and the counter can remain 100. |
| Start and stop together | Stop wins; no startup writes occur. |
| Bit 2 alone | Executes cleanup but does not itself clear the request or skip the continuation. With an already accepted request and satisfied timing conditions, startup writes can still follow in the same invocation. |

The physical origins of bits 1, 2 and 6 are not established here. They must not
be exposed as commands or described as interchangeable stop signals.

Cleanup `08730a` and its actual callees `084528 → 08688c` and `08976f` execute
without function-return substitutes. The observed effects include clearing
`ab40.bit0`, status `a49e.bit4`, low bits of `acf0`, and several `abbf` bits;
clearing `aced`; and OR-write intents to peripheral word `7f05`. Interrupt-mask
instructions surround those effects. Physical interrupt or peripheral behavior
is excluded.

Start acceptance checks `ad17` plus selected `ad16` bits, as established in the
previous replay. This continuation rereads only `ad17`. Four deliberate fixtures
with an **already latched** request and nonzero `ad16` still reach its startup
branch. That does not demonstrate a real fault bypass: fault producers,
higher-priority events and the full dispatcher are excluded. It is a reason to
avoid forcing internal request flags as a recovery technique.

## Remaining prerequisite

No automatic PV recovery feature is justified yet. The next offline question is
how mode 4 returns to an eligible startup state, including fault/event producers.
Physical validation still needs a naturally locked, suitable PV input and
complete baseline/readback using the existing brightness command's guards.
Clearing a lock, setting a request, entering mode 2 and delivering charging power
are distinct observations.

## Reproduction and limits

Use the [published offline dependency setup](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-pv-continuation \
  python3 tools/firmware_analysis/emulate_pv_start_continuation.py
cmp /tmp/solix-pv-continuation/pv-start-continuation-results.json \
  tools/firmware_analysis/expected_results/pv-start-continuation-results.json
```

The tool checks `dspDCDC-decoded.bin` against SHA-256
`7ae2bb915812441b8316d1c3f3efc2379fcb488d4d03e950901cf507c224ceb8`
and verifies every container record's CRC. It extends the earlier deliberately
limited C28x interpreter in a new file; existing tools are unchanged. Unknown
instructions fail. Its inherited fault predicate executes actual instructions;
no function return values are substituted.

The manifest records helper hashes, runtime versions, exact entry points and
exclusions. Counts are **8 periodic guards, 15 counter boundaries, 6 fault exits,
4 second-fault-word scope checks, 6 priority events, 8 start/stop/event-2 cases,
8 periodic-divider cases and 5 multi-invocation sequences**.

Synthetic inputs replace RAM, peripheral state, selected events, periodic edges
and the initial mode. Event retrieval, the empty-event entry prologue, the full
dispatcher, real timing, fault producers, mode-2 regulation and physical power
are excluded. Published artifacts contain no captures, identifiers or credentials.
