# Original C1000: input-event charging gates

Offline investigation, 2026-10-02, using public **A1761 main 1.5.9**. The
installed station runs **1.7.1**, whose controller image is unavailable.
These instructions do not validate that newer firmware. No device, network,
identity, flash, physical input or AC output was accessed.

## Bit 0 controls the second charging channel

The previous [source-field investigation](c1000-charge-source-provenance.md)
proved BC's priority mapping: bit 0 → code 1, otherwise bit 5 → code 2.
This follow-up traces the previously unresolved bit 0 farther downstream:

| Item | AC charging channel | Second charging channel |
|---|---|---|
| Power-state gate at `200004fc` | Bit 5 | Bit 0 |
| Internal rule action | `080241d8` | `08024494` |
| Producer | `0801f570` | `08020380` |
| Internal channel argument | 1 | 2 |
| Parameter-update register | `0004` | `0016` |
| Stop register / value | `0000` / `0054` | `0015` / `0058` |
| Input-power cache member | 7 | 8 |

The second producer consumes the same **`20000424` descriptor and dirty
byte `+6`** as the AC producer. Its lifecycle bytes are at `2000000c`;
AC uses `2000001c`. The second gate is associated with firmware events logged
as `portDcIn` / `portDcOut`. This establishes a second input/charging
channel in the MCU, rather than an AC bypass-selection bit. It does not
calibrate hardware measurements or prove active charging current.

The actual bit-0 setter uses the argument's low bit, except `002f` means
unchanged. It preserves every other power-state bit. This internal function
accepting an argument is not a new external setting command.

## Actual rule selection gives AC priority

Startup `08006a4e..08006ac2` registers twelve immutable callback groups at
`200020f0`. Engine `08015120` evaluates each row's predicates in order and
runs the first matching row's actions. The replay executes registration,
predicates, row selection and gate writes, rather than forcing a row result.

With synthetic noncritical BMS state and low/high temperatures both 25°C:

| Group / event path | Charging actions |
|---|---|
| 3, second-input arrival (`0800729e`) | Set bit 0 when qualified AC-input bit 0 of `20000690` is clear; otherwise clear it. Preserve bit 5. |
| 4, second-input removal (`08007390`) | Clear bit 0; preserve bit 5. |
| 5, AC-input arrival (`0800705e`) | Set bit 5 and clear bit 0. |
| 6, AC-input removal (`080071a6`) | Clear bit 5; set bit 0 only when qualified second-input bit 1 of `20000690` is set. |

Both AC-output bit 4 and car/DC-output bit 1 remain unchanged in these
selected rules. Their separate callback/event dispatcher and physical DSP
are not run, so this is controller-RAM preservation, not an uninterrupted
AC-output test.

These are **input/BMS event rules**, not app-selectable charging modes.
The application, module and library handler tables do not directly register
either charging-gate setter. That bounded table check does not exclude every
indirect path or undocumented operation.

### Matching depends on the sampled BMS state

The normal rows match cached low temperature ≥2°C and high temperature
≤55°C. At exactly 2°C, the normal row precedes another matching cold row.
With both temperatures 1°C or both 56°C, other rows disable the selected
charging gates and can request an alarm. These are synthetic software
predicates, not safe operating-temperature advice or physical fault tests.

A deliberately mixed low/high pair 1/25°C matches none of the tested groups
3/5/6 rows: both charging-gate bits retain their seeded values. Independent
periodic policy, BMS protections and other event paths still exist; this
bounded result is not a whole-device protection audit. Likewise, seeding
critical global bit 20 selects the first row, with different gate clearing
in groups 3/4 versus 5/6. No fault state was sent to hardware.

## Second-channel queue proof and shared descriptor

Actual second-channel producer instructions and packet builders were run
with synthetic existing-session state. With bit 0 clear, they clear
input-cache member 8 and, if the start/stop lifecycle byte is set, queue
register `0015`, value `0058`. With bit 0 set, an existing session consumes
a dirty descriptor and queues register `0016`. A clean descriptor creates
no new update. Startup produces its own initial parameter request.

Zero requested power still reaches the active parameter-update path and
leaves a nonzero current member. It is not a verified pause command. Variant
0/1 also take different current/power caps; those raw branches are not a
validated device/model classification or an electrical charging-rate claim.

Two synthetic cases enable both gates and invoke the producers in opposite
orders. The first consumes the shared dirty byte; the second queues no new
update. This exposes a dependency on the shared descriptor, not a physical
dual-input experiment or the real scheduler's order. Normal arrival rules
already supply the AC-priority mechanism described above.

## Reproduce and remaining controls

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_charge_gates.py \
  --output /tmp/original-charge-gates.json \
  --manifest /tmp/original-charge-gates-manifest.json
cmp /tmp/original-charge-gates.json \
  tools/firmware_analysis/expected_results/original-charge-gates-results.json
```

**85 cases pass:** one real registration, 32 normal rule cases, fifteen
sampled-temperature cases, four critical-row cases, sixteen bit-0 setter
cases, twelve second-producer cases, two shared-descriptor orders and three
handler-table checks. Event enqueue, alarm delivery and DSP allocation/queue
handoff are captured substitutes. The DSP, peripherals, whole event
dispatcher, scheduler, persistent storage and installed 1.7.1 are absent.

The controller SHA-256 is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Expected results and manifest use only synthetic state and public firmware
metadata. Exploratory excerpts remain owner-only in ignored
`.solix-private/original-charge-gates-20261002/`.

No supported external command selecting battery operation while AC input
remains connected was established. Setting charging watts or Fast does not
expose these input-event actions. Even an internal charging-converter stop
would require separate DSP/relay evidence before claiming bypass has ended.
No guessed rule event, temperature, fault or diagnostic write is added to the
runtime library. Exact main 1.7.1 firmware and a correlated physical charging
interval remain the useful next evidence.
