# Gen 2 backup export and non-clearing status queries

## Result and scope

The additional offline investigation did **not establish a complete,
app-facing export of saved disaster-preparation records**. The internal record
getter is real, but its reviewed callers do not provide a new export command.
The existing [D9 restoration limitation](gen2-disaster-plan-investigation.md#d9-readback-and-restoration-limits)
therefore remains: inactive backup status is insufficient to reconstruct and
restore every saved automatic window and maximum-SOC byte.

There is a useful read-only route for the separately stored **tariff plan,
reserve, UTC timestamp and clock-screen status**: the normal native `0100`
status request, without optional A2. A new replay passes **24 synthetic
cases**, preserving the saved settings and the clock-screen failure flag that
`0092` would clear. This adds query-path evidence, not a physical device test.

All controller addresses below apply only to the published **C1000 Gen 2 main
1.1.4.9** image. The installed C2000 controller binary is unavailable; no
C2000 or original C1000 equivalence is inferred. No device, cloud, SSH,
Bluetooth, configuration or output command was used in this investigation.

## Complete records remain internal

`0801a608(kind)` returns the complete manual-record pointer for kind 1 and the
three-record automatic array for kind 2. The nine-byte records and two switches
occupy the 38-byte saved block beginning at `20001ea9`.

A reproducible direct Thumb-BL scan finds **19 callers**:

| Call sites | Role |
| --- | --- |
| `08009420`, `08009440`, `08009480` | Cancellation/window invalidation |
| Fourteen sites in `08018afc..08018c4e` | Active-window selection and cached selection validation |
| `08019184`, `08019192` | D9 copies only the saved manual start/end timestamps |

No literal function pointer to this getter was found in the image. Direct
literals for the record addresses occur in clearing, storage and the getter.
These are bounded cross-reference findings: computed addresses, indirect
callbacks, generic storage services and radio-side implementations are not
exhaustively ruled out. Calling an internal pointer through a debugger or
inventing a register read would not establish an app protocol.

The recovered app-function dispatch table contains the known `005e` writer
and `0100` status handler; it does not identify a separate backup-export
handler. Named logging and plan queries are unsuitable substitutes: `0066`
starts/restarts a log timer, and `0092` is an LCD-plan query that clears a
failure flag. See [diagnostic limits](mqtt-power-offline-followup.md) and
[clock-screen behavior](gen2-timer-plan-investigation.md).

## What normal status actually reads

Handler `0800b57c` obtains a buffer, emits a success byte and calls serializer
`080224a0`. The serializer traverses its real 19-entry descriptor table,
including:

| Tag | Actual callback | Useful content |
| --- | --- | --- |
| D9 | `080190d4` | Current tariff, stored usage mode/reserve/caps/TOU periods, partial backup tail |
| DA | `08018730` | LCD schedule/status, including the failure flag at `200028fa` |
| FE | `08018e4c` | RTC getter plus stored signed offset, encoded as UTC seconds |

### Reported controller UTC metric

The Python decoder exposes **`controller_utc_timestamp_seconds` only for
`Model.C1000_GEN2`**, and only when FE is exactly five bytes with type `03`.
The remaining four bytes are an unsigned little-endian value. Missing,
truncated, oversized and differently typed FE fields leave the metric absent;
raw TLVs remain available. Other models receive no inferred timestamp metric.

The instruction chain is explicit:

1. `0100` handler `0800b5e8..0800b5f4` invokes serializer `080224a0` with
   refresh mode 1.
2. FE callback `08018e86..08018e9e` checks that mode, writes its type byte,
   calls `08019fd0` → RTC getter `08029cbc`, then calls offset getter
   `0801a6b0` for the word at `20001d4d`.
3. `08018e9c` adds them and `08018e9e` stores the low 32 bits starting at
   FE value offset 1. Under the recovered time-sync convention, the stored
   offset is seconds west of UTC, so this reconstructs UTC from the local RTC.

This is **controller-reported time**, not a host timestamp or request echo.
The 24 replay cases vary the stored offset and deliberately send a request FE
one day ahead; the full response still reports the controller's own value.
For refresh modes other than 1, the callback retains the previous FE bytes.
An arbitrary incremental telemetry message can therefore carry an older time.

After reconnect, repeated explicit full status queries can show whether the
reported epoch advances or jumps. That helps distinguish clock-state changes
from a stale request/envelope timestamp when investigating tariff behavior.
It does not establish clock accuracy, NTP success, delivery latency, timezone
correctness, or an independently measured clock offset. The decoder performs
no time synchronization and no host/device skew calculation.

D9's saved mode/reserve/plan fields come from the live settings block. Reading
them does **not** independently prove that a previous write has committed to
nonvolatile storage. FE gives the adjusted timestamp; it does not expose the
raw RTC and stored offset separately. Neither field completes backup-record
restoration.

The request's optional A2 updates transient request metadata. The audited
path omits it and uses only the ordinary A1 source and FE timestamp. In all
24 cases, the request FE was deliberately one day ahead of the synthetic
controller UTC time; the returned FE still reflected the controller clock,
and the RTC registers remained unchanged. This covers the controller handler,
not every upstream radio middleware path.

### New instruction-replay evidence

[The new replay](../tools/firmware_analysis/emulate_backup_query_routes.py)
executes the actual TLV parser, `0100` handler, descriptor traversal and
D9/DA/FE callbacks. Cases vary:

- Standard versus enabled TOU, with reserve 10% versus 85%.
- Stored offsets of −7200, 0 and 19800 seconds.
- LCD failure/status flag values 0, 1, 2 and 3.

Each case seeds four distinct dormant backup records and their maximum bytes.
It verifies the entire `0x190`-byte saved settings block, all 38 backup bytes,
output flags, RTC registers and LCD failure flag remain unchanged. D9 returns
the saved two-slot plan and reserve, and FE returns the expected UTC value.
In particular, DA reports failure value 2 **without clearing it**.

Substitutions are explicit: the other 16 measurement callbacks emit empty
typed values; allocation, deallocation, reply transport, memory helpers,
logging, persistence backend and refresh timers are boundaries. The calendar
year and RTC registers are synthetic. This does not reproduce complete
real-device telemetry or prove those substituted callbacks have no effects.
The backup selector executes and may update its active-state cache; the claim
is preservation of the checked saved configuration and flags, not zero RAM
writes.

## App names that do not establish a local export

Selected retained-app methods were traced locally; raw app material stays
private. These leads identify other interfaces:

| App method/address | Observed route and limitation |
| --- | --- |
| `StationCommand.getBackupRecord`, `02ad380c` | `akiot.energy.get_backup_records`, station ID and pagination; backup history, not a demonstrated export of the MCU's stored windows |
| PPS preparedness feature creation, `02a3d44c`, and status fetch `0420bbd0` | Builds `PpsAutoDisasterFeature`; inherited fetch uses HTTP `/charging_disaster_prepared/get_site_device_disaster_status` through `AutoDisasterMixin.request` |
| `getCurrentDisasterPrepareDetails`, `03ebe5d8` | HTTP `/charging_hes_svc/get_current_disaster_prepare_details`; an optional enable field also exists, so this is not proposed as a passive local query |
| `_getDeviceCurrentTime`, `029733e8` | A5101-specific UI method using HTTP `/charging_hes_svc/get_system_device_time`; not a C1000 Gen 2 clock opcode |
| `parseTLVBackupMindData`, `03b45d74` | Decodes/retains an opaque payload used by S1/S2 logic; finding the parser does not establish an A1763 request or saved-record schema |

The HTTP paths are code evidence only. They were not requested, and their
responses cannot be assumed to equal the controller's complete saved state.

## Next prerequisite and reproduction

Use fresh ordinary status to observe tariff/reserve/UTC/DA without adding
`0092` or a clock write. A reversible disaster-plan trial still needs a proven
complete-record readback or another independently established backup of every
saved record and switch. Do not erase records to discover whether they existed.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_backup_query_routes.py \
  --output /tmp/backup-query-routes-results.json
cmp /tmp/backup-query-routes-results.json \
  tools/firmware_analysis/expected_results/backup-query-routes-results.json
```

The tool defaults to the repository's published image; `--firmware-dir` or
`SOLIX_FIRMWARE_DIR` can select the same image externally. It enforces size and
SHA-256 `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
The new results and manifest contain synthetic/public data only. This suite
is separate from the earlier 16 D9/cancellation counterexamples.
