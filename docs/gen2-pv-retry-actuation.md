# C1000 Gen 2: what the PV retry path actually controls

## Result and boundary

On **A1763 / C1000 Gen 2 main 1.1.4.9**, the previously traced
[brightness-triggered retry](gen2-pv-retry-origins.md) reaches the internal MPPT
command queue. The synthetic falling input transition requests stop. A permitted
rising transition sends configuration, then requests start after a successful
configuration response. The DSP recognizes those commands and applies further
fault checks before setting its run-request flag.

This is **140 new offline instruction-replay cases**, using the published main
and DCDC images. It does not establish that a real panel resumes charging. No
station, network or app was accessed, and no recovery API or runtime behavior
was changed. These binaries establish neither original C1000 nor C2000 behavior.

## The input flag is qualified DSP state

The main MCU's module word at `20003f4c` comes from internal register `0126`:
receiver `0801b54c` maps register `0100+n` to `20003f00+2*n`. The DCDC read table
at DSP **word address** `082f00` maps `0126` to getter `086acf`, which returns
RAM `a49e`. The ACDC table's shorter range does not explain this register.

DCDC updater `084a5d` controls bit 5 as follows:

| Inputs | Result for register `0126` bit 5 |
| --- | --- |
| Qualifier `aaae.bit0` is clear | Keep the previous value, including a previous 1. |
| Qualifier set, mode `acea` equals 4 | Clear. |
| Qualifier set, either `ad17` or `ad16` nonzero | Clear. |
| Qualifier set, mode differs from 4, both words zero | Set. |

The actual updater, getters and setter execute in 72 cases. All other bits are
preserved. The qualifier is produced by the timer-processing routine at
`089466`; its tail at `0894ea` latches bit 0 after a counter threshold. It is not
a direct PV voltage reading. Its physical timebase was not replayed. The mode
and fault-word producers were also excluded.

Therefore, calling `20003f4c.bit5` a raw “panel connected” signal would be too
strong. It is a qualified module state that can retain an earlier result. The
main MCU interprets it as its DC-input event source; the port can also have
car/XT60i behavior. Actual PV availability and charging power require telemetry.

## Debounced event to main-controller policy

Callback `0802601c` converts the debouncer's active/inactive results into
pending masks `0002`/`0080` at `200004c4`, updates `200004be.bit1`, and marks an
event. Handler `08007258` consumes those masks through policy ports 3 and 4.

The policy descriptors are compressed initialized data. This replay executes
the firmware decompressor at `08005b6a` with source `080353d4`, recovering the
real descriptors at `20000174`. It then uses the real ROM rows at `0803265c`
and `08032724`, callback table `08031b6c`, and policy executor `08015fb4`.

- Falling input clears the controller's DC-input bit, `20000164.bit0`.
- Rising input sets that bit only if `20000164.bit23` is clear **and** scalar
  getter kind 11 returns nonzero. The latter reads halfword `2000040c`; its
  physical meaning has not been established. Zero blocks the rising transition.
- The policy also requests display behavior. This is more than a telemetry
  flag assignment.

Sixteen combinations cover previous state, both guards and event direction.
They preserve the complete saved-settings block and the AC/DC output-state
bits in the synthetic baseline. The baseline is already awake. Initial wake
actions are excluded, and the separate XT60i auxiliary configuration branch is
recorded as a substituted boundary rather than executed.

## Main-controller MPPT commands and race handling

Task `08025140` consumes `20000164.bit0`. The real command builders
`0802a590` and `0802a664` populate descriptors for internal selector 1:

| State | Queued request |
| --- | --- |
| Inactive, no previous command state | No stop request needed. |
| Inactive, previous command state present | Register `0015`, word `0058`. |
| Active, configuration not yet started | Register `0016`, six configuration words, callback `0801649c`. |
| Configuration callback reports success, input still active | Register `0015`, word `0057`. |
| Configuration callback reports success, input now inactive | Register `0015`, word `0058`. |
| Configuration callback reports failure | No start request. |

The callback reads the **current** input state. An input lost while configuration
is outstanding therefore does not leave a stale start request in this tested
path. Allocation and queue failures are covered separately. The inactive path
also clears task state and an input-power cache.

The seven cases execute the real task, builders and callback, substituting
allocation/free and the internal queue. Configuration contents depend on
synthetic BMS/module state; no new physical units are assigned to those words.
The command descriptors are evidence of queued intent, not UART delivery or a
physical charger response.

## DSP receipt and remaining actuation guards

Static receiver `085fb1` places a register `0015` write in RAM word `0419` and
marks the corresponding pending bit. Starting at its selected consumer branch
`0866bf`, five actual-instruction cases establish:

| Register `0015` value | Event bank 0 |
| --- | --- |
| `0057` | Bit 3: start request. |
| `0058` | Bit 4: stop request. |
| `0059` | Bit 5; not proposed as a command. |
| Tested other values `0000`, `0056` | No event. |

The real event-bit writer `0898dc` runs. The replay assumes the register's
pending bit was selected; it does not execute UART reception or the surrounding
64-bit pending-mask dispatcher.

In DSP mode 1, handler fragment `08744c..087464` consumes start/stop events:

- Start sets run-request word `acf1` to 1 only if `ad17==0` and `ad16` bits
  **4, 5 and 1** are clear. These tests use getter `0891b3`'s one-based bit
  arguments 5, 6 and 2.
- A blocked start leaves the previous run-request value intact at this point.
  It does not itself prove that fault handling elsewhere permits continued power.
- Stop clears the run request regardless of these guards. If both start and
  stop are present, stop wins.

Forty cases execute this fragment and its actual fault getters without replacing
their return values. Other event bits, the entry prologue and subsequent periodic
converter sequence are excluded. Static continuation `087464..0874a1` additionally
checks periodic activity, faults, a counter threshold and the run-request flag
before peripheral writes and a transition to mode 2. This continuation was not
replayed, and its timing and physical switching effects remain unverified.

## Practical consequence

The existing same-value nonzero brightness command has a plausible, now longer
traced path to stopping and restarting the MPPT controller. It is still only a
**candidate recovery procedure**. Faults, module state, policy guards, response
failure and physical input conditions can prevent charging from returning.

Any future physical check needs a naturally locked PV input, fresh SOC above
the previously identified ≤1% side-effect boundary, a complete settings/output
baseline, and actual input/battery power observations afterward. Keep the
existing brightness command's Standard/no-active-tariff and display-schedule
guards. A cleared lock or queued start alone is insufficient. No automatic
retry feature or internal-register command is justified by this replay.

## Reproduction and provenance

Use the [existing dependency setup](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-pv-actuation \
  python3 tools/firmware_analysis/emulate_pv_retry_actuation.py
cmp /tmp/solix-pv-actuation/pv-retry-actuation-results.json \
  tools/firmware_analysis/expected_results/pv-retry-actuation-results.json
```

The input hashes are enforced before execution:

- `MainMcu-decoded.bin`:
  `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
- `dspDCDC-decoded.bin`:
  `7ae2bb915812441b8316d1c3f3efc2379fcb488d4d03e950901cf507c224ceb8`.

The DSP container is parsed with per-record CRC checks. A deliberately limited
C28x integer interpreter rejects unimplemented instructions; it is not a general
DSP or peripheral emulator. ARM execution uses Unicorn. The manifest records
runtime versions, helper hashes, substitutions and the result digest.

Case counts: **72 status predicate + 16 main policy + 7 queued MPPT paths +
5 DSP command events + 40 DSP request guards = 140**. The inherited LCD,
persistence, scheduling, ACK/logging and memory-service substitutes remain in
place. All published results are synthetic; no phone data or identifiers are
included.
