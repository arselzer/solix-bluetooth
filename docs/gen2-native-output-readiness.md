# C1000 Gen 2 native AC Smart and countdown readiness

## Scope and new result

Offline audit dated 2026-10-02 of **A1763 C1000 Gen 2 main 1.1.4.9**,
using the exact 198656-byte image at `08005000`, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
The [92-case instruction replay](../tools/firmware_analysis/emulate_gen2_native_output_readiness.py)
uses synthetic RAM only. No device, BLE, MQTT, SSH, cloud or physical output was
accessed. This behavior is not established for C2000 or another firmware.

**A positive countdown cannot be validated by leaving its output off.** Its
setting handler acknowledges and immediate mode-1 A4 telemetry contains the
requested duration, but the next executed output-off task clears that countdown.
This occurs without timer expiry, a stop event or a pending DSP stop descriptor.
AC Smart has a simpler, lossless saved-preference roundtrip while AC stays off.

## Exact candidate request fields

These are fields inside the existing native packet/envelope, not complete MQTT
messages. Include source `A1=22` and normal `FD = 00 + ASCII UTC milliseconds`;
publish without retain. The replay also exercises the established FE-seconds
form. It executes the main parser/handler, not radio timestamp processing.

| Candidate | Native command / response | One setting TLV | Fresh readback |
| --- | --- | --- | --- |
| AC Smart preference | `0101` / `0901` | `A6`, value `01` + boolean byte | A4 byte 8 |
| AC countdown | `0101` / `0901` | `A3`, value `03` + LE32 seconds | A4 bytes 1–4 |
| Car/DC countdown | `0102` / `0902` | `A3`, value `03` + LE32 seconds | A4 bytes 9–12 |

Omit output-switch `A2` and every other setting. Use a fresh explicit `0100`
request and correlated `0900` response for the baseline and confirmation.
[The earlier serializer audit](gen2-output-policy-followup.md#ordinary-status-reports-include-real-remaining-values)
establishes mode 1 with actual remaining timers on that ordinary status route;
internal mode-3 comparison values are unsuitable as a timer baseline.

## Countdown cleanup is an ordinary off-state path

The replay extends the earlier selected off fragments through their actual
output-flag branch checks:

| Domain | Dispatch check | Off-task path | Unconditional cleanup |
| --- | --- | --- | --- |
| AC | `0802494a`, output word `20000164` mask `30` hex | `08024a4c` → `08024b66` | `08024b8e` calls `080296c0` |
| Car/DC | `0802449c`, output bit 1 | `080245a6` → `080245f6` | `0802462a` calls `0802984c` |

All 40 off-dispatch cases test durations `0`, `1`, `2`, `3600`, and `ffffffff`
hex, both timestamp forms, and pending-DSP-stop flag zero/one. Cleanup resets
remaining seconds, its enable bit, checkpoint and Smart counter. Logical output
flags are unchanged; no stop event is marked. With pending flag zero, no DSP
descriptor is built. Twenty on-dispatch cases instead stop at the actual on
branch before its body; they establish branch selection only.

For car/DC, a positive timer also stores its initial duration at `20002180`.
Off-task cleanup **and a later zero timer write preserve that initial value**.
Consequently a positive DC timer trial is not an exact restoration of every
volatile byte even when the visible remaining timer returns to zero. There is
no claim that a timer schedules deferred activation of an already-off output.

This corrects the previous suggestion to validate positive duration readback
with the tested output already off. Such a trial can demonstrate an ACK and
brief readback, but cannot confirm a persistent working countdown.

## AC Smart next-step guards

Thirty-two additional cases cover saved and target boolean combinations, Smart
counter values `0`, `900`, `28800`, `65535`, and both timestamps. Actual native
`0101`/A6 changes only `SETTINGS+1f` and A4[8]. Restoration returns **all 415 saved
settings bytes** and complete A4 telemetry exactly to their original values.
The setter retains counter history; the subsequent ordinary off task resets the
counter. No event, output-flag mutation or DSP descriptor occurs in these cases.

The subsequent [native AC Smart implementation and trial](c1000-gen2-clock-ac-smart-validation.md)
uses the following guards, matching the
[validated DC Smart control](c1000-gen2-native-dc-smart-validation.md):

1. Restrict it to C1000 Gen 2 main `1.1.4.9`; require fresh AC output off, both
   countdowns zero, and raw saved A4[8] equal to 0 or 1. Do not enable C2000 from
   this firmware evidence.
2. Capture full A4/D9 plus AC/DC outputs and mains presence under the existing
   control lock. Send only A6, then confirm twice with fresh status, preserving
   all other saved fields and protected states. The documented display-activity
   byte can vary independently.
3. Restore the captured boolean and confirm again. Failed confirmation may have
   applied the setting; obtain fresh state before a restoration attempt.

This validates preference storage only. Low-load shutdown timing, physical AC
continuity and a fresh full grace interval are not proved. The previous policy
audit retains the product-prefix threshold and counter-history uncertainty.

For countdown validation, use only an expendable **output-on C1000** load, an
inactive baseline, ample duration and cancellation well before expiry. Never
test this on the C2000 powering servers. A saved positive remaining count is
not an exact finish-time restore; zero acknowledgment does not revoke a stop
already queued by expiry, as shown in the
[event-worker replay](gen2-output-stop-worker.md#cleanup-and-the-cancellation-race).

## Reproduction and boundaries

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-native-output-readiness \
  python3 tools/firmware_analysis/emulate_gen2_native_output_readiness.py
cmp /tmp/gen2-native-output-readiness/gen2-native-output-readiness-results.json \
  tools/firmware_analysis/expected_results/gen2-native-output-readiness-results.json
cmp /tmp/gen2-native-output-readiness/gen2-native-output-readiness-manifest.json \
  tools/firmware_analysis/expected_results/gen2-native-output-readiness-manifest.json
```

Use the published analysis requirements. Expected files contain only synthetic
values and fixed example timestamps, with no private identities or captures.
The manifest hashes source dependencies and result. A second run matched both
files byte for byte. ROM is read/execute-only and instruction writes are limited
to synthetic RAM. Parser, handlers, dispatch checks, off fragments, cleanup and
A4 serializer execute original instructions. Logging/libc, response delivery,
LCD/report delivery, persistence/refresh transports, allocation/free and final
DSP queue remain explicit substitutes. The full output tasks, on-task bodies,
scheduler, radio, DSP/BMS execution, relay sensing and hardware protections are
outside the replay.
