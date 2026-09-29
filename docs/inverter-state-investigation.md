# Inverter status and log diagnostics

## Scope

Offline investigation on 2026-09-29 used recovered **C1000 Gen 2 main firmware
1.1.4.9**. No station, SSH host, broker or cloud endpoint was contacted.
The C2000 Gen 2 runs **2.1.6.4**; the findings below are not proof of its
implementation. Its AC output supplies servers and must remain enabled.

This continues [the native power investigation](mqtt-power-offline-followup.md)
and explains the separate power gate used by the recovered tariff selector.
The subsequent [DSP producer trace](inverter-dsp-investigation.md) identifies
register `0100.bit0` as qualified AC input in the recovered C1000 ACDC image.

## The missing inverter-state writer is an internal receive parser

Main-controller function `0801b54c` validates incoming DSP messages and copies
their register data into RAM `20003f00`. The writer was missed by the earlier
direct-store search because its destination is calculated and passed to
`memcpy`.

The byte collector `0801b6cc` receives data from the UART interrupt at
`0801156c`, using peripheral data register `40004804`. It buffers at
`200038e8` and schedules parsing after a short receive timeout. The normal
register-response layout is:

| Offset | Recovered interpretation |
| --- | --- |
| `00` | Lowercase ASCII `main` response magic. |
| `04` | Halfword corresponding to request constant `0010`; its physical meaning is unresolved. |
| `06` | Device selector; `0001` selects the inverter status region. |
| `08` | Length; complete frame size is this value plus 12 bytes. |
| `0a` | Response operation `1001`. |
| `0c` | First register, normally starting at `0100`. |
| `0e` | Payload byte count. |
| `10` | Little-endian register data, followed by CRC16. |

CRC uses initial value `ffff` and polynomial `a001`; the complete frame
including its little-endian CRC produces zero. Register `r` maps to
`20003f00 + 2 × (r − 0100)`. The nominal region is 146 bytes. This is a
separate controller protocol, with uppercase `MAIN` requests built by
`0801b400` and sent through internal port 2. **No app/MQTT bridge permitting
arbitrary register reads has been established.** These bytes are not native
MQTT commands.

Nineteen instruction-level replays execute the parser, CRC, copy helpers,
receive counter and status predicates with **zero substituted functions**.
They cover offsets, end clipping, status/fault combinations, invalid CRC,
wrong device/operation/magic and incomplete input. Receive bytes and RAM are
synthetic; actual UART timing, queued callbacks and hardware are not tested.

## What the registers tell us

| Register | C1000 firmware evidence |
| --- | --- |
| `0100`, bit 0 | Required by the activation predicate; the later DSP trace identifies qualified AC input on C1000. |
| `0100`, bit 2 | Charger feedback: compared with the main controller's charging command in `080164b8`; recovery code names a mismatch “AC charging off from dsp”. |
| `0101`, bits 0–13 | Must be clear for activation. Bits 14–15 are ignored by this predicate. |
| `0101`, bit 6 | Fault handler `08005c20` identifies AC discharge overload. |
| `0101`, bit 5 | The same handler identifies AC output short circuit. |
| `0102`, low byte | Must be zero for activation. Its individual bit meanings remain unresolved. |
| `0104`, bit 0 | Must be clear for activation; physical meaning unresolved. |

The actual activation getter is `08025e50`. The deactivation getter
`08025edc` checks only whether `0100.bit0` is clear. Consequently, when bit 0
is set but a fault or auxiliary blocker is present, **both predicates return
false**. The debouncer retains its previous gate value in that situation.
A register snapshot cannot always reconstruct the debounced gate without
history.

Eleven further replays execute the debouncer `080254e4`, both getters,
callback `08025e80`, bit helpers and event enqueue with no substituted
functions. Other debounce entries are disabled in the synthetic fixture.
The configured threshold is 10, but the actual comparison is
`counter > threshold + 1`: from counter zero a transition takes 12 matching
samples. The failed activation path resets then increments the counter,
so a subsequent healthy sequence takes 11 samples. Do not describe this as
exactly ten samples or infer its duration without the sampling cadence.

The callback updates tariff gate `200004be.bit0` and queues an event. These
results alone do not identify the bit's physical meaning. The later DSP trace
establishes input voltage/frequency/protection qualification; it remains
independent of output enable, bypass selection and charger feedback.

## Saved fault logs can contain the missing words

Collector `080167b8` queues a timestamp and the first 72 bytes of inverter
status. On a new nonzero inverter fault, logger `08026b70` writes queued
records as:

```text
<eight hexadecimal RTC digits>,INV,<36 comma-separated hexadecimal words>
```

The words correspond to registers `0100` through `0123`, including every
input to the activation predicate. This is a useful offline diagnostic lead,
but the records are **historical, fault-triggered snapshots**, not guaranteed
fresh status. An empty log proves nothing about the current power gate.
Do not induce a fault to obtain a record.

## App log requests and their side effects

The relevant requests are **app function `0f`**, native pattern `03 00 0f`:

| Request | C1000 behavior |
| --- | --- |
| `0065` | Handler `0800ca04` inventories saved logs, caches file availability, sets page size 250 and returns metadata. Expected response: `0865`. |
| `0066`, page 0 | Handler `0800bacc` generates RTC, serial and version header. Expected response: `0866`. It does not include inverter words. |
| `0066`, later pages | Reads saved log data and advances file/page cursors. These paths were statically inspected, not replayed here. |

The page request uses `A1=22` followed by a typed `A2` uint16. Page 0 is
`a1 01 22 a2 03 02 00 00`, before the established native timestamp field.
The recovered handler reads the page at payload bytes 6–7. Run metadata
first: page 0 depends on its initialized page size. Do not improvise field
order, omit the type byte, or try arbitrary page numbers.

The 250-byte header starts with raw RTC formatted using `[%08X]` into a
10-byte bounded string buffer. The recovered wrapper reserves a NUL byte,
so the transmitted ten-byte prefix is `[XXXXXXXX` plus NUL: the closing
bracket is truncated. It then includes a **17-byte private serial**, a
stored version, twelve module versions, padding and a final newline.
Metadata counts this synthetic header in its total length/page count.

Both requests call `08015710(1)`. This creates/restarts a **10,000-tick log
timer**, nominally ten seconds, and sets `20000728`. Its callback
`0802009c` clears that flag and the stored timer handle. The flag getter's
only identified direct caller is the idle check at `0800f19e`: it refreshes
the idle countdown to 60, temporarily inhibiting standby. No output-switch
or charging-setting write was found in these handlers or timer paths.

There is an additional resource concern: expiry marks the timer slot stopped
(`3`), rather than freeing it (`0`), and discards the handle. The allocator
selects only free slots. Three synthetic sessions separated by expiry use
handles 1, 2 and 3; metadata and page 0 within one session reuse the handle.
No generic reclamation was identified in the inspected timer service. The
allocator has 90 slots and loops if none are free. This warrants avoiding
periodic log polling; it is not a demonstrated C2000 failure or an audited
proof that no other firmware path can reclaim a slot.

Nine diagnostic replay cases cover metadata/header generation, readiness,
RTC boundary values, timer restart/expiry, idle behavior and separated
sessions. Real handlers and timer routines execute; allocator, transport,
debug output, tick source, file count/size lookup, module-version getter and
`snprintf` are substitutes. The latter mirrors the statically inspected
bounded wrapper. Protected output/settings/gate/inverter bytes remain
unchanged in these fixtures. Filesystem internals, unrelated concurrent
tasks, low-memory behavior and C2000 compatibility are not emulated.

The reference audit covers direct calls and literal/base-address uses;
linear propagation is not a whole-program alias proof. Ordinary app
`0064` is an update callback, and radio/internal function `10`, opcode
`0066` is a different Modbus configuration route. Neither is a substitute
for app log retrieval.

## Recommended next step

Keep these log commands offline for the server-supplying C2000. First
validate one bounded metadata/header session on an available noncritical
C1000, with a baseline, private capture and final status check. If its schema
matches, inspect existing saved pages within that same session for `INV`
records. Do not turn this into background monitoring or repeatedly reopen
expired sessions. No such live experiment was performed in this follow-up.

The established native status/readiness/connectivity queries remain the
current monitoring route. Inverter-controller reception is now mapped, but
fresh remote exposure of the full gate remains unresolved. Bit 0's producer is
now mapped in the C1000 DSP; C2000 equivalence remains an open question.

## Private reproducibility

Ignored, owner-only artifacts in `.solix-private/firmware-analysis/`:

- `emulate_inverter_receive.py` and `inverter-receive-emulation-results.json`: 19 cases.
- `emulate_inverter_debounce.py` and `inverter-debounce-emulation-results.json`: 11 cases.
- `emulate_log_diagnostics.py` and `log-diagnostics-emulation-results.json`: 9 cases.
- `trace_log_state_refs.py` and `log-state-reference-results.json`: direct-reference audit and its limitations.
- `inverter-investigation-artifacts-sha256.json`: artifact hashes.

Run using `PYTHONPATH=/tmp/solix-analysis-tools python3 <script>` in the
retained analysis environment. These are synthetic research fixtures, not
runtime firmware dependencies. Firmware images, raw logs and device identity
data remain outside Git.
