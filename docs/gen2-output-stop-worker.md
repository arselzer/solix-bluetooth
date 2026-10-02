# C1000 Gen 2 countdown expiry and output-stop worker

## Scope and result

Offline audit dated 2026-10-02 of **A1763 C1000 Gen 2 main 1.1.4.9**.
[1,045 synthetic instruction cases](../tools/firmware_analysis/emulate_gen2_output_stop_worker.py)
extend [the countdown and Smart policy replay](gen2-output-policy-followup.md)
through the actual event scanner, registered stop worker, timer cleanup, output
state getters and selected task fragments that build DSP stop requests. No
Bluetooth, MQTT, SSH, cloud, device, output command or hardware was accessed.
The addresses and behavior are not established for C2000 or another firmware.

**Cancelling after expiry has marked the stop event does not revoke that event.**
The timer setter can acknowledge zero, and the remaining field can read zero,
while the later event worker still clears the output flags. This is a concrete
reason to cancel well before expiry during a future noncritical C1000 test.

## Actual event route

The replay executes event initialization at `08010680`, then the registration
fragment `08006f20..08006f48`. The common output-stop callback is registered as
`0800709d` (Thumb entry `0800709c`); its event index is written to `2000015e`.
It becomes slot 4 in this synthetic initialization sequence. A separately executed
LCD registration fragment `0800e3e2..0800e3ea` assigns slot 5. These slot numbers
depend on the registered callbacks, rather than being protocol constants.

Expiry policies mark this actual registered event through `080106b4(index, 0)`:
AC requests bit `02`, car/DC bit `20` hex, in byte `20000140`. The event helper
coalesces marks and retains the first nonzero argument. `08010644` scans the
25-record event table and clears pending and argument **before** invoking the
callback. The callback can mark the later LCD slot, which this scanner pass also
reaches. The LCD worker itself is a recorded substitute, not display emulation.

The worker consumes the recognized request bits as follows:

| Request bit (hex) | Mask cleared in output word `20000164` |
| --- | --- |
| `01` | `00000040` |
| `02`, AC | `00000c30` |
| `04` | `00000001` |
| `08` | `00000008` |
| `10` | `00000004` |
| `20`, car/DC | `00000302` |
| `40`, combined stop | Expands to `02 | 08 | 10 | 20`, then is consumed |

Every combination of these seven recognized bits is replayed with four mode
bytes and two display-timer states. The table deliberately leaves the other
domains unnamed; their physical consumers are outside this audit. No assertion
is made for an undefined `80` request bit.

## Cleanup and the cancellation race

AC worker cleanup `080296c0` and car/DC cleanup `0802984c` operate on the volatile
countdown block beginning at `20002174`:

| Value | AC cleanup | Car/DC cleanup |
| --- | --- | --- |
| Enable byte `[20]` | Clears bit 0 | Clears bit 1 |
| Remaining seconds | Clears `[0:4]` | Clears `[8:12]` |
| Checkpoint | Clears `[16:18]` | Clears `[18:20]` |
| Car/DC initial duration `[12:16]` | Preserved | Preserved |

Six sequences execute the ordinary setting handler and actual policy/scanner:

| Sequence, both domains | Before worker | After worker |
| --- | --- | --- |
| Positive timer, zero written before expiry, policy sampled | No stop event | Output flags unchanged |
| Policy reaches expiry, then zero written and acknowledged | Stop bit and event remain pending | Domain output flags cleared |
| Policy reaches expiry without cancellation | Stop bit and event pending | Domain output flags cleared |

The ordinary A3 write clears its domain's volatile countdown state but does not
clear request byte `20000140` or the registered pending event. An ACK is therefore
not a cancellation acknowledgment for an already queued output stop. The replay
is deterministic interleaving of real instructions; it does not measure how wide
this race window is on a running station.

The worker also clears the saved timer/recovery preference (`SETTINGS+0x22` AC,
`+0x23` car/DC) and requests persistence when predicate `0800d014` returns false.
That predicate is false for internal mode byte `0`, `13` and `14` decimal, true
for the other values; the matrix tests these three values and representative `1`.
The expiry policy has already cleared its own saved byte before the worker,
independently of this worker predicate. The replay records persistence requests
without writing flash. It does not map these internal mode numbers to UI labels.

## Logical state precedes DSP delivery

The stop worker clears software flags. Selected later off-task fragments run with
synthetic context: AC `08024b66..08024b92`, car/DC `080245f6..0802462e`. They invoke
the real DSP descriptor builder `0802a758`; allocation and the final queue are
explicit substitutes. The captured intended requests are:

| Domain | DSP channel | Command | Two-byte payload |
| --- | ---: | ---: | --- |
| AC | 1 | `0000` | `52 00` |
| Car/DC | 1 | `0022` | `5b 00` |

Allocation failure or final queue rejection leaves the task's pending-stop flag
set. A later explicit execution of the same fragment tries again; successful
queue admission clears that flag. Neither failure restores the output word.
This proves retry behavior in these fragments, not scheduler timing, delivery
to the DSP, DSP acceptance or a relay transition.

The actual indexed getter `0801a518(0)` dispatches to `08019b00`, which tests
output mask `30` hex for AC. Index 6 dispatches to `08019aec`, which reads bit 1
for car/DC. Both report off after the worker even when the substituted queue
rejects the stop. The established AC telemetry field `A7[1]` uses this getter;
see [its earlier serializer audit](firmware-findings.md#mains-presence-does-not-expose-that-power-gate).
**Output-enabled telemetry is software state, not an independent confirmation
of physical output.** The AC mains-presence field comes from a different source.

## Prerequisites for a future C1000-only validation

1. Use only the noncritical C1000 Gen 2 and an expendable load. Never validate
   output expiry or low-load Smart shutdown on the C2000 powering servers.
2. Record fresh explicit native `0100`/correlated `0900` status, both remaining
   countdowns, output state and complete saved preferences. The ordinary status
   path uses serializer mode 1 with real timers on this image; mode 3 is an
   internal comparison route, as documented in the preceding audit.
3. Establish an inactive timer baseline. A later
   [dispatch replay](gen2-native-output-readiness.md) shows that ordinary off-task
   cleanup clears a positive timer set while its domain is already off. Validate
   a working countdown only with an expendable output-on load. Saving an active
   remaining value is not an exact reversible finish time: restoring it later
   extends the countdown.
4. For any output-on countdown trial, use ample duration and cancel well before
   expiry. Confirm fresh timer and output reports after cancellation and inspect
   the independent display/load. A zero ACK cannot revoke a pending stop.
5. Observe a deliberate expiry only after confirming the load is expendable;
   verify physical output and restoration independently. Keep the car/DC retained
   initial-duration caveat in the recorded result.

These are proposed prerequisites. No live validation or public control API was
added by this audit. Smart counter history and thresholds retain the limitations
in the [previous policy audit](gen2-output-policy-followup.md).

A subsequent [private CLI trial](c1000-gen2-native-ac-countdown-validation.md)
confirmed a 600-second timer, short progression and early zero cancellation on
main 1.1.4.9. It did not exercise expiry, independent output sensing or pending
stop revocation. HTTP and HA still exclude this output timer.

## Reproduction and evidence boundaries

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-output-stop-worker \
  python3 tools/firmware_analysis/emulate_gen2_output_stop_worker.py
cmp /tmp/gen2-output-stop-worker/gen2-output-stop-worker-results.json \
  tools/firmware_analysis/expected_results/gen2-output-stop-worker-results.json
cmp /tmp/gen2-output-stop-worker/gen2-output-stop-worker-manifest.json \
  tools/firmware_analysis/expected_results/gen2-output-stop-worker-manifest.json
```

Use the published analysis requirements. The main image is 198656 bytes,
loaded at `08005000`, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
`SOLIX_FIRMWARE_DIR` can supply this exact image externally.

Cases: one registration, 1,024 recognized stop-bit/mode/display combinations,
six expiry/cancellation sequences, four argument-coalescing sequences, eight
allocation/queue outcomes and two repeated-fragment retry sequences. Expected
JSON contains synthetic RAM values only, with no account, serial, MAC, pairing
identity, credential or phone capture. The manifest hashes source dependencies
and output; a second run matched both files byte for byte.

ROM is read/execute-only; instruction writes are restricted to synthetic RAM.
Logging/libc, diagnostics/report delivery, LCD worker, allocation/free, final DSP
queue and persistence/refresh transports are substitutes. Full tasks/scheduling,
radio/MQTT, actual DSP/BMS, output measurements, relay behavior and physical
protections are excluded.
