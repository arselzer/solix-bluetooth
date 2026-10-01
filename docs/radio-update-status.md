# Radio module-update status

## Result and scope

Internal radio query **`0064`** provides a limited module-update status:
**1 = downloading, 2 = ready, 3 = failed**, using the firmware's own labels.
The reviewed producers update this value only for **OTA type 1**, identified
by the stop callback as `OTA_TYPE_WIFI`. MCU update branches can preserve an
older value. It is therefore not a reliable “any update is running” flag,
progress percentage, or confirmation that firmware was installed successfully.

**144 synthetic instruction-replay cases pass.** They execute the getter,
serializer, OTA-type getter, start/stop callback decisions and two bounded
failure branches. Actual transfer start/stop calls are replaced by recording
stubs. No device, network, update engine, flash, reset or credentials are used.
This research does not grant permission to start an update.

Input is the published **A1763 C1000 Gen 2 radio 0.3.3.0** image:
`firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin`, SHA-256:

```text
e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8
```

Addresses and behavior are specific to that image. Original C1000/C2000
equivalence has not been established.

## Getter and field provenance

The 42-entry internal radio table at `3c147be4` maps **`0064` → `4203cab8`**.
The handler reads a **32-bit word at `3fc90678`**, takes its low byte and
returns status `00` followed by raw TLV **A1, length 1**:

```text
00 a1 01 <module_status_low_byte>
```

It uses actual serializer `4204ddb2` → `4204dd4c`, then response helper
`4204fa68`. There is no input-TLV parser, state reset or validation of the
status value in the reviewed handler. Repeated reading is not shown to clear
the cached result. A transport error leads to one attempted response, with no
handler-level retry in the exercised cases.

The 18 getter cases include 0/1/2/3, unknown values and larger synthetic words.
For example, word `00000100` serializes byte `00`, and `12345678` serializes
`78`. These are truncation tests, not assertions that such words are normal
device states. All getter cases preserve global RAM.

## Meaning of the three identified states

OTA-type getter **`4202937c`** reads word **`3fc8aa64`**. The selected
producers use that value as follows:

| State | Actual store | Condition and evidence |
| --- | --- | --- |
| **1, downloading** | `420474e4` | Start callback `42047478`, type exactly 1; log says `module ota status -> downloading` |
| **2, ready** | `42048036` | Stop callback `42047fb6`, type exactly 1; logs say `stop OTA_TYPE_WIFI` and `module ota status -> ready` |
| **3, failed** | `420400bc` | Failure branch `420400ac..420400e0`, type exactly 1; log says `module ota status -> failed` |
| **3, failed** | `4204253a` | Failure branch `42042526`, callback argument exactly 1 and type exactly 1; same failure label |

The callback-configuration builder at **`4204825a..420482a6`** supplies
`42047478`, `42047fb6`, `42040076` and `420424f0` to registration function
`4202ba22`. This links the selected branch locations to actual callback
configuration; registration itself is only inspected statically here.

Zero and other values remain unnamed by this audit. In particular, the getter
contains no independent check that could justify interpreting zero as “safe
to update,” “idle,” or “no failure.”

## Why it is not a universal busy or success indicator

For OTA types **2 and 4**, the start callback takes the path logged as
`OTA_TYPE_MCU`. It can invoke transfer boundary `4204529a(0, 1, 0)`, while
leaving `3fc90678` unchanged. The Wi-Fi branch invokes the same boundary with
`(1, 1, 0)` after storing status 1. These calls are recorded and suppressed
in the replay; no transfer is performed.

The start callback separately sets byte **`3fc905c4` to 1**. The stop callback
clears that byte to 0; for types 2/4 it also reaches the substituted MCU stop
boundary `42047c5a`, still without changing the module-status word. This
separate byte is not included in the `0064` response. Its complete consumers
and suitability as a global busy flag are outside this audit.

Consequently:

- MCU activity can coexist with an old “failed” or “ready” module status.
- For type 1, the stop callback overwrites even a seeded failure value 3
  with **2, ready**. That transition does not verify an image, boot partition,
  installed version or successful reboot.
- Unknown OTA types also preserve the old status in these branches. A fresh
  response establishes a fresh read of the cache, not a fresh update outcome.

These are executed callback-decision results with synthetic state. They do
not establish every real callback ordering, cancellation policy or update
failure sequence.

## External routing limit

This is an internal handler finding, not a ready-to-send BLE/MQTT command.
The replay supplies an opaque response context and substitutes final
transport; it does not execute authentication, encryption or outer routing.

The [known MQTT raw-data path](radio-factory-routing.md#mqtt-differs-from-ble)
has a concrete obstacle: callback **`42043a18`** sends normalized commands
**above `003f`** through `420439cc` directly to the controller builder,
bypassing radio dispatch. `0064` is above that threshold and cannot simply
reuse the native raw route being investigated for radio RSSI `0022`.
Controller command numbers in other functions may have unrelated meanings.

No SDK method, Home Assistant sensor or update automation is added. A useful
future readback would need a proven route, model-specific validation and a
label such as “radio module update status,” preserving unknown values and
avoiding global busy/success claims.

## Reproduction and limits

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_radio_update_status.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output /tmp/radio-update-status-results.json \
  --manifest /tmp/radio-update-status-manifest.json
```

Verified with Unicorn 2.1.4. The script checks the firmware hash, requires
assertions and writes source/results hashes. Checked-in [synthetic results](../tools/firmware_analysis/expected_results/radio-update-status-results.json)
and [manifest](../tools/firmware_analysis/expected_results/radio-update-status-manifest.json)
include exact write addresses and suppressed transfer-boundary arguments.

| Cases | Scope |
| --- | --- |
| 18 | Getter values/truncation and response success/failure |
| 36 | Start callback: six OTA types, three initial status words, MCU slot absent/present |
| 18 | Stop callback: six types and three initial status words |
| 18 | Type-gated failure-decision branch |
| 54 | Callback-argument/type-gated failure-decision branch |

Each producer case finishes with the actual read-only getter to check its
serialized result. Write guards permit only stack, the status word and the
separate activity byte during producer replay; getter execution permits only
stack writes. The two failure cases stop before cleanup/reporting epilogues.
Host substitutes cover logging, ROM copy, response transport and transfer
start/stop. Their internals and hardware effects are deliberately not executed.
