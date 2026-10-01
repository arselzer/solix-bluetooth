# C1000 Gen 2: DSP recovery from mode 4

## Result and scope

The DCDC controller has an internal return path from mode 4 to mode 1:
**200 qualifying observations with fault word `ad17` clear**. For a nonzero
`ad17` without bit 5, a separate counter can clear that stored fault after
**300 consecutive qualifying fault observations**. Neither count is a measured
duration.

Returning to mode 1 does not start conversion. Mode entry clears pending events,
and the old run request has been cleared. A new start must reach the DSP after
mode-1 initialization. The main controller's previously traced input-rise path
could supply that request; this is not evidence that a person must resend it.

These are **73 new offline instruction-replay cases** for the published DCDC
component from **A1763 / C1000 Gen 2 main 1.1.4.9**. They extend the
[mode-1 fault-exit investigation](gen2-pv-start-continuation.md). No device,
network or physical power path was accessed. Original C1000 and C2000 behavior
remain outside this binary's evidence.

All addresses below are DSP **16-bit word addresses**.

## Actual dispatcher and entry behavior

Dispatcher `0874d5` sends modes 4 and 6 to handler `08736b`. When the current
mode differs from the last dispatched mode at `acec`, it records the new mode
and clears event bank 0 through `089909` before invoking the handler.

The real event getter `0898ed` returns the pending 32-bit word and replaces the
bank with 1. Thus the first cleared-bank invocation runs initialization; later
passes ordinarily have event bit 0 available even without a newly injected
command. The separate timer flag still controls counter advancement.

On the zero-event entry path, the handler:

- Clears healthy counter `acf7`, fault-retry counter `acf8` and run request
  `acf1`.
- Executes cleanup `08730a` and its actual callees.
- Sets reason word `acef` to 9.

The fixture executes this dispatcher, getter and entry code. It does not
substitute their results. Starts already queued when a changed mode is first
dispatched are cleared along with other pending events.

## Counter and fault conditions

Both counters use event bit 0 **and** periodic flag `aaae.bit6`, read through
`089528`. Missing either condition pauses these checks; it does not constitute
a fresh fault-free observation.

| Condition at a qualifying observation | Actual result |
| --- | --- |
| `ad17 == 0`, healthy count below 199 | Increment `acf7`; reset fault-retry counter `acf8`. |
| `ad17 == 0`, next healthy count reaches 200 | Clear `acf7`, select mode 1 and record the previous mode. No start request is created. |
| `ad17 != 0` | Reset healthy count `acf7`. |
| Nonzero `ad17` with bit 5 set | Reset retry counter `acf8`; do not perform the timed clear. |
| Nonzero `ad17` with bit 5 clear, retry count below 299 | Increment `acf8`. |
| Nonzero `ad17` with bit 5 clear, next retry count reaches 300 | Reset `acf8`; execute `089233`, which clears the entire stored `ad17` word. Remain in the recovery mode. |

The bit-5 test is `08919f(6)`: its argument is one-based. No physical fault name
is assigned to this bit. The selected fault getter, actual clear function and
counter branches all execute.

A healthy observation below the 200 threshold resets the retry counter through
`087396 → 08739f → 0873b5`. Therefore, fault observations separated by a
qualifying healthy observation do not simply accumulate toward 300. Conversely,
a qualifying fault restarts the 200-observation healthy wait. Paused checks do
not reveal what happened physically between observations.

The 200th healthy branch bypasses the retry-counter reset, but subsequent mode
entry initializes the relevant state. This detail is covered without treating
that stale counter as an independently persistent setting.

### Stored fault clearing is not fault removal

`089233` is a RAM-word clear. A real voltage, temperature or other fault producer
may reassert a fault afterward; those producers are excluded here. The synthetic
sequence in which 300 fault observations are followed by 200 healthy observations
assumes the cleared fault stays clear. It establishes a software path to
eligibility, not physical recovery after “500 ticks.”

## Relevant events and priority

| Event while the recovery handler is active | Effect |
| --- | --- |
| Start bit 3 | Not latched by this handler. It does not set `acf1`. |
| Stop bit 4 | Does not create a run request or advance recovery by itself. |
| Bit 5 | Immediately calls `089233` and resets the healthy counter. This happens before the periodic checks, including when the timer flag is clear. |
| Bit 6 | Has priority over bit 5: executes cleanup, clears the run request, selects mode 5/reason 10, and records the previous mode. |

If bit 5 clears a fault while periodic checks are paused, the old retry counter
survives that invocation. The next qualifying healthy observation resets it.
With periodic checks enabled, that reset happens in the same invocation.

The [previous command-consumer replay](gen2-pv-retry-actuation.md) mapped internal
register `0015` word `0059` to event bit 5. This gives that previously unnamed
event a concrete **stored-fault-clear effect in this recovery mode**. It also
bypasses the bit-5 exception used by timed clearing. It remains internal research
evidence: the full physical guards and fault producers have not been established
for a public control.

Mode 6 shares the recovery handler and its 200-observation return, as tested.
The bit-6 diversion into mode 5 follows a different dispatcher branch; the
replay does not invent a return from mode 5.

## Eligibility still needs a fresh start

One complete synthetic sequence executes the following actual controller paths:

1. Mode 1 encounters a fault and exits to mode 4, clearing its run request.
2. Changed-mode dispatch initializes mode 4 and discards an early start event.
3. A stored fault without bit 5 clears at its 300-observation threshold.
4. After 200 healthy observations, the handler selects mode 1 with request 0.
5. Mode-1 initialization discards another start queued just before that mode
   is first dispatched.
6. One hundred qualifying startup invocations accumulate with request still 0;
   mode 1 does not issue startup writes.
7. A fresh start after initialization reaches the existing predicate and issues
   the previously traced peripheral write intents, entering mode 2.

The second fault word, `ad16`, does not prevent the tested recovery handler from
returning to mode 1. Its selected bits are still checked by the mode-1 start
predicate. Six cases cover this distinction. They do not demonstrate a physical
fault bypass: the module status producer and main-controller policy can impose
additional conditions outside these DSP-only cases.

### How the main controller could supply the new request

The earlier [module-status trace](gen2-pv-retry-actuation.md) established that
register `0126.bit5` can become set when the timer qualifier is present, mode is
no longer 4, and both fault words are zero. Its rising transition can pass through
the main controller's debounce and DC-input policy, then configuration and a
fresh internal `0057` start request.

That is a conditional composition of separately executed paths. This replay
does not execute concurrent main/DSP scheduling, UART delivery, debounce timing
or the real fault producers. It therefore cannot guarantee that a late-enough
start arrives, or that a real panel supplies power. In particular, the module
status predicate treats mode 4 explicitly; sharing a recovery handler does not
make every mode-6 interaction equivalent.

## Reproduction and remaining limits

Use the [existing offline setup](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-pv-mode4 \
  python3 tools/firmware_analysis/emulate_pv_mode4_recovery.py
cmp /tmp/solix-pv-mode4/pv-mode4-recovery-results.json \
  tools/firmware_analysis/expected_results/pv-mode4-recovery-results.json
```

The tool requires `dspDCDC-decoded.bin` SHA-256
`7ae2bb915812441b8316d1c3f3efc2379fcb488d4d03e950901cf507c224ceb8`
and checks all container-record CRCs. It extends the limited C28x replay in a new
file; existing tools are unchanged. Unsupported instructions fail. No function
return values are substituted; register-save/call stacks are represented
abstractly, and RAM, peripheral values, events and fault changes are synthetic.

Counts: **16 dispatcher-entry, 16 healthy-threshold, 4 periodic/event-gate,
16 timed-fault-clear, 10 event-priority, 6 second-fault-word eligibility and
5 multi-stage recovery cases**. The manifest records runtime/helper hashes,
entry points and exclusions. Published artifacts contain no captures or IDs.

Fault producers, physical time, peripheral semantics, main-controller
concurrency, mode-2 regulation and charging power remain untested. A physical
locked-PV trial with complete baseline/readback remains the prerequisite for a
recovery feature. No new runtime API or device operation was added.
