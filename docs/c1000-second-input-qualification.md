# Original C1000: second-input qualification and source handoff

Offline investigation dated **2026-10-02**, using public A1761 **main 1.5.9**.
Installed main **1.7.1 / radio 0.3.3.0** differs; its controller and original
radio images remain unavailable. No device, network, physical input, output,
identity or firmware write was accessed.

## Previous evidence and the remaining qualifier

The [AC qualifier](c1000-smart-blocker-followup.md) traced input bit 0, while
the [charging rules](c1000-charge-gate-rules.md) consumed the previously seeded
second-input bit 1. The [source-field audit](c1000-charge-source-provenance.md)
proved BC reflects charging gates, and the [diagnostic audit](c1000-diagnostic-charging-audit.md)
bounded additional command-table routes. This work executes the missing
second-input producer and connects its result to selected existing rules.
It does not repeat their complete earlier suites.

## Second input uses one DSP-cache bit

Actual registration `0800de10..0800de1e` installs debounce slot 1 at
**`2000251c`**, immediately after the AC slot:

| Member | Value |
|---|---|
| Entry predicate | `0802169c` |
| Removal predicate | `08021710` |
| Change callback | `080216b4` |
| Threshold byte | 200 |

Entry reads **bit 4 of the uint16 DSP-cache word at `20002274+4c`**. Removal
is the inverse of that same low-byte bit. Unlike the separately traced AC
entry predicate, these instructions do not check additional fault, power or
presence members. The replay tests every low-byte value against four high
bytes: 0, 1, `80` and `ff`, for **1,024 words**.

Other word bits have unassigned meanings. This is an input-qualification
predicate, not a complete device-health check or a protection audit. Qualified
presence does not prove charging current; the replay qualifies it with all
synthetic power caches zero. It neither assigns the physical DC/solar signal
nor recommends manipulating a reported status word.

The callback sets/clears **input byte `20000690` bit 1**, preserves all other
input bits, records arrival **`0002`** or removal **`0080`** in the change word
at `20000696`, and queues the registered input event. The replay uses synthetic
event ID 9/data 0. If the internal mask includes both values `1` and `2`
(mask `3`), arrival takes priority. These masks are callback arguments, not
external setting values.

## Qualification retains history

The actual sampler `080208a8` executes both AC and second-input slots:

| Sequence | Result |
|---|---|
| Fresh valid second-input entry | Qualifies on sample 202 |
| Invalid entry samples, then valid entry | Invalid samples leave counter 1; qualifies on valid sample 201 |
| Qualified presence, then continuous absence | Clears on removal sample 202 |
| Qualified presence, other word bits changed to `ffff` | Remains qualified through 300 samples because bit 4 remains set |
| 100 removal samples, one present sample, then absence again | Present sample resets removal counter to 0; another 202 absent samples are required |
| Healthy AC and second input arrive together | Both qualify on sample 202; AC callback runs first, then second input, queuing two events |

The previously reproduced registration uses a **10-tick** sampler period.
That permits a nominal delay derivation, but this replay counts calls only:
DSP caching, real interrupts and scheduling can add latency. No guaranteed
physical handoff time is assigned.

Qualification changes only its counters, input flags and event bookkeeping.
The **complete power-state word, charging descriptor, preferences and output
lifecycle RAM remain unchanged**. Event delivery is captured; its dispatcher
and physical DSP/relay are not executed.

## Selected rules consume qualified inputs

Four additional cases explicitly invoke already traced healthy rule groups
after qualification, with synthetic 25°C BMS temperatures:

| Scenario | Qualified AC / second input | Explicit rule | Resulting charging gate / BC |
|---|---|---:|---|
| Second input arrives; raw AC absent | 0 / 1 | 3 | Second gate bit 0 / BC 1 |
| Raw AC present but AC entry blocked by its fault-word predicate | 0 / 1 | 3 | Second gate bit 0 / BC 1 |
| AC qualifies after second input | 1 / 1 | 5 | AC gate bit 5; second gate cleared / BC 2 |
| AC removal qualifies while second input remains | 0 / 1 | 6 | AC gate cleared; second gate set / BC 1 |

The second scenario distinguishes **raw AC presence from qualified AC state**.
It is a synthetic condition, not a supported source-selection method. Separate
fault/protection policies remain outside this bounded handoff. The rule groups
are called explicitly, rather than pretending the captured events were fully
dispatched. Both AC-output and car-output bits, and every unrelated power-state
bit, remain unchanged synchronously; electrical continuity is not measured.

The second-input predicate, removal predicate and callback are absent as direct
entries in the 32 app, 32 module, 14 library and 40/8 diagnostic handler tables.
This excludes direct registration of those three functions only. It does not
exclude every indirect command or later firmware implementation.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_dc_input_qualification.py \
  --output /tmp/original-dc-input-qualification-results.json \
  --manifest /tmp/original-dc-input-qualification-manifest.json
cmp /tmp/original-dc-input-qualification-results.json \
  tools/firmware_analysis/expected_results/original-dc-input-qualification-results.json
cmp /tmp/original-dc-input-qualification-manifest.json \
  tools/firmware_analysis/expected_results/original-dc-input-qualification-manifest.json
```

**1,064 cases:** one registration, 1,024 predicate words, 24 callback masks,
six qualifier sequences, four explicit rule handoffs and five direct-table
checks. Actual instructions execute until the second sampler slot completes;
four additional qualification slots are excluded. Exact low-RAM comparisons
and RAM-only CPU write guards reject unexpected changes and peripheral/flash
writes. Firmware SHA-256:
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Public outputs contain synthetic state only. Exploratory excerpts and sanitized
local capture inventories remain owner-only under ignored
`.solix-private/original-input-qualification-20261002/` (directory 700/files 600).

## Installed firmware and practical next evidence

The retained `4030` version summary has untyped ASCII radio/main strings;
it confirms 0.3.3.0/1.7.1 but supplies no main image or artifact URL. Rechecking
five nonempty retained update logcat recordings plus the completed bugreport
text found no OTA URL, vendor package filename or the identified app download
logging markers, including escaped-slash URL forms. This is a bounded search
of existing captures, not proof that no firmware metadata exists elsewhere.
Raw phone files and the local phone inventories are not published.

The [update-network capture](c1000-original-update-network.md) retains encrypted
S3 traffic; transport byte counts cannot identify or reconstruct the object.
The known 1.5.9 public object path cannot be transformed into a verified 1.7.1
path by changing its version text. An exact vendor artifact URL/package, an
accessible official download, or equivalent independently verified 1.7.1
main/radio image would unlock comparison. The [app OTA capture audit](c1000-app-ota-capture-investigation.md)
describes its conditional URL logging/internal-file boundary.
The new [firmware metadata follow-up](c1000-firmware-metadata-followup.md)
traces the app's actual original/shared check request and its `lastPackage`
URL/hash schema; no 1.7.1 artifact or cloud response was obtained.

No supported original charging-pause, source override or forced discharge
with connected AC was established. A stable, naturally below-full physical
charging interval remains needed for the [native rate/Fast procedure](c1000-native-charging-test-plan.md).
The current 100%/0 W idle baseline supplies no reason to write a rate setting.
Correlated physical second-input presence/power readings would separately help
validate these older qualifier semantics on installed firmware, while leaving
AC output enabled.
