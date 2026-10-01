# Original C1000: charging producer and bypass follow-up

## Scope and conclusion

Offline investigation, 2026-10-01, of the public original **A1761 main MCU
1.5.9** image. The station subsequently updated to main **1.7.1**; these
addresses and results do not establish that newer firmware's behavior.

The controller has separate charging and AC-output gates. We have **not
identified a supported external command that selects battery power while
keeping AC output enabled**. In particular, storing a zero charging ceiling
does not take the internal charging-disable path. Stopping the charging
converter would also not, by itself, prove that the DSP bypass relay selects
battery operation.

No device command, network operation, flash write or physical output test was
performed for this investigation. Existing [live bypass observations](c1000-bridge-charging-and-bypass.md)
remain the relevant electrical evidence.

## Traced controller paths

| Address | Observed role |
|---|---|
| `0800b908` → `08024c28` | App function `0f`, command `0044`: store raw `u16` charge ceiling at `20002040`, request persistence, enter common acknowledgement helper. |
| `08014118` | Charging policy. Reads the ceiling and fast-charge setting, calculates voltage/current/power allowances. |
| `080242b8` | Store six-byte `[power, voltage, current]` descriptor at `20000424`; mark it dirty. |
| `0801f570` | Charging/output producer; charge branch tests `200004fc` bit 5. Separate output branch at `0801f762` tests bit 4. |
| `0802461c` | Construct internal DSP charging parameter update at register `4`. |
| `0802454c` | Construct internal DSP start/stop command; channel 1 stop is register `0`, value `0054`. |
| `080241d8` | Internal bit-5 setter; pointer to `u16`, `002f` means unchanged, otherwise insert the low bit. |
| `08015120` | Internal rule engine: first matching row's ten predicates, then ten action callbacks. |

The rule engine uses twelve registered groups (`200020f0`) and immutable
40-byte condition/action rows described at `20000500`. The charging setter
appears in callback groups **3–6, 10, 11**. Callers include the logged
`portAcIn` event path (`08007048..0800718c`) and BMS-event path
(`08005cb4..08005cdc`). Its address is absent from the app's 32 direct handler
entries. That table check is **not** an exhaustive indirect-reachability proof.

## Actual instruction replay

The new replay passes **87 synthetic cases**:

- `0044` stores `0`, `1`, `99`, `100`, `750`, `1000`, `1200` and `65535`
  without changing the power-state word. Acceptance in RAM is not a supported
  operating range; normal library validation remains necessary.
- The internal charging setter changes only bit 5, preserving AC-output bit 4
  and unrelated bits in the tested states.
- Descriptor storage clamps voltage to `300..396` for variant zero or
  `300..576` otherwise, and current to `3600` internal units.
- The active-charge producer still sends a register-4 parameter update when
  requested power is zero. It caps transmitted power at `1600` and floors the
  transmitted current word at `10` internal units. No conversion to amperes
  is established by this replay.
- With charging bit 5 clear, an existing charging session instead queues
  register `0` value `0054`. This branch preserves the output bit in controller
  RAM; no DSP, relay or AC waveform is simulated.

Allocator and queue operations are synthetic. Real controller instructions
construct the descriptor and internal frame. The replay stops before unrelated
periodic/output processing, so it does not establish whole-device behavior.

## Reproduce and remaining work

For a separate native MQTT status check, the same 1.5.9 app table registers
`0040` at `0800adb0`. It reads A1's source byte, builds A1 response source
`(source & 0x0f) | 0x30`, and calls full status builder `08009164`. An A1=`22`
body (`a10122`, with ordinary FE seconds if required by the radio) therefore
matches this handler. Normal response construction at `08022978` ORs `08`
into the opcode's high byte: **`0040` → `0840`**. Periodic `0405` is a separate
report opcode. The handler has a mode-dependent deferred-response branch;
this static evidence does not establish the radio's forwarding policy or
1.7.1 acceptance. Command `0057` changes realtime subscription state and is
unnecessary when fresh `0405` reports are already arriving.

### Writes can succeed without a command ACK

The common helper **`08007718` deliberately suppresses its response** when
`20000d13 != 0` and `(20000d30 & 2) != 0`. Helper `0800cb78` implements this
gate. It is the same gate that sends `0040` through the deferred full-report
path.

These six original-model handlers all tail-call the common helper:

| Command | Handler | Tail branch to common ACK |
|---|---|---|
| `0044` charge watts | `0800b908` | `0800b922` |
| `0045` device timeout | `0800baec` | `0800bb06` |
| `0046` display timeout | `0800bace` | `0800bae8` |
| `004c` display brightness | `0800bb8c` | `0800bb9e` |
| `004f` light | `0800b9d8` | `0800ba0e` |
| `0050` temperature unit | `0800bd68` | `0800bd7a` |

A separate **48-case** actual-instruction replay exercises the common helper
and the complete `0044` handler with both source bytes and route conditions.
The 900 W value is stored in both response modes. With the gate false,
response transport receives `00a10131` or `00a10132`; with the gate true,
there is **no response-transport call**. Persistence remains a substitute and
is not a verified physical flash write. The earlier 87-case power-path suite
substitutes the common helper, so its acknowledgement entry does not prove a
packet was transmitted.

For this route, write once, request fresh `0040` status, then verify the changed
field and protected baseline in a newly received complete report. Missing
`0844` alone is not evidence that the write failed. Do not automatically retry
a setting write to compensate for this expected absence of an ACK. This does
not establish the semantics of other models or every future firmware.

Install Unicorn in an analysis environment, then run:

```sh
python tools/firmware_analysis/emulate_original_power_paths.py \
  --image firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-power-results.json
python tools/firmware_analysis/emulate_original_ack_modes.py \
  --image firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-ack-results.json
```

The tool checks image SHA-256
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
It records its own hash, synthetic results and callback-table locations.
Private research artifacts remain in the ignored analysis directory.

Useful next evidence would be a corresponding 1.7.1 controller image or a
documented app operation that selects input/bypass mode, followed by tracing
the original-model DSP relay logic. Gen 2 tariff commands, fault injection,
Smart AC auto-off and disabling AC output are not substitutes for such evidence.
