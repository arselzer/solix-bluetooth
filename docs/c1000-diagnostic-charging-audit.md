# Original C1000: diagnostic tables and charging-path limits

Offline follow-up on 2026-10-02 using public **A1761 main 1.5.9**. Installed
**main 1.7.1 / radio 0.3.3.0** differs; neither newer main nor original radio
image is available. No device, network, identity, GPIO, flash or output was
accessed. This extends the [input-event charging-gate investigation](c1000-charge-gate-rules.md)
to the separate diagnostic tables, without adding a runtime command.

## Exact tables behind the diagnostic dispatcher

Actual parser/dispatcher `080276bc` selects two immutable `<uint16 property,
padding, Thumb handler>` tables:

| Inner selector | Table | Entries |
|---|---|---:|
| 0 | `08029b30` | 40 |
| 1 | `08029c70` | 8 |

The replay traverses the already documented [function-0c tunnel](c1000-f0-diagnostic-tunnel.md),
then executes the actual inner parser and table search for all 48 properties.
Each target body is replaced with a routing sentinel in this part of the
audit. Cross-selector requests, unknown properties and other selected selector
values reach no handler. Full table contents are in the expected result file.

Neither AC gate setter **`080241d8`** nor second-input gate setter
**`08024494`** appears directly in these tables. Together with the prior
application/module/library checks, this narrows the search. It does **not**
exclude every indirect callback, other firmware version or radio command.
Diagnostic properties also have their own namespace: diagnostic `D1`/`E5`
must not be confused with normal saved-watts/Fast telemetry tags.

## Selected controls stage requests to the radio

Five plausible diagnostic leads were followed through their actual handlers,
module-stage poller **`08011374`** and request builders. The transport boundary
`08012b38` captures packets instead of sending them:

| Diagnostic property | Cache stage | Function-10 command | Actual payload |
|---|---:|---|---|
| `D2` | 9 | `0020` | A1 = supplied byte |
| `D5` | 12 | `0023` | A1 = supplied byte |
| `F4` | 16 | `0038` | A1 = supplied byte |
| `D4` | 11 | `0022` | Empty |
| `EC` | 8 | `0030` | A1=`01`, A2=`00` in the synthetic cache |

Parameterized cases use bytes 0, 1 and 255. The first poll advances to wait
stage **18** and records the original stage plus a 1,000-tick deadline.
`D2` separately normalizes a network-permit cache member to boolean while
passing the raw byte in A1. These are controller-to-radio requests; their
radio-side meaning and downstream behavior remain unassigned.

Every selected run preserves the seeded complete power-state word, charging
descriptor/policy bytes, BMS cache, input-presence byte, saved configuration,
Smart block and AC/DC lifecycle blocks. Exact low-RAM comparisons permit only
the documented staging/wait bookkeeping changes. This is **synchronous MCU
RAM evidence**, not proof of later radio, rule, DSP, relay or physical output
behavior. No charging-gate function or event is called in these selected cases.

`D6` has a separate delay: its handler stores stage 13, the byte argument and
the current tick. At that same tick the first poll makes no further change.
The later event/radio path is deliberately not executed or proposed for a
device. Other diagnostic bodies contain identity/calibration, storage and
upgrade operations; a successful dispatch alone does not make them sensors
or supported preferences.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/audit_original_diagnostic_charging_paths.py \
  --output /tmp/original-diagnostic-charging-results.json \
  --manifest /tmp/original-diagnostic-charging-manifest.json
cmp /tmp/original-diagnostic-charging-results.json \
  tools/firmware_analysis/expected_results/original-diagnostic-charging-results.json
cmp /tmp/original-diagnostic-charging-manifest.json \
  tools/firmware_analysis/expected_results/original-diagnostic-charging-manifest.json
```

**131 cases pass:** 48 table selections, 53 cross/other-selector rejections,
four unknown-property rejections, one direct-gate table check, eleven actual
staging handlers, eleven actual poller/builders and three initial `D6` waits.
RAM-only CPU write guards reject peripheral/flash writes. Firmware SHA-256:
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Public results use only synthetic state and public firmware addresses;
exploratory excerpts stay in ignored
`.solix-private/original-diagnostic-table-20261002/`, directory 700/files 600.

## Remaining useful evidence

No supported external charging-pause or bypass-disable route preserving AC
output was established. The prior [installed-1.7.1 F0 attempt](c1000-f0-live-transport-limit.md)
lost control and required recovery; this audit does not justify another live
factory probe. Exact newer firmware/radio images would help resolve indirect
routing. Ordinary native charging-power/Fast commands already have successful
setting readback; [a below-full charging interval](c1000-native-charging-test-plan.md)
is needed to measure their physical effect.
