# Gen 2 diagnostic getters and exposure limits

## Result and scope

The published **A1763 C1000 Gen 2 main 1.1.4.9** image contains useful
factory getters, but this audit establishes **no new ready-to-use BLE or
native MQTT query** and no complete export of disaster-preparation records.
Its radio-facing function `0c` and inner factory parser are separate layers;
the missing controller-side connection matters even though
[radio forwarding is understood](radio-factory-routing.md).

**109 synthetic instruction-replay cases pass.** No device, network, cloud,
flash backend, firmware modification or runtime API was used. Findings are
specific to this image, not original C1000, C2000 or other firmware versions.

Input: `firmware/c1000_gen2/1.1.4.9/MainMcu-decoded.bin`, load address
`08005000`, 198,656 bytes, SHA-256:

```text
21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9
```

## Exposure: registration is not an installed command handler

| Layer | Address | Observed behavior |
| --- | --- | --- |
| Protocol registration | `080309d6..080309e4` → `08017d08` | Registers function `0c` callback `08017704`, from constants at `0803380c + 4c` |
| Radio command wrapper | `08017704` | Logs the command, then looks it up in the RAM table pointer/count at `20000760` / `20000764` |
| Startup RAM initializer | `08005a8c..08005aa2` | Initializes that eight-byte table descriptor to zero |
| Inner RX feeder | `08014bc8` → `08011608` | USB receive callback feeds diagnostic RX ring when endpoint equals 3 |
| Parser scheduler | `08011640` | Starts callback `0802f6ac` on a 10-tick timer |

Eight replay cases execute the actual startup RAM initializer, protocol
registration, wrapper and its byte-dump logging loop. With the startup table
unchanged, four command values, with or without a valid inner frame, return
without invoking the diagnostic parser, a getter or the upgrade-timer stop.
Low RAM is unchanged by these wrapper calls; logging itself is substituted.

The image contains one direct literal reference to `20000760`, in this
wrapper. This audit has not found a command-table installer. The adjacent
ordinary app table at `20000758` has a separate setter, `080291b0`; it does
not initialize the diagnostic descriptor. This is a bounded static search
and isolated startup-state replay, **not a complete hardware boot** or proof
that no later path can install a table. USB feed/dispatch is established;
BLE-to-inner-parser and response routing on this controller remain unproved.

Do not transplant the original C1000 diagnostic envelope into Gen 2, or
treat the radio's function whitelist as proof of controller support.

## Inner parser and serializer

The independent factory path is parser `0800de18` → dispatcher `0802f6ac`.
The latter uses two eight-byte-entry property tables: selector 0 has **51
entries at `08032b64`**, and selector 1 has **9 at `08032cfc`**. Entries
contain a property number and Thumb callback. The results JSON preserves
the complete public table inventory; unreviewed entries are not executed.

Its inner framing is:

```text
EE 00 length_LE16 selector property_LE16 data CRC_BE16 FC 55
length = len(data) + 8; complete frame size = len(data) + 11
CRC-16/Modbus: init FFFF, reflected polynomial A001,
covering bytes from the 00 after EE through the end of data.
```

Serializer `0800dbdc` constructs the same framing, uses actual CRC helper
`080091fc`, appends to the TX ring and clears its scratch buffer. Reply
selector/property values come from the parsed request. This description is
an **inner factory format**, not a confirmed app command/TLV/ACK envelope.

## Reviewed getters

These are selector-0 properties. Sizes and mappings below are confirmed by
actual instructions with two synthetic RAM/GPIO states. Cached-field names
remain deliberately narrow where their physical meaning is unproved.

| Property | Callback | Returned data |
| --- | --- | --- |
| `0001` / `0002` | `080283bc` / `080283d0` | 16-byte model / main-version strings |
| `0003` / `0004` | `08028394` / `080283a8` | 16-byte public build-date / build-time strings |
| `0005` | `080284a8` | Identity string selected through pointer table `200001d0` |
| `0007` | `0802846a` | One cached BMS byte, `2000406b` |
| `00d0` / `00e2` | `08030558` / `080142f4` | Constant two-byte `55 00` |
| `00d1` / `00d8` / `00e3` | `08030574` / `08018714` / `08014310` | 12-byte cached blocks at `2000079f` / `20000787` / `20000793` |
| `00e0` | `08028428` | Word at `2000041d`, zero byte, byte at `2000041c` |
| `00e1` | `080283e4` | Unsigned 16-bit difference of words at `2000400c` and `2000400e`, followed by zero word |
| `00e5` | `08029fee` | Stored identity at `20000116`; length is at least 17, otherwise string length narrowed to a byte |
| `00e7` | `0801378e` | Four calibration halfwords, `20000134..2000013b` |
| `00e8` | `08017748` | Ten halfwords from component-version/configuration getters; details below |
| `00eb` | `08015638` | Constant zero word |
| `00ef` | `0802d0be` | Low byte of calibration halfword at `2000013c` |
| `00f0` | `080136d8` | Boolean GPIO input `40011408 & 1000` |
| `00f6` | `08018362` | Cached word `2000403b & 3000`, returned little-endian |
| `00f7` | `080195e4` | Cached four-byte word at `200042a0` |
| `00f9` | `08019220` | Three cached bytes at `20000112` plus a NUL that the getter writes |
| `00fd` | `0801923a` | Calibration/configuration byte at `20000111` |

`E8` calls `0801a460` with keys `5, 0, 7, 8, 10, 3, 4, 9`, then
`0801a6bc`, then key `11`. Halfword index 1 is main version `1149`; index 8
is the saved halfword at `SETTINGS + 0d`. The other component labels are
not inferred from another model. Likewise, `F7` has a version-formatting
consumer (`08019600`); it is not established as a fault bitmap.

The physical meaning of GPIO `F0` is unknown. It uses mask `1000`, unlike
the original C1000's separate GPIO getter. It cannot currently provide a
trustworthy new mains-present, bypass or UPS sensor.

## Side effects: these are not passive enumeration requests

**Every accepted inner frame**, including an unsupported property or
selector, calls `0802c464` before lookup. If the timer ID stored at
`20000148` is nonzero, it calls actual timer stop `08010998`; the synthetic
allocated timer changes from state 2 to state 3. Invalid framing/CRC does
not reach this step in the five malformed cases.

The upgrade-mode branch at `08006b68..08006c70` configures this timer with
1,800,000 ticks and callback `0801438c`. That callback calls reset routine
`0800d7d0`, which writes AIRCR. The replay does not execute the reset. Thus
even a getter can cancel a pending upgrade-mode reset timer.

`F9` also calls `0801926c`, which writes `00` to `20000115` before returning
the four-byte string. This terminator write is reproduced explicitly; the
other reviewed getters need no additional non-scratch write allowance.

Several other entries are clearly unsuitable as getter candidates:

| Candidate | Reason excluded |
| --- | --- |
| Diagnostic `D9` / `DA` | Queue radio function `10`, commands `0001` / `0002`, through `08030f40` / `08030d04`; they are not normal status `D9` / `DA` |
| `DC`, callback `08029880` | Clears/reinitializes a separate 216-byte buffer via `08029868` |
| `E4`, `E6`, `EE`, `F5`, `F8`, `FC` | Identity, calibration or configuration setters; some request persistence |
| `0006`, `FB` | Asynchronous DSP requests with wake/restore paths, not a simple cache read |
| Selector 1 `0016` | Aggregate status helper also refreshes runtime cache bytes |
| Selector 1 `0019` | Payload-dependent reset/control behavior |

The inventory does not imply that the remaining entries are safe to call.

## Complete backup export remains missing

All 92 reviewed-getter cases preserve the entire `0x190`-byte saved settings
region at `20001d48` and the selected eight-byte output-state region.
Instruction-level read tracking, including substituted copies/string reads,
records **zero reads of the 38-byte disaster-plan region at `20001ea9`**.
No reviewed response exports its three automatic records, manual record
and switches. `E8` is not a hidden full-settings serializer.

This reinforces the [existing restoration prerequisite](gen2-persistent-plan-followup.md#requirement-for-a-reversible-backup-trial),
without claiming no undiscovered export exists. Diagnostic `D9`, diagnostic
`DA` and normal status tags bearing the same numbers belong to different
namespaces. The [previous backup-export follow-up](gen2-backup-export-radio-app.md)
also explains why triggering an energy report is not a passive substitute.

## Reproduction and limits

```sh
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  tools/firmware_analysis/emulate_gen2_diagnostic_getters.py \
  --image firmware/c1000_gen2/1.1.4.9/MainMcu-decoded.bin \
  --output /tmp/gen2-diagnostic-getters-results.json \
  --manifest /tmp/gen2-diagnostic-getters-manifest.json
```

The analysis dependency is Unicorn 2.1.4. The script requires assertions,
checks the input hash and produces a script/results hash manifest. Checked-in
synthetic [results](../tools/firmware_analysis/expected_results/gen2-diagnostic-getters-results.json)
and [manifest](../tools/firmware_analysis/expected_results/gen2-diagnostic-getters-manifest.json)
contain no captured device identifiers.

Cases: 23 getters × two RAM/GPIO states × timer absent/present, four unsupported
selector/property pairs, five malformed frames, and eight initial empty-table
radio-wrapper cases. Actual parser, dispatcher, timer stop, getter, serializer
and CRC instructions run. Host substitutes cover ring I/O, memory/string
primitives and logging; RAM/GPIO are synthetic. Independent byte assertions
cover the simple getter mappings; `E8` checks its length and two identified
halfwords. Bounded execution and write guards reject unexpected paths.

Next useful evidence is a controller command-table installation or another
documented factory transport, followed by a fully traced getter-only response
route. Neither exposure nor live safety follows from the inner replay alone.
