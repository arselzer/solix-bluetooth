# Original C1000: Fast status retention and battery-state readback

Offline follow-up dated 2026-10-01 using the public **A1761 main 1.5.9**
controller image. The available station runs **main 1.7.1, radio 0.3.3.0**;
its main image is unavailable here. These instruction addresses and synthetic
results do not validate Fast over that station's Prime or native transport.
The subsequent [Prime hardware trial](c1000-prime-fast-validation.md) independently
validated Bluetooth retention, restoration and input-loss clearing on 1.7.1;
the [separate native trial](c1000-native-fast-validation.md) subsequently
validated native MQTT retention and restoration at full SOC.
The instruction replay accessed no device, network, cloud, output or
persistent storage. A separate retained-capture comparison is described below.

This extends the earlier [timer and mode investigation](c1000-timer-and-mode-followup.md)
with complete charging-policy execution and actual status serialization.

## E5 reports the live preference, including at full SOC

The `005e` application handler at `0800bd00` records the last requested byte
at `20000d10`, normalizes it to boolean and calls `08024c18`. That setter
changes bit 2 of `200020c4`. The BLE command is `405e`, with A2=`01 00` or
`01 01`; the native command is `005e`. Command acceptance and retained state
are separate observations.

The E5 serializer at `080093cc..080093f0` calls getter **`080173a8`**, which
extracts the current bit from `200020c4`. The complete emitted TLV is:

```text
E5 02 01 value
         00 = Fast flag clear
         01 = Fast flag set
```

It does **not** serialize the last-request cache. A request can leave that
cache at 1 while a later policy run produces fresh E5=`01 00`. An ACK, or the
last requested value, therefore cannot establish retention.

The entire charging callback **`08014118`** was executed 120 times for each
of 64 synthetic conditions: SOC 99/100, eight starting policy states,
primary/expansion BMS selection, and AC-input presence absent/present.

| Condition | E5 after every callback |
|---|---|
| Input present, SOC 99% | `01 01` |
| Input present, SOC 100% | `01 01` |
| Input absent, either SOC | `01 00`, while the request cache remains 1 |

All cases preserve the complete F8 value, saved Smart configuration block,
saved charging ceiling, output/charging flags, and output countdown words.
The callback changes internal policy states and descriptors; those are not
measured electrical behavior. Repetition here is 120 callback invocations,
without advancing a simulated clock or running the whole firmware scheduler.

Thus **SOC 100% alone does not clear Fast in these 1.5.9 paths**. Fresh E5 can
confirm a retained preference at full charge without requiring charging
current. It does not prove accelerated charging, available mains capacity,
input-source selection, or retention through a reboot.

## Other clear paths and limitations

These five direct calls to the setter were identified in decoded code:

| Call site | Effect |
|---|---|
| `08005f20` | Sets Fast for an internal event logged as `startSuperChg` |
| `08007284` | Clears Fast in the AC-input removal event tail (`portAcOut`) |
| `0800bd14` | Sets/clears according to application `005e` |
| `08014132` | Clears when bit 0 of the input-presence byte `20000690` is absent |
| `08023ba0` | Clears as part of the broad defaults routine `08023b6c` |

`08005f14` is an internal code address, **not an external command `005f`**.
Only the small Fast-clear block within defaults was replayed. The enclosing
defaults routine replaces multiple settings and is unsuitable as a live Fast
test. Direct-call enumeration also does not exclude initialization, indirect
calls, reset, or other writes elsewhere in the complete system.

Four further runs set the selected BMS byte `+0x2d` or `+0x31` to 1, for
each BMS selection. They select policy states 5 or 7 respectively and leave
E5 enabled through all 120 calls. The first byte participates in the BMS
communication-failure path; no user-facing meaning is assigned to `+0x31`.
These cases reinforce that E5 alone does not mean charging is occurring or
permitted. No additional normal-operation clear beyond the input-presence
path was established in this bounded investigation.

## Useful monitoring candidate: BF and C0

The original model's BF/C0 fields are **typed byte BMS state codes** for
the main/expansion battery:

| Stage | Evidence in 1.5.9 |
|---|---|
| Incoming BMS state | `uint16` at frame `+0x34` |
| Parser copy | `080134c2..080134c8` stores its low byte at BMS `+0x25` |
| Main/expansion BMS | `200005d4` / `2000060c`, 0x38 bytes apart |
| Getters | `08016790` / `08016796`, cases 4/5 of `08016744` |
| Information builder | `08010c88..08010c9a`, bytes `+0x3b` / `+0x3c` |
| Status serialization | `08009164..0800924a`, BF/C0 value `01 code` |

Sixteen synthetic combinations of 0, 1, 2 and 255 prove exact readback for
both fields, including unrecognized values. The parser branches on code 2 at
`080134aa` / `08013540`, selecting incoming current at `+0x44` instead of
`+0x40`. Its subsequent remaining-capacity calculation also distinguishes
code 2; `0801683c` uses the same code to reconcile the remaining-time estimate
with net power. This supports the charge-side interpretation of **2**.

This first trace did not assign names to codes 0 and 1. The subsequent
[BMS producer replay](c1000-battery-phase-firmware.md) proves delayed charge/discharge
phase meanings for 2/1 in the older BMS images; neither is instantaneous flow,
and zero is not proof of healthy idle or bypass. The Python decoder
now exposes raw `battery_state_code` and `expansion_battery_state_code`, only
when the field is exactly `01 + byte`. Other models do not inherit them;
unknown byte codes are retained without semantic labels. Expansion state needs
fresh expansion-presence/count data; a zero byte alone does not establish an
attached idle battery. These fields should accompany E5 and measured powers,
and must not be substituted for a verified bypass or AC-input-presence flag.

The combined private [native DC Smart capture](c1000-native-dc-smart-validation.md)
contains **43 complete original-model reports** on main 1.7.1, all with
BF/C0=`01 00`. This verifies the observed byte format, not transitions or the
meaning of zero. Raw captures remain private. Synthetic decoder tests cover
0/1/2/255, malformed types/lengths, native status routing and model separation.

## Prerequisites and subsequent 1.7.1 validation

Use a complete fresh baseline: both outputs, charging ceiling, all existing
protected preferences, E5, **whole raw F8**, zero A2/A3 countdowns, SOC, and
fresh evidence of mains presence. Do not infer mains solely from zero watts
at full charge. The physical supply must tolerate the elevated allowance.

Write once, then obtain fresh telemetry across several policy iterations;
native ACK suppression must not trigger automatic setting retries. E5 must
retain the requested value while protected settings and every F8 byte stay
unchanged. Restore the original E5 preference and confirm the entire baseline
again. If the flag clears itself, record the condition and stop re-enabling
it. A successful trial at 100% SOC would establish retention/readback only.
Actual charging-rate validation needs a separate below-full trial.

The [Prime DC Smart validation](c1000-prime-dc-smart-validation.md) establishes
one different 1.7.1 control; it does not validate Fast. No Gen 2 or C2000
control follows from this original-model analysis.
The later [Prime Fast validation](c1000-prime-fast-validation.md) follows these
fresh-baseline and restoration requirements, confirming the flag at full SOC
and automatic clearing after input loss. The subsequent native trial supplies
its independent transport evidence. Actual Fast charging rate remains unverified;
the [current-limit follow-up](c1000-fast-current-limits.md) explains additional
temperature, voltage-history and DSP constraints in the older image.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_fast_retention.py \
  --image firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-fast-retention.json
cmp /tmp/original-fast-retention.json \
  tools/firmware_analysis/expected_results/original-fast-retention-results.json
```

**93 cases, 8,192 complete charging callbacks**: 64 retention conditions,
four BMS exceptions, four runtime/cache combinations, four Fast/full-F8
round trips, one bounded defaults-clear block, and sixteen BMS readbacks.
The suite runs real getter, serializer and policy instructions. Persistence,
ACK, logging and output dispatch remain captured/substituted; hardware and
transport are absent. Assertions and a read-only firmware mapping reject
unexpected execution or writes outside emulated RAM.

The image SHA-256 is checked against
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
Published expected results and their dependency manifest contain synthetic
data only. Private address excerpts and exploratory runs are retained
separately; no phone or station captures are published.
