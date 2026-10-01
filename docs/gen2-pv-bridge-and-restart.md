# Gen 2 PV: bridge limits and main-controller restart

Offline investigation dated 2026-10-01, using public **A1763 C1000 Gen 2
main 1.1.4.9** and its DCDC payload. No device or network was accessed. This
does not establish original C1000 or C2000 behavior.

This follows [DSP recovery](gen2-pv-mode4-recovery.md) and
[recovery event origins](gen2-pv-recovery-event-origins.md). The useful new
result is a main-controller retry and communication-failure path. **No public
fault-clear command or physical PV recovery procedure is established.**

## No proven external route to the immediate clear event

The prior DSP replay established internal register `0015` word `0059` →
event bank 0 bit 5. That is an internal register operation, not an app opcode.

The present audit found and reviewed all **23 aligned direct Thumb BL calls**
to internal descriptor queue `08027e40`. The only reviewed descriptor selecting
register `0015` is builder `0802a590`, whose existing replay establishes
`0057` for nonzero enable and `0058` for zero. The other sites select fixed
registers, except one builder selecting `(argument << 12) & ffff`; that
expression cannot select `0015`. The result JSON lists each call site and its
register expression. No literal Thumb pointer `08027e41` occurs in the image.

This narrows the known direct routes. It is **not** complete indirect-call,
memory-alias, diagnostic-tunnel or radio-forwarding analysis. The separate
radio function 10 Modbus bridge forwards `0067`, which is absent from this
main firmware's recovered function 10 table; ordinary app `0066` retrieves
saved logs. Neither route establishes arbitrary DCDC-register access.

For unresolved event bank 0 bit 6, all **62** occurrences of event-writer
address low word `98dc` in the decoded DCDC payload are operands of direct
`LCR 0898dc` instructions. There is no additional literal materialization of
that address in this search. Computed pointers, different entry points and
aliased bank writes remain possible. This does not upgrade the prior direct
producer audit into an absence proof or justify probing numeric commands.

## Running feedback drives a second configuration attempt

Main task `08025140` reads global DC-input-active bit 0 at `20000164`.
Its state starts at `20000010`:

| Offset | Observed role |
|---|---|
| `+1` | Initial configuration successfully queued |
| `+2` | A stop command may be needed when input becomes inactive |
| `+3` | Sampling counter |
| `+4` | Sampling timer ID |

When active and `+1` is clear, the task builds DCDC configuration register
`0016` with callback `0801649c`. Successful enqueue sets `+1`; **`+2` is set
even if allocation or enqueue fails** (`08025202..08025206`). Allocation or
enqueue failure therefore permits another configuration attempt on the next
active task invocation, independent of the sampling timer.

A nonzero completion calls the start/stop builder according to the **current**
global input state. Zero completion does nothing. Enqueuing the later start
can itself fail, and that callback does not clear the configuration latch.

The task installs a single-shot sampling timer with period argument **1000**
at `080252de..080252f4`. On each expiry it queues a separate two-byte update
to register `0016`, refreshes cached power and restarts the timer. This update
is a write of the configuration's first word, **not a status poll**.

After the sample counter exceeds 10 (`08025358`), cached register `0126.bit4`
at `20003f4c` determines the next action:

- Clear: reset the configuration latch and counter, restart the timer and mark
  another internal pending flag. The next active invocation queues full
  configuration again. Firmware logs this as MPPT opening failed/restart.
- Set: retain the configuration latch. With the independently stored
  weak-light state inactive, the counter settles at 30 and no full
  configuration is requeued by this branch.

The replay starts with a running timer at tick 0: no restart at tick 10000,
latch reset at 11000, and configuration requeued on the next invocation.
It exercises allocation failure, queue rejection, negative completion,
missing completion and rejection of the later `0057` enqueue. These are
**synthetic timer ticks**, not measured elapsed seconds or real UART failures.
The separate weak-light-lock branch and scheduler cadence are excluded.

This explains a plausible continuation after DSP mode 4→1 recovery: once
main's input policy allows activity again, an absent running indication can
cause configuration and a fresh start request. It does not prove that a real
panel starts charging, nor that cached running feedback is current.

## Repeated status failures clear the DSP cache

Bulk-status request builder `08016600` installs callback `0801b2d4`.
Its communication state starts at `200003e8`:

| Offset | Observed role |
|---|---|
| `+1` | Communication-failure flag |
| `+2` | Completion counter |
| `+3` | A successful status completion has occurred |

A nonzero completion clears failure state, resets the counter, sets the
received-status flag, then increments the counter to **1**. Each later zero
completion increments it. When it exceeds 6, the callback:

1. Clears **all 146 bytes** at `20003f00`, including `0126` running/ready bits.
2. Sets communication failure and clears the received-status flag.
3. Requests error code 25 and display refresh argument 10.

An error request is not a guarantee that public telemetry will report
`controller_error_code=25`: other alarms and priority selection may
determine the reported code. A cleared cache is likewise **not proof of
physical mains loss, AC-output interruption, fault clearing, or an energy
transfer**. These cases preserve the main input/output word; actual physical
outputs and subsequent alarm-policy effects are outside the replay.

Consequently **six failed completions after a success** clear that cache,
not seven. A subsequent success resets failure tracking. Before the first
success, each failed callback resets the count before incrementing; it also
clears cache word `013b.bit7`. That branch does not reach the bulk clear.

The replay calls this exact callback but substitutes error/display delivery.
It does not execute the UART worker or status parser. The later
[45-case worker audit](gen2-uart-request-worker.md) shows that unrelated or
rejected replies can discard a pending request **without invoking its callback**.
Matching short or different-selector replies can invoke success without a
complete DSP-cache refresh. Six failures here therefore means six delivered
failure callbacks, not six missed packets or a bound on cache age. Physical
UART timing and main event policy reacting to the zeroed cache remain untested.

## A separate parameter-update retry detail

Callback `08016570` handles the alternate configuration-update path, with a
different dirty flag. On failure it increments an 8-bit counter, marks the
update dirty if the result is below 10, **then immediately resets the counter**.
Starting from zero, repeated failures therefore do not accumulate toward ten
in that path. The replay checks initial 0/1/8/9/10 and wrap boundaries; it
does not reinterpret this as a bounded ten-attempt policy.

## Reproduction and limits

Run with the analysis dependencies installed:

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/pv-bridge-restart \
  python3 tools/firmware_analysis/emulate_pv_bridge_restart.py
```

The script enforces the published main and DCDC SHA-256 hashes. An optional
`SOLIX_FIRMWARE_DIR` selects another directory containing those exact files.
Expected results and manifest are in
`tools/firmware_analysis/expected_results/pv-bridge-restart-*.json`.

**47 cases:** 16 timer/feedback, 5 queue/completion failures, 12 bulk-status
callback cases, 14 parameter-completion boundaries. The actual ARM task,
software timer service/query/start/restart, RAM tick getter, configuration
builders and selected callbacks execute in Unicorn. Every case asserts saved
settings and the global input/output word remain unchanged.

Allocation/free, queue acceptance/delivery, logging, diagnostic collection,
error/display delivery and memcpy/memset are substitutes. The timer table
contains only the relevant synthetic timer. DSP hardware, queue-worker
retries, physical faults, asynchronous scheduling and wall-clock time are
not simulated. The queue-address audit is static, distinct from those 47
execution cases. No recovery control is added to the SDK.

The remaining prerequisite for an external clear/recovery feature is an
actual transport-and-handler path with its guards and side effects, followed
by a controlled physical PV-state trial. The internal word `0059` and event
IDs alone do not supply that prerequisite.
