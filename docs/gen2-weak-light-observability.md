# C1000 Gen 2 solar weak-light lock telemetry

## Scope and useful result

The Python decoder now exposes **`pv_weak_light_locked`**, a passive diagnostic
integer (`0` or `1`), for **C1000 Gen 2 / A1763**. It reports the controller's
MPPT weak-light lock state from an existing status field. This can help explain
solar charging that has stopped retrying, alongside `dc_input_active`,
`dc_input_power_raw` and `controller_error_code`.

Evidence comes from the published **main 1.1.4.9** firmware and **73 synthetic
instruction-replay cases**. No station, solar panel, app or network was accessed.
Physical low-light behavior is **not validated**. A zero lock value does not
establish that sunlight is sufficient, the input is connected, or charging is
possible. These findings do not establish the same field for original C1000,
C2000 or other firmware.

### Separate unlocked-state capture

During the independent [original Fast SDK repeat](c1000-prime-fast-validation.md),
the upstream C1000 Gen 2 contributed seventeen stored status snapshots, all
with exact 14-byte type04 A3 and byte13=0. The current decoder reported
`pv_weak_light_locked=0`. This confirms the observed unlocked wire shape on
main 1.1.4.9; no connected panel or physical locked-state transition was tested.
The station captures remain in the ignored private evidence archive.

## Exact field and provenance

Offsets include the type byte in the TLV value, excluding tag and length:

| Item | Exact evidence |
| --- | --- |
| Status field | A3, exactly 14 bytes, leading type `04`, byte 13 |
| Registration | `08032d88`: tag A3, type 04, builder `0801a2a8` |
| Getter | `08015494`: extracts bit 1 of byte `2000039a` |
| Full serializer | `0801a34e` calls the getter |
| Incremental serializer | `0801a39c` also refreshes the getter |
| Producer | `0800d5d0`, with a firmware log identifying the MPPT weak-light lock |
| MPPT caller | `08025214` and `080253a8`; a newly set lock leads to the stop-retry path |

The decoder requires `Model.C1000_GEN2`, that exact length/type, and a byte value
of 0 or 1. Missing, malformed or unknown values produce no metric; they do not
imply an unlocked state. Existing error/work fields remain independent. The
serializer also copies this flag in internal update mode 3, unlike some fields
that retain an earlier buffered value. Packet delivery and polling freshness
still matter.

The firmware naming and caller behavior support **weak-light lock** as a
description of software state. The underlying physical causes and DSP status
bits have not been fully calibrated; this is not a measured sunlight sensor or
a named hardware fault.

## Lock, persistence and retry behavior

The state block begins at `20000394`; its flag byte is at offset 6 and event
counter at offset 7. In the mode-1 branch of `0800d5d0`, a nonzero event argument
increments the counter only when the previous mode nibble is 1 or 2. The fourth
qualifying event sets bit 1, clears the counter, and returns a newly-locked
indicator. Starting from another mode first establishes mode 1, so that first
call does not count. A zero event argument does not accumulate failures.

The producer compares the state word with backup register index 2 and can save
it there. Boot routine `08008024` reads that register and retains the word only
when its validity bit 0 is set. This is evidence of retained lock-related state;
the replay substitutes backup-register IO and does not establish behavior
through an actual power cycle.

The mode-0 branch clears the flag word and counter under the tested normal
global state. This is an **internal function**, not an exposed clear-lock
command. No new control or recovery command is provided.

### The timer is conditional

Routine `08008024` registers callback `08030418` with period argument 1000. The
callback has these independently tested branches:

- Global state bit 23 suppresses its work.
- A user-action flag (bit 3) takes the internal clear path, then sets a separate
  retry flag (bit 2) and resets the stored RTC start value. Other global guards
  exist in the called clear function, so this is not a universal unlock promise.
- If unlocked, it resets the stored start value.
- While locked, a separate activity byte or module bit resets an invocation
  counter. Without either, the 16th callback enters the internal clear path.
- An elapsed RTC difference **greater than 600**, rather than equal to 600,
  sets bit 2 and invokes the low-SOC action helper. **Bit 1 remains set** in this
  branch. Consequently, a ten-minute deadline does not mean the reported lock
  must become zero.

An internal remaining-time getter at `080198f8` clamps an RTC difference to 600.
It was replayed at its boundaries, but no external readback for that countdown
was established. In particular, A3 bytes 11–12 are a **literal 600** from
`0801a214`, not a decreasing countdown. A3 bytes 9–10 are derived from the BLE
transport budget, not a new fault mask.

## Reproduction and limits

Use the existing [offline dependency and firmware input instructions](firmware-analysis-reproduction.md).
The new standalone suite is
[`emulate_weak_light_status.py`](../tools/firmware_analysis/emulate_weak_light_status.py):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-weak-light-replay \
  python3 tools/firmware_analysis/emulate_weak_light_status.py
cmp /tmp/solix-weak-light-replay/weak-light-status-results.json \
  tools/firmware_analysis/expected_results/weak-light-status-results.json
PYTHONPATH=python python3 -m pytest python/tests/test_pv_weak_light.py -q
```

The **73 replay cases** comprise 32 getter/full/incremental serializer cases,
8 event sequences, 16 internal-clear cases, 10 timer cases or sequences, and
7 internal remaining-time boundary cases. The decoder has **16 focused tests**
covering valid values, independent work state, absent/invalid layouts and model
isolation.

Actual ARM getter, serializer, mode-1 accumulation, mode-0 clear, timer and RTC
read branches execute. Backup-register IO, logging, memory copy/clear and the
low-SOC action helper are substitutes; the latter records requested arguments
without performing power actions. The physical MPPT/DSP producer, scheduler,
ADC/PV input, charging electronics and app behavior are not executed. The
mode-2 physical-threshold branch is not replayed. Synthetic states can represent
combinations that real hardware never maintains.

Input `MainMcu-decoded.bin` SHA-256:
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
The input hash/size is checked before execution; the result manifest records
helper hashes, Python/Unicorn versions and substitutions. Published results
contain only synthetic data and firmware observations.

The useful next physical check is passive collection while an already connected
PV source naturally transitions through low light. Compare complete A3/A6/A8
status with the app or a meter; preserve raw reports. No induced fault, reset,
output toggle or lock-clearing experiment is needed to validate this metric.

The [external retry-origin follow-up](gen2-pv-retry-origins.md) adds 59 cases
tracing the existing brightness command into user-action, timer and input-retry
paths. A same-value nonzero brightness write is a candidate for a later physical
trial in a naturally locked state; low-SOC side effects and physical timing
remain unresolved. This supplies no automatic recovery API.
