# C1000 Gen 2 AC countdown roundtrip

## Scope and result

Offline audit dated 2026-10-02 of **A1763 C1000 Gen 2 main 1.1.4.9**. The exact
198656-byte image is loaded at `08005000`, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
[98 new synthetic instruction cases](../tools/firmware_analysis/emulate_gen2_ac_countdown_roundtrip.py)
extend the [native output-readiness replay](gen2-native-output-readiness.md) with
600-second AC timer roundtrips, normal rapid rearming, and off/on task resets.
No device, BLE, MQTT, cloud, SSH, relay or physical clock was accessed. These
addresses and behavior are **not established for C2000 or other firmware**.

**Early AC timer cancellation preserves all 415 saved settings bytes and the
complete mode-1 A4 report in the tested normal sequences. It does not restore
every volatile byte.** Zero clears an internal checkpoint; active timer sampling
resets the Smart inactivity counter. Fresh status cannot expose a pending stop
event, previous-countdown state, or all of that counter history.

## Native setter and sampled on-state route

The ordinary command remains native `0101`, expected response `0901`:

```text
source: A1 value 22
one setting: A3 value 03 <seconds_u32_le>
timestamp: FD value 00 <ASCII UTC milliseconds>
```

Use the existing native packet/envelope and nonretained publication. Omit AC
switch A2, charge power A4, frequency A5, Smart A6, and fast charging A7. The
replay runs parser `080225a6` and the actual handler `0800bd1c`; response delivery
is a substitute. Positive A3 stores remaining seconds at `20002174` and enables
bit 0 at `20002188`. Zero clears these and the 16-bit checkpoint at `20002184`.
Neither setter changes AC output flags, Smart counter or previous-countdown byte.

The stable on-state replay starts at the actual mask check `0802494a`. AC is
already initialized, and the software output mode matches the cached DSP mode.
It executes the sampling gate, mode check, AC power-report helper `0800ce98`,
sample-timer restart, counter increment and policy call through `08024a86`.
Software mode values 1 and 2 are both tested; they are not assigned app labels.

An active sample timer skips the policy. On an eligible pass, the caller adds
2 to its 16-bit counter and calls `080134fc`. The policy subtracts that amount
and resets the counter. Synthetic timer eligibility/ticks are explicitly
supplied; this does not measure task scheduling or elapsed physical time. The
replay stops before the task's later fault/protection body, which is independent
of timer storage. It does not prove physical output continuity.

Forty-eight bounded cases cover both mode values, saved Smart off/on, counters
0/598, checkpoints 0/55, and zero/one/five eligible active samples:

| Point | Mode-1 A4 AC remaining | Stop event | Saved settings |
| --- | ---: | --- | --- |
| Inactive baseline | 0 | None | Captured 415 bytes |
| 600-second write ACK | 600 | None | Unchanged |
| Five eligible active samples | 590 | None | Unchanged |
| Zero cancellation ACK | 0 | None | Unchanged |
| Later skipped and eligible inactive samples | 0 | None | Unchanged |

The [ordinary native `0100`/correlated `0900` route](gen2-output-policy-followup.md#ordinary-status-reports-include-real-remaining-values)
uses mode 1 with actual remaining timers on this image. Internal comparison mode
3 contains zero placeholders and cannot supply a timer baseline.

## Rearming: reachable sequences versus adverse seeds

The AC policy remembers whether a countdown was enabled in byte `20000026`:

| Policy entry | Elapsed amount used |
| --- | --- |
| Countdown enabled, previous byte zero | Fixed 2, ignoring earlier Smart counter |
| Countdown enabled, previous byte nonzero | Current caller-incremented counter |
| Countdown disabled | Smart branch; records previous byte zero on exit |

Zero A3 does not reset the previous byte. This initially suggested that old
history could expire even a long newly armed timer. The new ordinary lifecycle
cases provide a narrower conclusion:

- Arm then cancel before any active sample: previous remains zero, so rearming
  consumes 2 regardless of the earlier counter.
- Arm, take an active sample, then cancel and immediately rearm: previous is one,
  **but the active sample cleared the counter**. The next caller adds only 2.
- Taking an eligible inactive sample between cancellation and rearming records
  previous zero again.

All 24 ordinary rearm cases, including an earlier counter of 65534, consume
exactly 2 from a new 600-second duration and leave no stop event. Therefore
**the replay does not establish an early-expiry bug in normal cancel/rearm**.

Twelve separately labeled adverse cases seed arbitrary hidden combinations.
Previous one plus counter 598, 600, or 28800 can queue an immediate stop on the
next caller sample; previous zero forces 2. Those adverse combinations are not
shown reachable through the ordinary tested arm/cancel/rearm sequences. Their
identical inactive A4 reports establish an observability limit, not a proven
normal-lifecycle failure. No whole-program invariant for all other producers,
memory faults or concurrent writers is claimed.

## AC off/on is an asynchronous reset path

The native output A2 handler invokes `08013708` only when its desired boolean
differs from the logical output getter. That helper ORs `40` hex into request
halfword `20000436` and marks a deferred event using index byte `2000015c`.
Four actual-handler cases confirm that its ACK does **not** synchronously clear
the counter/history or change the output word. The event registration is
synthetic; the complete deferred control callback is excluded.

After the software output transitions off, actual task fragment `08024b66`:

- Clears counter `20000030`, initialized byte `20000020`, timer enable, remaining
  duration and checkpoint.
- Preserves previous-countdown byte `20000026`.

The next on initialization `080249ec..080249f6` clears the counter again. The
actual on descriptor builder `0802a870` emits channel 1, command 1, six payload
bytes; allocation/final queue are substitutes. Eight reset cases cover previous
zero/one, counter 598/65534, and accepted/rejected queue admission. Counter reset
occurs even if admission fails, while the initialized flag stays zero on failure.
A later selected retry succeeds. Arming after this task reset consumes 2 even
with retained previous one, because the counter is zero.

The replay substitutes the deferred off/on output-word transitions and does
not establish physical relay operation. **Holding AC off for ten seconds is
not a firmware guarantee of task execution.** Fresh logical off telemetry can
precede DSP delivery. OFF/ON is also unnecessary for the tested ordinary rearm
sequences; it should not be treated as an atomic history-reset command.

## Restoration and the queued-stop boundary

The bounded cases compare a second machine executing the same samples without
timer writes. All 415 saved bytes and A4 return exactly to their captured values.
However:

- All 24 cases with initially nonzero checkpoint lose that hidden value when
  zero is written; A4 does not expose it.
- Sixteen cases with Smart enabled end with different counter history from the
  no-timer comparison. A timer can restart its low-load grace history.
- A skipped sample after cancellation can leave previous one until the next
  eligible inactive sample. The setter itself does not normalize it.

Two new 600-second sampled boundary sequences complement the earlier
[stop-worker race audit](gen2-output-stop-worker.md#cleanup-and-the-cancellation-race):

| Eligible samples before zero | State | Worker result |
| ---: | --- | --- |
| 299, nominally 598 sampled seconds | Remaining 2, no stop queued | AC flags unchanged; all 415 saved bytes unchanged |
| 300, nominally 600 sampled seconds | Stop already queued | Zero ACK does not revoke it; worker clears AC mask `c30` |

The expiry case seeds saved AC recovery byte `SETTINGS+22` as one: expiry clears
that byte and requests persistence. The other 414 bytes remain unchanged. These
nominal sampled seconds are not physical-time measurements. A4 zero after
cancellation still cannot certify absence of a previously queued stop.

## Guard for a bounded first live trial

A first live trial can provide evidence under controlled conditions without
claiming lossless transient restoration:

1. Use only the noncritical C1000 Gen 2, exact main `1.1.4.9`, and an expendable
   load. Never use the C2000 powering servers. Confirm independent AC output
   operation and a known inactive history with no concurrent app/controller.
2. Under the control lock, capture fresh complete native A4/D9, firmware, both
   zero remaining timers, AC on, DC state, mains presence and saved preferences.
   Require AC Smart **off** to avoid deliberately altering enabled Smart history.
   Prefer established Standard/inactive backup/transfer state to reduce other
   independent control activity. Quiet status alone is not proof of hidden state.
3. Send only A3=600. Obtain fresh correlated mode-1 status promptly; expect a
   positive remaining value close to 600 and unchanged protected preferences and
   outputs. Plan the zero cancellation within roughly ten seconds of the write,
   independently of whether positive confirmation succeeded.
4. Send only A3=0 well before expiry. Confirm zero, saved A4/D9 and both outputs
   in multiple fresh samples; inspect the independent load/display. Do not
   describe that as restoring checkpoint/counter bytes or revoking queued stops.

This is a proposed trial, not a tested public API or unconditional safety guard.
The station has no proven external query for those hidden event/counter fields.
If cancellation cannot be delivered, a remaining timer can still expire and stop
the test load. The full 415-byte comparison is offline evidence; ordinary native
status does not export the whole saved file.

The later [bounded native MQTT trial](c1000-gen2-native-ac-countdown-validation.md)
confirmed 600→594→0 with two timer writes and no output-switch writes. Its live
telemetry evidence is separate from these synthetic cases; physical expiry and
independent output measurements remain untested.

## Reproduction

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-ac-countdown-roundtrip \
  python3 tools/firmware_analysis/emulate_gen2_ac_countdown_roundtrip.py
cmp /tmp/gen2-ac-countdown-roundtrip/gen2-ac-countdown-results.json \
  tools/firmware_analysis/expected_results/gen2-ac-countdown-results.json
cmp /tmp/gen2-ac-countdown-roundtrip/gen2-ac-countdown-manifest.json \
  tools/firmware_analysis/expected_results/gen2-ac-countdown-manifest.json
```

Use the published analysis requirements. Cases: 48 bounded roundtrips, 24
ordinary rearms, 12 adverse seeds, four deferred output-handler calls, eight
off/on reset cases, two 600-second expiry/cancel boundaries. Expected artifacts
contain synthetic values only, with no account/device identity, credential or
phone capture. The manifest hashes source dependencies and result; a second
run matches both files byte for byte. ROM is read/execute-only; instruction
writes are limited to synthetic RAM. Explicit substitutes/exclusions are listed
in the manifest, including full tasks/scheduler, post-policy protection body,
radio, physical clocks, DSP/BMS/relay execution and output sensing.
