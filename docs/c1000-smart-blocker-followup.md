# Original C1000: AC Smart blockers and counter history

Offline investigation dated 2026-10-01 using public **A1761 MainMcu 1.5.9**.
The installed original C1000 runs **1.7.1**. These instruction replays do not
establish its behavior, Gen 2 behavior, or a physical shutdown interval.
No device, network, output dispatch or persistent storage was accessed.

This extends the [Smart auto-off analysis](c1000-smart-auto-off-policy.md).
It traces the global blocker's producer and the GPIO reader, then combines
those with timer and inherited-counter cases. It does not repeat the earlier
1,347-case boundary suite.

## The global blocker is qualified DSP state

AC Smart resets its counter when `20000690` bit 0 is set. Startup routine
`0800ddfc` registers the following first entry at `20002508`:

| Entry member | Value |
|---|---|
| Entry predicate | `08021014` |
| Removal predicate | `080210a0` |
| Change callback | `08021044` |
| Threshold byte | 200 |
| Periodic sampler | `080208a8`, registered for 10 ticks |

The entry predicate requires all of these conditions in the DSP cache at
`20002274`:

```text
byte[0] bit0 is set
uint16[2] & 0x3fff == 0
byte[4] == 0
byte[8] bit0 is clear
```

Sixty actual predicate cases distinguish the fault-word mask's lower 14 bits
from bits 14/15 and the single bit at byte 8 from its other bits. The removal
predicate is simpler: **byte[0] bit0 is clear**. It does not repeat the other
entry checks. After qualification, asserting the tested fault bits while
keeping presence bit 0 set leaves the global blocker set through 300 samples.

The change callback sets global bit 0 for its internal event mask 2 and clears
it for mask 1, preserves unrelated bits, records an input-change flag, and
queues an event. These are internal MCU callbacks, not new application commands.
The global flag participates in the AC charging policy, but a qualified flag
is not a complete test of healthy mains, bypass or actual battery current.

### Qualification includes history and delay

The real sampler increments an 8-bit counter and changes state when it is
greater than `threshold + 1`:

| Sequence | Observed transition |
|---|---|
| Fresh counter 0, continuously valid entry | Sample 202 sets global bit 0 |
| Invalid entry samples, then valid entry | Invalid samples leave counter 1; valid sample 201 sets the bit |
| Qualified state, presence disappears | Removal sample 202 clears the bit |
| Qualified state, only entry-fault conditions appear | Bit remains set in the 300-sample replay |

Complete startup registration also produces a repeating **10-tick** timer
with callback `080208a9`. Combined with the earlier nominal 1 ms tick proof,
202 samples are approximately 2.02 seconds under uninterrupted scheduling.
This is a nominal derivation. This replay does not run real interrupts or
promise wall-clock latency, and cached DSP data can itself be delayed.

## The GPIO is a separate input

Smart's other blocker calls `08007fb8(3, 2)`. Actual table lookup and GPIO
reader execution show it reads **`40011408` bit 2**. A zero result resets the
counter. It reads the input register; changing the corresponding synthetic
output-register value at `4001140c` has no effect on the result.

Initialization `08008064` changes pin 2's configuration nibble at `40011400`
to 4 while preserving the other nibbles. This numeric register behavior is
verified with captured emulator writes. The board signal's electrical name,
polarity relative to mains, voltage and wiring remain unresolved. No physical
pin was read or driven by this investigation.

There is a useful future diagnostic lead: the property-table entry at
`08029be0` associates **F0** with handler `0801255e`. That handler reads the
same GPIO and passes a single byte, 0 or 1, to response helper `0800d6b0`.
The replay captures that helper. The enclosing public BLE/native query
envelope and availability on installed firmware are not established, so this
does not add a runtime query or label F0 as a mains sensor.

## A blocker must overlap an eligible Smart check

The two-second Smart timer gate precedes all three counter-reset checks:
GPIO, global bit and output countdown. When the timer is active, those checks
are skipped entirely.

Three new sequences start with counter 451 in Normal mode, assert one blocker
while the timer is active, execute the real Smart-enable handler, and release
the blocker before the next eligible sample. All three produce:

```text
counter451 → skipped check451 → Smart enabled451 → eligible check → stop request
```

The temporary blocker does not reset the counter because it was never sampled
at the relevant point. Conversely, keeping any blocker present for an eligible
sample yields counter **1**, and the first sample after release yields **2**.
Reset and increment happen during the same eligible sample.

Another pair of synthetic cases holds GPIO high and starts at counter 451:
running the Smart check immediately **before** DSP qualification sample 202
requests stop; running it **after** qualification resets the counter to 1.
This proves an ordering dependency between the two software paths. It does
not prove a physical mains-plug race: the GPIO's real behavior, scheduler
ordering and other protections have not been established.

Every stop is captured as an event request. The replay does not dispatch it,
and the synthetic AC output-enabled flag remains set during these cases.

## Output lifecycle resets differ from mode changes

The previous analysis proved that enabling Smart leaves its counter untouched.
Two additional actual instruction blocks explicitly clear it:

- The outer AC-output-disabled gate at `0801f762` branches to
  `0801fa5a`, resetting the counter and cached AC output power.
- The output initialization tail at `0801f806..0801f80a` resets the counter
  after preparing the output-start request.

Both paths reset synthetic counters 1, 451 and 65535 to zero. The replay only
executes the initialization's RAM-reset tail; it does not send its preceding
DSP start request. These findings are not a recommendation to cycle an output
to reset Smart timing, particularly when loads must remain powered.

## Practical conclusions and remaining work

- Enabling Smart cannot promise a fresh inactivity grace period on this image.
- Brief mains, GPIO or countdown changes cannot be assumed to clear history;
  their timing relative to an eligible check matters.
- Stable qualified input can block this particular Smart path. It does not
  disable faults, countdowns, Device Timeout or every output-stop policy.
- Software qualification and GPIO are independent inputs. Do not infer one
  from the other or from reported input watts alone.
- Physical GPIO meaning, original DSP calibration and installed 1.7.1 behavior
  remain the useful next evidence gaps. The internal F0 getter is a research
  lead, with its transport and semantics still unverified.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_smart_blockers.py \
  --firmware firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-smart-blockers.json
cmp /tmp/original-smart-blockers.json \
  tools/firmware_analysis/expected_results/original-smart-blockers-results.json
```

**90 new cases pass:** one timer registration, one GPIO configuration,
eight GPIO reads, two diagnostic getter captures, 60 DSP predicates, four
qualification sequences, eight inherited-counter/order sequences and six
output-lifecycle resets. The SHA-256-checked input is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
The adjacent manifest records tool, helper, dependency and expected-result
hashes. All fixtures contain synthetic state and public firmware metadata.
