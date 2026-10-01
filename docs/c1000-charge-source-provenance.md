# Original C1000: charging-source and power-field provenance

Offline investigation dated 2026-10-01 using the public **A1761 MainMcu
1.5.9** image. The installed station runs **1.7.1**; this image cannot
validate that newer controller. No device, network, DSP, flash or physical
output was accessed. Existing charging controls and live results remain in
[charging and bypass validation](c1000-bridge-charging-and-bypass.md).

## BC follows internal control flags, not measured battery current

The actual information builder at `08010bf8..08010c32` selects BC's value
from `08017480(8)` and `08017480(9)`. These are getters for bits **0** and
**5** of the power-state word at `200004fc`:

| Bit 0 | Bit 5 | Typed BC value |
|---|---|---|
| Clear | Clear | `01 00` |
| Set | Clear | `01 01` |
| Clear | Set | `01 02` |
| Set | Set | `01 01` — bit 0 has priority |

Bit 5 is the independently traced **AC charging gate**, which selects the
charging-converter branch in [the charging producer](c1000-bypass-firmware-followup.md).
The electrical meaning of bit 0 is not assigned by this replay. BC does not
encode both flags when both are set.

The builder stores the selected byte at information-cache `+0x38`;
`08009164..0800924a` emits BC with type `01`. These instructions do not
consult qualified input-presence byte `20000690`, SOC, battery phase/current,
Fast preference, charging watts, or AC/car output-enable bits. In synthetic
cases, BC can be zero with a nonzero input-power cache, or two with every
input-power cache zero. Neither observation proves a fault or bypass mode.

Eight complete setting/readback round trips execute the real Fast `005e`
and charging-power `0044` handlers. Fast on/off and the normal ceiling
1000 → 100 → 1000 leave BC unchanged under each flag combination. The
handlers do not change those source flags. The charging policy and DSP are
not run between writes; this does not rule out a later policy effect.

Keep `charging_source_code` raw in the runtime library. A main 1.7.1
correlation is still needed before introducing descriptive source labels.
BC alone cannot establish mains presence, active battery charging, battery
operation, or a safe permission to operate a bypass relay.

## AF sums the input-power cache in this image

The information builder calls these actual getters before serializing the
ordinary typed `02 + uint16` values:

| Field | Information-cache member | Getter and source |
|---|---|---|
| A5 | `+0x0a` | `08017618(7)`, input-cache member 7 |
| AE | `+0x1c` | `08017618(8)`, input-cache member 8 |
| AF | `+0x1e` | `08017644`, sum of all ten input-cache members |
| B0 | `+0x20` | `08017660`, sum of all ten output-cache members |

Input members are ten `uint16` values beginning at `200020c8`; output
members begin twenty bytes later. AF is therefore an aggregate of cached
inputs in this controller, rather than an isolated photovoltaic measurement.
This does not establish what each hardware producer measures or calibrate
its electrical accuracy. In particular, A5 must not be assumed to measure
all mains power passing through to an AC load.

The getters accumulate a wider sum, then the information builder stores only
its low 16 bits. Synthetic sum 65536 becomes zero on the wire; 655350 becomes
65526. Normal station operation is not shown to reach these sums. This is a
serializer boundary, not a physical overflow measurement.

## Reproduce and limits

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_charge_source.py \
  --output /tmp/original-charge-source.json \
  --manifest /tmp/original-charge-source-manifest.json
cmp /tmp/original-charge-source.json \
  tools/firmware_analysis/expected_results/original-charge-source-results.json
```

**96 cases pass:** 64 source/output/input-presence/power combinations, twenty
individual cache-member probes, four sum boundaries and eight setting round
trips. Actual getters, information-cache writes and typed serialization run;
setting ACK and persistence scheduling are captured substitutes. RAM checks
verify unchanged power caches, source/output flags and protected preferences,
with the original settings restored after each round trip. No whole firmware
scheduler, real current, physical input removal or transport is simulated.

The input SHA-256 is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Expected results and the adjacent manifest contain only synthetic values and
public firmware metadata. Private exploratory excerpts are retained under
ignored `.solix-private/original-charge-rule-followup-20261001/`.

No new external command to disable input or select battery operation was
identified. The useful next evidence is main 1.7.1 field correlation during
an independently verified charging interval, followed by an exact-version
controller/DSP trace if the missing firmware becomes available.
