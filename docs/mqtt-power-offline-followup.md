# Native MQTT power control: offline firmware follow-up

## Scope and evidence

Analysis on 2026-09-29 used recovered **C1000 Gen 2 main firmware 1.1.4.9**,
its radio firmware, retained captures, and the existing local checkout of
`anker-solix-api`. This follow-up made no device, SSH, MQTT, or cloud request.
The C2000 Gen 2 runs **2.1.6.4**; its matching behavior is evidence, but does
not establish that every C1000 implementation detail applies to it.

See [the live reconnect and tariff experiments](c2000-mqtt-reconnect-and-tariff.md)
for C2000 results. Native status, readiness and charging-power control work;
the tested Peak plans still did not activate a tariff or discharge the battery.
The [later encoding audit](c2000-tou-encoding-audit.md) shows those plans were
malformed, correcting their earlier interpretation as valid all-day slots.
The [DSP producer trace](inverter-dsp-investigation.md) subsequently identifies
the C1000 gate input as qualified AC input; neither result proves C2000 discharge.

## Clock semantics are more specific than a timestamp acknowledgement

The C1000 controller's **internal function `10`, opcode `0026`** handler
(`0800cc18`) receives an untyped four-byte UTC value and four-byte signed
offset. This is a clock **write**, distinct from ordinary app function `0f`.

| Operation | Recovered behavior |
| --- | --- |
| Incoming UTC `0` or `ffffffff` | Keep the cached UTC value. |
| Incoming offset `0` or `ffffffff` | Keep the cached offset; zero does not clear it. |
| Other changed offset | Update cached and persisted offset. |
| RTC synchronization | Attempt to set `RTC = cached UTC − cached offset`, modulo 2³². |
| Status `FE` | Serialize type `03` followed by `RTC + stored offset`, little endian. |

The RTC synchronization helper `08014668` calls `08029d24(2)`. The argument
is a polling timeout; the helper waits for bit 5 of peripheral register
`40002804`. A timeout skips the RTC write, yet the enclosing `0026` handler
still returns acknowledgement `00`. Consequently, neither that acknowledgement
nor a plausible `FE` alone proves the RTC's local-hour interpretation.

Fourteen instruction-level replay cases cover positive and negative offsets,
the zero/−1 sentinels, invalid-UTC sentinels, and successful/timed-out RTC
polling. The handler, offset store, register helpers and `FE` builder execute
actual ARM instructions. Peripheral values are synthetic; logging, ticks,
persistence backend, response transport and a five-byte copy are substitutes.

Radio static tracing also found a sign conversion: timezone update code near
`420383cc` passes the negated timezone offset to callback `4203b906`, using
`−1` when the source offset is zero. This agrees with the controller's
subtraction convention. That radio branch was not replayed or changed live.

## An all-day slot does not depend on a correct clock

The actual C1000 tariff selector `0801bcc4` requires mode 1, the readiness
predicate, and separate power-state bit `200004be.0`. It then reads RTC and
converts the epoch through `08005210/0800521c`. The hour calculation is simply
`(epoch // 3600) % 24`; there is no additional timezone or valid-clock check
in this path.

Forty-three actual-code cases, with **no substituted function on this path**,
establish:

- A `00:00–24:00` slot matches every hour, including RTC `0` and `ffffffff`.
- Start is inclusive and end is exclusive: `00:00–23:00` excludes hour 23.
- The first matching slot wins when slots overlap.
- An overnight interval needs two slots; `23–2` never matches directly.
- Missing mode/readiness/power gate, or an empty schedule, returns tariff 0.

Thus clock error alone cannot explain inactive tariff during a correctly
decoded **all-day** C2000 trial if its selector follows this C1000 code.
This does not prove the C2000 power gate is false: a model-specific selector
or plan representation remains possible. Mains-present and AC-enabled fields
do not expose the complete gate; see [the gate analysis](firmware-findings.md).

## Native charge-cap layout and reserve side effects

The native app command is `0103`, pattern `03 00 0f`, with `A1=22`.
BLE uses `4103` and `A1=21`. Their setting fields have the same typed layout:

| Field | TLV bytes | Meaning |
| --- | --- | --- |
| `AA` | `aa 02 01 <upper>` | Upper charging percentage. |
| `AB` | `ab 02 01 <lower>` | Lower discharge percentage. |

`01` is the one-byte integer type marker, not the first percentage. Omit `AB`
when changing only the upper cap. Preserve the established native timestamp
convention. The existing upstream map uses this layout for A1763 and A1783/A1785;
the C1000 recovered handler independently confirms it.

The C1000 handler `0800c530` reads value byte 1 and calls these setters:

- Upper setter `0802b504`: store upper at settings `+17`; if reserve at `+19`
  exceeds the new upper value, reduce reserve to that value.
- Lower setter `0802b528`: store lower at `+18`; if reserve is below
  `lower + 5`, raise reserve to that value.
- Both schedule settings persistence. Neither setter enforces the app's
  percentage ranges by itself.

Sixteen offline executions run the real TLV parser, lookup, `0103` handler and
setters. They cover all five app upper values, low/high starting reserves,
all five lower values, and absent fields. Persistence scheduling, display timer
status, refresh and response handling are substituted. This verifies the
software mapping and side effects, not physical charging or C2000 equivalence.

For verification, standard status `D9` raw values including their binary type
byte contain reserve at index 3, upper at 4 and lower at 5. The C1000 `D9`
builder `080190d4` reads the same getters used by these setters. Compare all
three values after a cap change; upper-only does not universally mean reserve
is unchanged. Keep the established upper 80–100% in 5% steps and lower
1/5/10/15/20% validation.

## Remaining diagnostic leads and boundaries

No newly audited request is established as a strictly passive query of the
complete power gate or raw RTC on C2000. The following distinctions prevent
misclassifying commands as harmless status reads:

| Candidate | Finding |
| --- | --- |
| App `0089` | Already verified on C2000; readiness prefix, not the inverter gate or RTC. |
| App `0066`, page 0 | C1000 handler `0800bacc` includes raw RTC as `[%08X]` plus device identity/version data. Every response calls `08015710(1)`, creating/restarting a 10-second log timer. Later pages also move log-read cursors. Not a strictly passive query. |
| App `0092` | Returns plan metadata/string but clears pending plan-status flag when it equals 2. No direct raw-clock/power-gate field found. |
| Internal `10/0026` | Changes clock/offset; do not treat it as a clock query. |
| Internal `10/0066` | Different radio Modbus configuration handler; do not confuse with app log download. |

The log timer callback `0802009c` cancels its timer and clears the log flag.
Its wider interaction with logging and the C2000 implementation still needs
review before considering a bounded log-page request. Any log response can
contain private identity data and belongs only in `.solix-private/`.

The safest next native observations remain fresh `0100`/`0900` status, the
existing `0057` status subscription, `0089` readiness, and radio `0027/0028`
connectivity diagnostics. Retain raw `D9` and `FE` alongside interpreted fields.
These cannot by themselves distinguish a false inverter gate from different
C2000 tariff semantics. Do not compensate by writing internal flags or time.

## Additional charging leads

Static C1000 tracing shows that `0101/A7` fast-charge enable is ignored while
any tariff is active; a disable request still reaches its setter. The power
controller also clears fast charge if the power gate is absent or a tariff is
active. An acknowledgement therefore does not guarantee fast charge changed.

The configured charging-power setter stores the supplied halfword directly;
a separate settings validator checks 100–1200 W in this C1000 firmware.
This does not establish safe operation below the library's tested minimum,
nor a zero-watt charging-disable command. Keep existing model-specific limits.

Tariff energy accumulation is another offline lead: `0802d880` updates
SolarToBatt/GridToBatt/BattToLoad accumulators and tariff duration counters only
while a tariff is selected. Report builder `0802db30` divides energy sums by
360. Its external report route and full field schema remain unmapped; no new
native energy-counter request is recommended yet.

### Static report-route follow-up

Further C1000 tracing found no direct call to `0802db30`, but a Thumb function
pointer at `0802e1f0`. The periodic accumulator loads that builder and completion
callback `08029330` at `0802e1d2`, then calls `08013d94`. That helper queues a
descriptor through `08016a70` with command halfword **`0401`** and function
**`0f`**. The builder formats a larger structured/JSON report containing the
named tariff accumulators and durations. This establishes its internal queue
route, not a safe incoming query or the radio's final MQTT representation.
The descriptor layout, dispatch translation and completion semantics still
need tracing. No corresponding report was requested live.

The retained Anker Solix API reference at `c2f8769` separately maps C2000
**`0503` on `state_info`** to six unknown unsigned energy values in `A2` at
four-byte offsets 0–20, plus an `A3` timestamp. Its suggested scale is `0.001`
and its publication interval is explicitly uncertain. Those unnamed fields
must not be equated with the firmware's named tariff counters without evidence.
The local endpoint retains these raw packets if received, but does not expose
them as named energy sensors or issue an invented polling command.

Private `energy-report-callers.json`, `energy-report-registration-disassembly.json`
and `energy-report-callback-disassembly.json` retain the static inspection.
No firmware execution or new hardware test was added for this route.

## Private reproducibility

Scripts and JSON results are retained under ignored, owner-only
`.solix-private/firmware-analysis/`:

- `emulate_clock_semantics.py`: 14 clock cases.
- `emulate_tariff_clock.py`: 43 complete selector cases.
- `emulate_soc_cap_handler.py`: 16 native settings-handler cases.

Run with `PYTHONPATH=/tmp/solix-analysis-tools python3 <script>` in the retained
analysis environment. These are synthetic firmware experiments, not hardware
tests or a redistributable firmware dependency. Raw firmware, captures,
identifiers and credentials are deliberately excluded from Git.
