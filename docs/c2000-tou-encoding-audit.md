# C2000 Time-of-Use encoding audit

## Scope and correction

This investigation on 2026-09-29 used retained C2000 Gen 2 **2.1.6.4** captures,
recovered C1000 Gen 2 main **1.1.4.9** and radio **0.3.3.0**, the retained Anker
Android binary, and a local upstream source checkout. **No device, SSH, Bluetooth,
MQTT or cloud request was made during this offline audit.** Raw artifacts remain private.

**Subsequent live validation:** the [corrected C2000 Peak trial](c2000-corrected-peak-trial.md)
confirmed one-slot storage in Standard, then active Peak and battery discharge
with mains connected and AC output enabled. Settings and eventual grid power
were independently verified after restoration. That later test is separate
from the offline evidence below.

The evidence substantially changes the interpretation of earlier failed Peak
trials. **A6 is strongly supported as the schedule slot count; A7 contains
triplets without another count byte.** Earlier experiments put an extra count
inside A7 and used A6 values 3 or 4 as an unknown parameter. Under the recovered
controller interpretation, those requests installed malformed slots.

This supersedes claims that the earlier C2000 tests confirmed a valid one-slot
all-day plan. They confirmed accepted writes and readback bytes, but our decoder
misinterpreted those bytes. Their failure to activate Peak cannot establish a
missing cloud command, an extra binding requirement, or a blocked power gate.
Successful local MQTT, charging-power and charge-cap tests remain independent.

## Evidence from C2000 captures

An independent raw-TLV audit of the retained native tariff and headroom trials
found **244** `0421`/`0900` status records satisfying:

```text
D9 value length = 26 + 3 * D9[6]
```

Offsets include the leading type byte:

| D9 offset | Supported interpretation |
| --- | --- |
| 0 | Binary type `04` |
| 1 | Active tariff |
| 2 | Usage mode |
| 3 | Backup reserve |
| 4 / 5 | Upper / lower charge limits |
| 6 | Slot count |
| 7 onward | `count` triplets: tariff, start hour, exclusive end hour |
| After triplets | 19 bytes of backup-related data; not schedule padding |

Observed lengths were **26 bytes for count 0, 35 for 3, and 38 for 4**. The
previous decoder instead called offset 6 a parameter and offset 7 a count.
That interpretation fails to account for the length changes.

An earlier final BLE snapshot also has length 38, count 4 and first triplet
`00 01 00`. Its old zero-slot assertion was incorrect: clearing the first tariff
byte did not clear the count. Later native trials genuinely restored `A6=0`.
These historical records are unchanged; they do not describe a new live check.

More strongly, replaying all **17** retained `0090` requests through the actual
C1000 handler and D9 builder reproduced every C2000 schedule-prefix byte and
full D9 length. The unrelated backup tail differed in one byte; this is not a
claim that complete C1000 and C2000 firmware behavior is identical.

## What the controller actually reads

Recovered C1000 handler `0x0800c7c4` parses these fields:

| Field | Actual C1000 behavior | C2000 evidence |
| --- | --- | --- |
| A2 | Typed byte → usage mode | Stored mode changes observed |
| A3 / A4 | Not read | Sent as zero previously; meaning remains unproven |
| A5 | Typed byte → backup reserve | Independent reserve changes observed |
| A6 | Typed byte → number of slots | Readback length and bytes match |
| A7 | Skip type byte, copy remaining bytes as triplets | Readback bytes match |

Setter `0x0802b5b4` stores a 20-byte structure: mode, count, six triplets.
Builder `0x080190d4` explicitly computes `26 + 3 * count`, serializes count
at D9 offset 6 and copies triplets from the structure after its first two bytes.

For example, the old Peak request contained:

```text
A6 value: 01 04
A7 value: 04 01 01 00 18
```

It is consistent with **four slots**, whose first triplet is
`01 01 00`: Peak from hour 1 to hour 0. The next triplet begins with `18`, an
invalid tariff, and the remaining bytes were zero in these captures. The
selector does not interpret an end before start as an overnight range.
Even with synthetic readiness and power gates both true, this stored plan
selects tariff 0.

The corrected one-slot candidate is:

```text
A6 value: 01 01
A7 value: 04 01 00 18
```

Here the initial `04` is the binary type, followed directly by Peak, hour 0,
hour 24. The actual C1000 handler, selector and D9 builder produced one valid
slot, D9 length **29**, and active Peak for **all 24 simulated hours**.
That offline execution alone did not establish C2000 activation; the subsequent
live trial now does, for this one-slot plan and firmware.

Twelve additional controller cases confirmed A3/A4 do not alter C1000 schedule
storage. Unused slot bytes persist when fewer bytes are copied, so count governs
which triplets are active. Do not infer an empty plan from a zero tariff byte.

## Radio forwarding and cloud boundary

Sixteen RISC-V replays covered the old and corrected candidate field layouts:

- Native MQTT JSON command 17 enters `0x42026f4e`, decodes `data`, and invokes
  the callback registered at `0x42043a18`.
- For opcode `0090`, that callback forwards function `0f` and the unchanged body
  through `0x420439cc` to framing builder `0x4204fcae`, with UART port 2 selected.
- The authenticated Bluetooth route at `0x42045792`, using A1=`21`, reaches the
  same wrapper. Native MQTT uses A1=`22` and its separate incoming callback.

Neither selected radio path strips a count from A7 or translates A3/A4/A6.
Replay stopped at the framing builder; encryption, UART delivery and C2000
radio equivalence were not tested. JSON lookup, base64, allocation, libc,
logging/ticks and Bluetooth authentication were substituted.

The retained upstream checkout is commit
`c2f87696f49151d8ead790b744bc6970e6258c2c`. Its
`helpers.py:convert_pps_tou_schedule` produces count plus triplets; all seven
retained schedule/clear A7 values match this implementation. However,
`mqttcmdmap.py:CMD_TOU_PLAN_V2` labels A3/A4/A6 unknown, and direct
`pps_tou_schedule` support is disabled in `mqtt_pps.py`.

That converter fits a combined count-and-triplets **D9** field. Applying it
unchanged to **A7**, while separately providing A6, duplicates the count under
the recovered handler semantics. The upstream D9 layout therefore supports the
corrected interpretation; its unfinished command encoder is not evidence that
our previous request was valid.

The upstream cloud method `schedule.py:set_pps_use_time` submits serialized
`pps_use_time` JSON with ranges, prices, currency and reserve through
`power_service/v1/app/device/set_device_attrs`, then retrieves attributes.
This client code does not expose the server's JSON-to-device translation or
prove that another activation packet is required. No cloud request was made.

## Retained app binary: useful leads and limits

The app uses Dart **3.11.0** Android ARM64 AOT, snapshot hash
`78da37fed6bf1489361a312568249f3f`. Its binary contains the PPS TOU command and
HTTP service paths, `_sendTouModeCommand`, `setTouSystemParams`, `setTouArgs`,
`clearTouPlan`, and separate `TOUSystemStatus`, `TOUSettingSystemStatus` and
`TOUPeriods` symbols.

These locate future decompilation targets; they do **not** establish an A1783
serializer, field values or call order. A matching AOT snapshot decoder was not
available locally, and no app-generated TOU request was captured in the retained
phone sequence. Thus this audit cannot claim a byte-for-byte comparison with
actual app output. Older app notes consisting of symbol lists should be read
with the same limitation.

## Live validation and reproducibility

The subsequent bounded C2000 check stored the corrected one-slot plan
**while retaining Standard mode**: D9 count 1 at offset 6, `01 00 18` at offsets
7–9, length 29, no active tariff and idle battery. It cleared the plan before
a separate trial with 85% reserve, then confirmed Peak and discharge with
mains present. Standard/count 0/reserve 10% were restored. Grid input did not
resume immediately in the retained MQTT samples; a later independent BLE check
confirmed idle and input equal to output. See the [live record](c2000-corrected-peak-trial.md)
for exact observations, guards, restoration limits and private capture hashes.

Public corrections belong in `python/solix_link/protocol.py`, its protocol and
native-status fixtures, and historical protocol/MQTT/firmware reports. Existing
capture bytes must remain unchanged. In particular, conclusions about valid
all-day schedules and a C2000-specific extra parameter need explicit correction.

Private reproducible files in `.solix-private/firmware-analysis/`:

- `audit_tou_encoding.py`: retained request/source comparison and app-symbol
  provenance; 17 requests, seven A7 matches, two converter examples.
- `emulate_tou_radio_forwarding.py`: 16 actual-code forwarding cases.
- `emulate_tou_controller_encoding.py`: 12 actual-code storage cases.
- `emulate_tou_d9_capture.py`: 17 schedule-prefix/length matches, 244 raw status
  length checks, and 24 corrected synthetic hour cases.
- Corresponding JSON results and `tou-encoding-audit-artifacts-sha256.json`.

The D9 replay executes the original parser, handler, getters/setters, memory
copy, selector, RTC conversion and serializer. Persistence, logging, reply
transport, display-timer query and refresh are substituted. Its backup-status
calculator receives a synthetic zero result; the remaining backup getter runs
and retains the one-byte cross-model difference. RAM and RTC peripherals are
synthetic. None of these executions proves physical discharge on the C2000.
