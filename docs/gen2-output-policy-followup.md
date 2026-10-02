# C1000 Gen 2 output countdowns and Smart policy

## Scope and result

Offline **A1763 C1000 Gen 2 main 1.1.4.9** audit dated 2026-10-01.
[635 synthetic instruction cases](../tools/firmware_analysis/emulate_gen2_output_policy.py)
extend the earlier [preference storage/readback work](gen2-preference-candidates.md)
into countdown execution and asynchronous Smart decisions. No Bluetooth, MQTT,
SSH, cloud, device or output command was sent. C2000, original C1000 and other
firmware equivalence are unproved.

The countdown controls have concrete native fields. Smart has concrete storage
and a bounded low-load policy, including an important distinction from original
C1000: **an eligible Gen 2 policy sample resets the inactivity counter while
Smart is off**. The setter itself does not reset it. Neither finding establishes
a physical shutdown time or authorizes C2000 output experiments.

## Countdown request and readback

The hash-checked ordinary controller table at `08032e60` maps native `0101`
to handler `0800bd1c` and `0102` to `0800bf94`. Both handlers accept A3:

```text
A3 value: 03 <remaining_seconds_u32_le>
AC: native 0101; established BLE application group 4101
car/DC: native 0102; established BLE application group 4102
```

Type `03` and four little-endian seconds are the request format. Normal source
and timestamp fields still belong to the selected transport. This direct-handler
suite does not replay radio delivery or test these timer writes on a station.

| Operation | Actual handler result |
| --- | --- |
| Positive AC duration | Stores remaining value at `20002174`, enables volatile countdown bit 0 |
| Positive car/DC duration | Stores remaining at `2000217c`, initial duration at `20002180`, enables volatile bit 1 |
| Zero | Clears remaining and the domain's volatile enable bit; clears its 16-bit checkpoint |
| All tested writes | Leave persistent preferences, electrical-output word and Smart inactivity counter unchanged |

No handler range check rejects `ffffffff` in the replay. This does not make every
uint32 duration suitable for a public API. Zero leaves the car/DC initial-duration
word intact; cancelling is not restoration of all transient RAM.

### Ordinary status reports include real remaining values

Full 34-byte A4 includes remaining AC seconds at `[1:5]`, car/DC at `[9:13]`.
The serializer supports three modes, but their reachability matters:

| Path executed | Serializer mode | Countdown bytes |
| --- | ---: | --- |
| Native `0100` status handler `0800b57c` → serializer at `0800b5f4` | 1 | Actual remaining values |
| `0421` publication callback `08012634` → serializer at `0801265a` | 1 | Actual remaining values |
| Internal comparison `08013c46` → `0801bd40` | 3 | Zero placeholders |

Nine whole-producer cases confirm both ordinary report paths preserve nonzero
and maximum countdown values. The mode 3 comparison does not publish; a detected
change schedules `0421`, whose callback rebuilds in mode 1. No false-zero timer
baseline in current native status handling is established here. The MQTT gateway
requests fresh `0100` and decodes its correlated `0900` response for control
baselines. Its Gen 2 timer guards therefore use the proven mode 1 route on this
image. C2000 remains dependent on its separate live evidence.

## Countdown execution

The real AC policy `080134fc` and car/DC policy `08014a08` take precedence over
Smart whenever their volatile countdown bit is set. Their caller fragments:

1. Skip the policy while a one-shot sampling timer is active.
2. Restart that timer, with period **2000 ticks**, once eligible.
3. Add **2**, modulo 65536, to a 16-bit counter and call the actual policy.

The [millisecond tick evidence](gen2-energy-counter-investigation.md) gives the
timer's software scale. A late task or timer poll does not add the missed elapsed
time to this counter: countdown progression depends on executed samples.

When countdown has just transitioned from disabled to enabled, the policy uses
**2**, discarding any accumulated counter for that sample. Later samples use the
counter. If `counter < remaining`, the policy subtracts it; equality or greater
marks an output-stop event. Each countdown-policy call resets the counter to zero.
Power threshold, Smart flag and AC Smart blockers do not prevent this timer branch.

Expiry sets AC request bit `2` or car/DC request bit `32` at `20000140`, clears
the corresponding saved timer/recovery byte and requests persistence. It marks
the shared event whose index is stored at `2000015e`, with argument zero, using
real helper `080106b4`. Reasons `55`/`56` are stored at `200000c2`.

The policy does **not** itself clear the volatile enable bit or zero the remaining
word at expiry. The later event worker/output cleanup is excluded from this suite.
A handler ACK, remaining zero, or seeded output word cannot prove physical output
continuity or complete post-expiry cleanup.
The subsequent [stop-worker replay](gen2-output-stop-worker.md) executes that
worker: writing zero after expiry is queued does not revoke the pending stop,
and software output state can change before DSP queue admission.

## Smart thresholds and counter history

Smart operates only outside the active countdown branch. The policy compares
signed raw DSP power values; these thresholds are not calibrated physical watts
by this replay:

| Domain | Low-load predicate | Counter limit; stop uses strictly greater |
| --- | --- | --- |
| AC, internal product prefix `YH` | Raw signed power ≤20; cached AC-state byte nonzero; blocker bit 0 clear; Smart nonzero | 28800, nominal eight hours of two-second sampling |
| AC, other prefix | Same | 900, nominal fifteen minutes |
| Car/DC | Raw signed power ≤3; Smart nonzero | 18000, nominal five hours |

The prefix comes from getter `0801926c` at `20000112`, not the advertised model
name. This work does not identify the physical unit's prefix. The car/DC log
string says **“15min”**, but its actual comparison constant is `4650` hex =18000.
The instructions and the synthetic boundary cases agree; the log label alone
would give the wrong timing inference.

The cached AC-state byte is `2000014c`, read by `080180f8`; the blocker is
`200004be` bit 0. Their physical producers are not executed by this suite.

Any failed low-load predicate resets the counter on an eligible policy call.
Smart off likewise resets it, unlike the original-C1000 Normal-mode behavior.
Enabling/disabling Smart still leaves the counter untouched during the setter.
Six explicit off/on history cases establish:

- Off → eligible policy sample → on clears earlier history.
- Off → on before any policy sample retains earlier history.
- Off → sampling timer still active → on also retains earlier history.

The latter two can mark a stop at the next eligible low-load sample when a
counter is already above the threshold. Toggling the preference is not a promised
fresh inactivity grace period. Marked events leave the synthetic electrical-output
word unchanged because their worker is not executed.

## Implementation and live-test prerequisites

Subsequent C1000 Gen 2 trials exposed guarded [DC Smart](c1000-gen2-native-dc-smart-validation.md)
and [AC Smart](c1000-gen2-clock-ac-smart-validation.md) configuration, requiring
the corresponding output off and inactive timers. A private operator
[AC countdown](c1000-gen2-native-ac-countdown-validation.md) now has a bounded
600-second arm/early-cancel validation. Physical expiry and low-load shutdown
remain untested; C2000 output-policy controls remain excluded.

A first configuration trial should use the noncritical C1000 Gen 2 with
the tested domain already off, fresh complete status, inactive countdowns and
saved full preferences. Changing Smart while an output is off can establish
configuration/readback without asserting low-load shutdown behavior.

A countdown trial requires a noncritical load, initially inactive timer, ample
duration, fresh explicit `0100` readback and cancellation before expiry. Restoring
a saved positive remaining value can extend the original finish time; a zero
baseline is preferable. Verify saved settings and AC/DC state after cancellation,
and keep transient checkpoint/initial-duration limitations explicit. These are
prerequisites, not a live test performed by this audit.

## Reproduction and limits

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-output-policy \
  python3 tools/firmware_analysis/emulate_gen2_output_policy.py
cmp /tmp/gen2-output-policy/gen2-output-policy-results.json \
  tools/firmware_analysis/expected_results/gen2-output-policy-results.json
cmp /tmp/gen2-output-policy/gen2-output-policy-manifest.json \
  tools/firmware_analysis/expected_results/gen2-output-policy-manifest.json
```

Requires the published analysis requirements. The main image is 198656 bytes,
SHA-256 `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
`SOLIX_FIRMWARE_DIR` can supply the exact image externally. Synthetic output
contains no account, pairing identity, station serial, MAC or private capture.

Cases: 384 Smart boundaries, 4 AC blocker combinations, 192 countdown cases,
12 wire/readback cases, 16 Smart setter/history boundaries, 6 off/on sequences,
12 actual caller fragments and 9 ordinary/internal report paths.

Real parser, handlers, getters, selected caller fragments, policies, software
timer query/restart, event marking and A4/report serialization execute. Other
measurement callbacks return synthetic empty values in the report-path cases.
Logging/diagnostic formatting, memcmp, tick reads, memory helpers, persistence,
allocation/free and response/refresh/LCD transports are explicit substitutes.
ROM is read/execute-only; instruction writes are restricted to synthetic RAM.
Full output tasks, event worker, radio/MQTT, elapsed wall time, actual DSP/BMS,
protections, flash and physical power behavior are not reproduced.
