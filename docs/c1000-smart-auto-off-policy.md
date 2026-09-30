# Original C1000 Smart auto-off policy

## Scope and practical result

This is offline analysis of the original **C1000 A1761 main MCU 1.5.9**. The
station used for the separate control tests runs **1.5.1**. It is not evidence
that C1000 Gen 2 or C2000 uses the same policy, nor a physical shutdown test.

**Enabling Smart does not start a fresh inactivity interval in this image.**
The low-load counter runs in Normal mode too. The Smart command changes the
mode and persists it without clearing that counter. If it has already exceeded
the threshold, the next eligible check can request output shutdown. An
integration must not promise a new 15-minute or five-hour grace period when
Smart is enabled.

Normal mode skips this particular low-load shutdown path. It does not disable
fault handling, output countdowns, Device Timeout, or every other shutdown path.

## Recovered conditions

The callbacks first require their respective output-state bits. Once their
software timer expires, the following branches run:

| Property | AC output | Car/DC output |
| --- | --- | --- |
| Outer callback | `0801f570` | `0801f2f8` |
| Output-state gate | `200004fc` bit 4 | `200004fc` bit 1 |
| Signed input field | `20002274 + 0x1e` | `20002274 + 0x86` |
| Low-load comparison | Raw value `<= 20` | Raw value `<= 3` |
| Counter | `uint16` at `20000028` | `uint16` at `2000000a` |
| Request threshold | Incremented counter `> 450` | Incremented counter `> 9000` |
| Mode getter | `08017560` | `0801756c` |
| Normal mode | Value 1 skips the request | Value 1 skips the request |
| Smart mode | Value 2 permits the request | Value 2 permits the request |
| Stop request | `200004ec` OR `0x02` | `200004ec` OR `0x20` |

Both paths call the event queue at `08010278` with the event ID stored at
`200004f8` and argument zero. The replay captures this call; it does not dispatch
the event or operate an output.

The AC counter resets when any of these conditions is present:

- Raw input is greater than 20.
- GPIO helper `08007fb8(3, 2)` returns zero. The actual reader checks
  `40011408` bit 2; the physical pin's meaning has not been established here.
- `20000690` bit 0 is set; its physical meaning remains unresolved.
- The AC output countdown flag at `200020c4` bit 0 is set.

The car/DC counter resets when its raw input is greater than 3 or its output
countdown flag at `200020c4` bit 1 is set. This branch does not contain the AC
GPIO/global-flag checks. Its surrounding code identifies the car-output domain;
this does not establish that it measures or shuts down every USB/DC port.

**Reset is followed by an unconditional increment in the same sample**, so a
blocking sample normally leaves the counter at 1. Eligible samples then
increment it. The `uint16` counter wraps from 65535 to zero. Normal leaves an
above-threshold counter running; Smart resets it to zero when requesting stop.

### Countdown provenance

Actual command handlers `0042` / `0800b990` and `0043` / `0800ba88` store a
32-bit AC or car-output countdown at `200020b0` / `200020b8`. Nonzero values set
the corresponding blocking flag; zero clears it. Eight replay cases execute
these handlers and verify the flags while preserving unrelated bits. These are
**output countdowns**, separate from Device Timeout and Smart mode.

## Timing proof and its limits

Startup calls SysTick setup `08025c50`, which divides the declared 72 MHz core
clock by 1000, writes reload 71999, and enables the core-clock SysTick. Vector 15
points to `08010188`; that handler increments the tick at `2000070c` once per
interrupt. The nominal tick is therefore one millisecond, assuming the declared
clock matches the running clock.

AC setup `0801f81e` and car/DC setup `0801f34e` allocate **2000-tick one-shot
software timers** with no callback. The policy checks whether the timer is still
active and skips counting until it is inactive. The timer manager
`08010448` expires the timer when unsigned `now - started >= period`.
The policy rearms it using the current tick through `08010508`.

From counter zero and an initialized timer, uninterrupted eligible samples give:

- AC: 451 samples × nominal 2 seconds = **902 seconds**, approximately 15 minutes.
- Car/DC: 9001 samples × nominal 2 seconds = **18002 seconds**, approximately five hours.

These are code-derived nominal intervals, not measured device guarantees.
Late timer polling and controller scheduling extend elapsed time: there is no
catch-up loop that turns one delayed sample into multiple counter increments.
An inherited counter, blocked samples, output initialization, and integer wrap
also change the time until a stop request. The replay covers tick wrap and
late timer polling without simulating real interrupts or the complete scheduler.

## Raw power to telemetry

The policy's power input is also cached for telemetry. AC values `<= 15` become
zero in the cache; car/DC values `< 2` become zero. Above those deadbands, the
signed input is copied as a `uint16` without a scaling calculation:

| Domain | Cache setter | Cache address | Status field |
| --- | --- | --- | --- |
| AC | `08024ebc(7, value)` | `200020ea` | Typed `A6`, `uint16` |
| Car/DC | `08024ebc(3, value)` | `200020e2` | Typed `AD`, `uint16` |

Getter `0801762c` feeds the information structure at `20001374` offsets `0x0c`
and `0x1a`. Serializer `08009164` then emits the typed fields. Twelve cases
execute this getter/store/serializer path with synthetic inputs.

This establishes the same raw scale as those reported power fields. The original
DSP producer and its physical calibration have not been verified here, so the
thresholds above deliberately remain **raw 20 and raw 3**, rather than an
independently proven 20 W and 3 W. Negative signed inputs also enter the low-load
branch; this is an observed firmware condition, not permission to treat invalid
telemetry as a safe low-load state.

## Reproduction

The public input and its provenance are in
[`firmware/c1000_original/1.5.9`](../firmware/c1000_original/1.5.9/README.md).
The exact decoded-image SHA-256 is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_c1000_smart_policy.py \
  --output /tmp/c1000-smart-policy.json
```

Use `--firmware /path/to/MainMcu-decoded.bin` for an external copy. The script
rejects a mismatched image hash. Expected synthetic output is in
[`c1000-smart-policy.json`](../tools/firmware_analysis/expected_results/c1000-smart-policy.json),
with source/dependency digests in the adjacent manifest.

**1,347 cases pass:** 1,300 policy boundaries and guard combinations, four
active-timer skips, two actual Normal-to-Smart command sequences, 12 telemetry
serializations, 15 timer expiry/rearm cases, six SysTick cases, and eight
countdown-handler cases. The policy cases check every changed non-stack RAM byte
against a small expected set.

The replay enters after the outer output-enabled gate. It executes the real
policy arithmetic, mode/blocker getters, GPIO reader, cache stores, software
timer gate, timer manager, and serializer. Peripheral input and elapsed ticks
are synthetic. Substitutions cover stop-event dispatch, logs, interrupt-priority
programming, unrelated SysTick housekeeping, configuration persistence, and
acknowledgments. The final diagnostic log after a stop request is not executed.
There is no BLE, MQTT, cloud, original DSP execution, or physical output testing.

## Further validation

A future test should use only a noncritical original C1000 load, record the
installed firmware and AC/car power separately, and allow for an already
accumulated counter. Before assigning physical thresholds, trace the original
DSP producer or compare calibrated loads against these raw status fields.
The GPIO and global AC blockers are useful remaining targets for offline work.
