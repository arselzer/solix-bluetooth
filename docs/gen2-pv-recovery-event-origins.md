# C1000 Gen 2: origins of the recovery events

## Result

The immediate stored-fault clear in the
[mode-4 recovery handler](gen2-pv-mode4-recovery.md) has a concrete **internal**
command source: register `0015`, word `0059`, becomes event-bank-0 bit 5.
The established main-controller MPPT start/stop helper cannot produce that word.
This investigation has **not established an app-facing fault-clear command**.

No producer for event-bank-0 bit 6—the event that diverts mode 4 into mode 5—was
established by this bounded audit. Nearby command handlers that contain a
bit-6 constant address **event bank 1**, which has different consumers.

Evidence uses the public A1763 / C1000 Gen 2 firmware package for main
**1.1.4.9**. All work was offline; no commands were sent and no runtime controls
were added. Original C1000 and C2000 are outside these binaries' evidence.
DSP addresses below identify 16-bit words; main-MCU addresses identify bytes.

## Internal register `0015`: complete input-domain check

The DSP register receiver previously traced at `085fb1` stores register `0015`
in RAM word `0419`. The selected consumer at `0866bf..0866d0` reads that word
and calls event writer `0898dc` with bank 0.

The new replay executes that consumer for **every 16-bit value**:

| Register word | Result |
| --- | --- |
| `0057` | Bank 0 bit 3: start request. |
| `0058` | Bank 0 bit 4: stop request. |
| `0059` | Bank 0 bit 5: immediate stored-fault clear in the recovery handler. |
| Other 65,533 values | No event from this consumer. |

None produces bank 0 bit 6. This rules out an additional undocumented value in
**this selected register consumer** as its source. The surrounding pending-mask
dispatcher and UART reception are outside this replay.

Two composed cases execute the actual command consumer and recovery handler
with a blocking fault present and the periodic flag clear. In an already active
mode-4 handler, `0059` clears stored `ad17`. If changed-mode initialization runs
first, it discards the queued event and the stored fault remains. Command timing
therefore still matters even for this internal route.

## The normal main-controller helper cannot request that clear

Main function `0802a590` accepts an internal selector and an enable argument.
For selector 2, its actual instructions at `0802a60a..0802a648` implement:

```text
enable == 0: queue internal register 0015, word 0058
enable != 0: queue internal register 0015, word 0057
```

The enable argument is a Boolean test. Passing numerical `0059` as that argument
still produces `0057`; it does not pass through a raw register value. Six
synthetic argument cases execute the real builder, allocation handling and
descriptor construction. Two unsupported selectors are rejected without a
queued request. Allocation, memory services and the final queue are substituted.

Static main-image references place this helper at callback/task call sites
`080164ac`, `080164de`, `08024942` and `08025406`. This bounded trace does not
establish another main-builder path that emits register `0015` word `0059`.
It also does not exclude a generic bridge, indirect dispatch or another
firmware version. An event number, register word and external command opcode
are separate identifiers; matching numbers do not connect those layers.

## Distinguishing the event banks

The exact DCDC image contains **62 direct long-call sites** to event writer
`0898dc`. Every audited local setup includes an explicit bank literal within
the final three words before the call. Seventeen select bank 0:

- Fifteen have fixed event literals, limited to bits 1, 2 or 9. Their setup and
  actual event writer execute in the new replay.
- Consumer call `0866ce` selects bits 3, 4 or 5 from register `0015`, as exhaustively
  checked above.
- Helper call `0871fc` selects bit 3 or 4 from its accepted Boolean argument.

None of those established setups supplies bank 0 bit 6. Separately, the actual
branch at `086531` executes an event-6 setup, but it sets `AL=1`: the real writer
sets **bank 1 bit 6**, leaving bank 0 unchanged. The nearby branch at `0864d3`
also selects bank 1 in the static trace. These are not origins of the event
tested by the MPPT mode-4 handler.

This is a direct-call setup audit, not a complete proof that bank-0 bit 6 is
unreachable. Indirect calls, aliased event-bank writes and other control-flow
entries were not resolved. No physical meaning such as restart, sleep or
shutdown is assigned to the untraced producer.

## What remains before a public control

A usable fault-clear mapping still needs an actual app/main-controller request
that reaches the internal `0015/0059` path, its acceptance conditions, and full
side-effect/readback evidence. Clearing stored `ad17` can bypass the exception
used by timed clearing; the real fault producer may also reassert it. The
internal word is not proposed as an arbitrary BLE or MQTT opcode.

For the preempt event, the missing prerequisite is a proven bank-0 bit-6 producer
and its callers. This audit provides no new live command to try. Existing
brightness-based PV retry remains a separate candidate, with physical locked-PV
validation still outstanding.

## Reproduction

Use the [published offline dependency setup](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-pv-events \
  python3 tools/firmware_analysis/emulate_pv_recovery_event_origins.py
cmp /tmp/solix-pv-events/pv-recovery-event-origins-results.json \
  tools/firmware_analysis/expected_results/pv-recovery-event-origins-results.json
```

The tool enforces the existing main/DCDC image hashes and verifies DSP-record
CRCs. It reports 65,536 command-word executions, 62 direct-call setup records,
15 fixed bank-0 setup replays, 8 main-builder cases, 2 composed command/consumer
cases and 1 bank-1 counterexample. These counts overlap where explicitly noted;
they are not presented as independent physical tests.

DSP function return values are not substituted. RAM, events, fault conditions
and MMIO are synthetic. UART, public authentication/routing, indirect call
provenance, real fault producers, scheduling and physical charging are excluded.
The manifest records input/helper hashes and exact limits. Published artifacts
contain no phone data, device identifiers or credentials.
