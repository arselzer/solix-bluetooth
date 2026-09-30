# Original C1000: native networking and firmware leads

## Scope and current result

This 2026-09-30 investigation concerns **A1761, the original C1000/C1000X**.
It used retained app binaries, public source references, and the public
vendor **A1761 v1.5.9** controller package. The firmware was downloaded without
authentication, checked, decoded and replayed offline. This investigation
sent no station or authenticated Anker requests and changed no settings.
Separate parent-run physical tests are identified below. The C1000X remains
untested. A1763/C1000 Gen 2 firmware cannot establish A1761 command support.

The original station already has a working [Bluetooth-to-MQTT
bridge](c1000-bridge-charging-and-bypass.md). Direct station-to-local-broker
operation still needs its provisioning and credential exchange recovered.
Upstream explicitly supports A1761 through **cloud MQTT**; that proves a
vendor MQTT interface, not local endpoint replacement. See the
[upstream supported-device list](https://github.com/thomluther/anker-solix-api/blob/main/README.md).

## What the public protocol reference establishes

The bounded audit uses [mqttmap.py and mqttcmdmap.py at
c2f8769](https://github.com/thomluther/anker-solix-api/tree/c2f8769/src/anker_solix_api).
It extracts 18 A1761 entries: 15 commands and three telemetry types.

| Native MQTT type | Reference meaning | Relevant readback |
| --- | --- | --- |
| `0044` | AC charging power, A2 typed uint16 LE, 100–1000 W, step 100 | `0405/D1` |
| `0045` | Device Timeout, minutes | `0405/D2` |
| `0050` | Temperature unit | `0405/DD` |
| `0052` | Display on/off | `0405/DE` |
| `0057` | Enable/disable bounded realtime telemetry | Subsequent `0405` |
| `005e` | Fast-charge switch, A2 typed byte 0/1 | `0405/E5` |
| `0076`, `0077` | DC/AC smart-output mode | Packed `0405/F8` |
| `0405`, `0407`, `0830` | Status, network information, version strings | Reports, not proven query opcodes |

Reference command bodies use A1=`22` and FE seconds; the already tested
legacy BLE controls use A1=`21` and their separate `40xx` command numbers.
These correspondences do not establish unknown Wi-Fi commands. No A1761
provisioning, SOC cap, reserve or tariff command appears in this map; this
is a statement about the reviewed reference, not firmware capability.

**The reference's Smart command values are inverted relative to tested
hardware.** It says normal=`1`, smart=`0`; the original C1000 on main **1.5.1**
instead accepted wire `0`→Normal/status `1`, wire `1`→Smart/status `2` in both
AC and DC round trips. The v1.5.9 handlers independently agree with the live
mapping. Packed F8 byte 1 is DC mode and byte 2 is AC mode, including its type
prefix. The source-audit fixture deliberately preserves the upstream values
as evidence; it is not the SDK's encoding authority. Smart mode can shut an
output down at low load and is unsuitable for selecting battery power while
retaining AC output.

An old `c1000x_backup_charge` suggestion is not reliable evidence:
[PR 245](https://github.com/thomluther/anker-solix-api/pull/245) removed its
nonfunctional placeholder command. Its older E5 interpretation must not
override the later explicit fast-charge mapping. In particular, `0057`
is a telemetry trigger, not an established battery-discharge control.

Our [physical bridge trial](c1000-bridge-charging-and-bypass.md) already
confirmed 1000→100→1000 W readback with AC output kept on. A 100 W setting
below the reported load did not establish battery-only operation. A5 stayed
zero during bypass, so zero A5 is not proof that mains supply stopped.
There is still no verified software-only A1761 force-discharge or SOC-cap
control.

## Model-specific firmware lead

A participant's [A1761 OTA metadata](https://github.com/thomluther/anker-solix-api/discussions/221)
contains these public vendor URLs. The HighVoltage package has now been
downloaded and checked; the LowVoltage URL remains unrequested.

| Metadata component | Package | Reported bytes | Reported MD5 |
| --- | --- | ---: | --- |
| `A1761_mcu_high` | [V1.5.9 HighVoltage, 20250617](https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2025/07/22/iot-admin/NMvH3hGsgGHRWgg5/A1761_AllPackets_V1.5.9_20250617_HighVoltage.bin) | 417860 | `ce53b545926bf5ce88e886e5da1b12dc` |
| `A1761_30Ah` | [V1.1.4 LowVoltage, 20250617](https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2025/07/22/iot-admin/uFoYXrDnhUhTtXLN/A1761_AllPackets_V1.1.4_20250617_LowVoltage.bin) | 417860 | `bc0bc44fc69b3cbdb085caa158214a0e` |

The low-voltage child is labeled `A1761_30Ah`, the expansion identifier;
these filenames must not be interpreted as proven 120/230 V regional
variants. The metadata also reports version strings `v0.2.3.1` and `v1.5.9`,
without establishing which chip the first names. [Source discussion](https://github.com/thomluther/anker-solix-api/discussions/221).

The tested station's B3 code is **151**, formatted **1.5.1** by the reference
convention. The downloaded **1.5.9** image is therefore not an exact installed
match or a recommendation to update. Its size and MD5 match the public metadata;
the [published input and manifest](../firmware/c1000_original/1.5.9/README.md)
record SHA-256, component checks and vendor notice. The original 68-byte trailer
is retained but its signature/meaning is unverified. No original-model radio
image was obtained.

## What the original controller image establishes

The v1.5.9 package contains five components: main MCU, AC/DC DSP, DC/DC DSP,
main BMS and sub BMS. The decoded main image loads at **`08005000`**; its reset
vector is `08005149`. Both DSP build strings identify A1761 **230 V**, a
**TMS320F2800137** and **V5110**. The package contains no separately listed
radio image. All three identified controller/BMS checksums, the outer payload
sum and **158 nested DSP block checksums** match. DSP whole-image checks remain
unresolved.

Executing the actual scatter initializer `08005a88` to `08005aa0` recovers
initialized RAM, including three command tables. Hardware initialization and
the application's main loop are not run:

| Frame function | Dispatcher/table | Evidence |
| --- | --- | --- |
| `0f` | `08007768`, RAM `20000254`, 32 entries | App control/status handlers |
| `10` | `0801dfd8`, RAM `20000154`, 32 entries | Module/controller messages and acknowledgements |
| `01` | `0801dfac`, RAM `200009d8`, 14 entries | Protocol-library negotiation/info |

Registration at `08022d6c` binds `0f` and `10` independently. The common
initializer `08022258` registers function `01`. Opcode numbers without their
function/transport context are insufficient to identify a command.

| App opcode | Actual v1.5.9 handler | Bounded finding |
| --- | --- | --- |
| `0044` | `0800b908` → `08024c28` | Stores raw uint16 charging watts at `20002040`; requests deferred persistence |
| `005e` | `0800bd00` → `08024c18` | Stores raw byte at `20000d10`; any nonzero value sets `200020c4` bit 2, preserving its other bits |
| `0076` | `0800bd48` → `08024e00` | Exactly wire 1 stores DC Smart mode 2 at `200004ab`; other byte values store Normal mode 1 |
| `0077` | `0800bd28` → `08024de8` | Same mapping for AC mode at `200004ac` |
| `0057` | `0800b2a8` | Sets realtime-report flag `20000d17`; optional A3 uint16 seconds updates its timer |
| `0051` | `0800bc58` → `08023b6c` | Restores numerous defaults, including charge power, smart modes and temperature; not a harmless query |

The actual v1.5.9 MCU F8 serializer `080093f0..08009424` and getters
`0801756c`/`08017560` produce type `04`, DC mode, AC mode. The installed
v1.5.1 station's captured BLE F8 instead has type `01`, DC mode, AC mode.
The mode values/positions agree; whether the type difference comes from
firmware version or a radio transformation is unresolved. The synthetic MCU
frame is not asserted to match the installed BLE header byte for byte.
Fast-charge getter `080173a8` reads the
stored bit; this replay does not establish acceptance under physical charging
conditions or later policy auto-clears.

The charge-power setter has no range check on this path. Crucially,
**D1 readback is upper-clamped** in `080092f6..0800931c`, with branches at
`08009518`: 1000 W for variant byte `20000454=0`, 750 W for nonzero variants.
The raw getter `08017500` still returns the stored uint16. Therefore a status
value at the maximum does not prove an out-of-range write was rejected. This
does not justify exposing arbitrary values or using zero as a charging stop.
The established original-model command domain remains 100–1000 W in steps
of 100. Charging consumers are present (for example `08014304..08014332`),
but their complete hardware policy has not been replayed.

Neither `0090` nor `0103` appears in the recovered **function-0f table**.
This removes a proposed direct Gen2-controller analogy, not every possible
SOC-cap or discharge mechanism on every A1761 firmware version. No reliable
original-model forced-discharge or charge-cap control was found.

### Radio/provisioning implications

Function-`01` opcode `0025`, handler `08017ec4`, produces protocol-library
information (`2.0.1`, build metadata), with no SSID or credentials. It is not
evidence for Gen2 activation. Function-`01` `0024` (`08018274`) checks A2 and
invokes callback slot `20006408+0x14`; the callback's purpose is unresolved.
These are not proposed BLE/MQTT packets.

The separate function-`10` table contains `0824` and `0825` acknowledgement
handlers, as well as `0026` time synchronization and `0027` module-state
handling. An acknowledgement number alone cannot establish the corresponding
request payload or whether it is exposed to the app. Wi-Fi button handlers
(`0800657a`, `080067cc`) alter module state and timers. They provide a concrete
main/radio interface to trace, but do not reveal the app's SSID/password
serializer or the ESP32 cloud/MQTT bootstrap implementation.

## App evidence and the provisioning gap

The retained Flutter `libapp.so` contains these static strings:

| String | Example file offset |
| --- | --- |
| `A1761AnkerDevice` | `0x801667` |
| `parseConnectWifiData` | `0x70957c` |
| `parseActivateWifiData` | `0x454612` |
| `parseMultiDeviceBindData` | `0x9558d1` |
| `_sendSSIDMessage` | `0x140f8b` |

Input SHA-256:
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
These are file offsets of strings, not executable entry points. They identify
search targets; they do not establish that A1761 calls those serializers, their
payload layout, transport encryption, or command numbers. In particular,
neither `4024/4025` compatibility nor the Gen 2 local MQTT certificate-response
format is established for A1761. The reviewed SolixBLE C1000/device files
provide ordinary legacy control and negotiation, without a recovered Wi-Fi
provisioning builder.

Original C1000 sessions use P-256 ECDH and AES-CBC. A phone HCI capture alone
does not guarantee decryptable application writes: an app plaintext trace,
serializer analysis, or corresponding session secret would also be needed.
The earlier Prime repeated-GCM-nonce recovery does not establish a CBC
decryption route. Keep all such material private.

## Reproducing the bounded source audit

Use a local copy of the two public source files at the pinned revision:

```sh
python3 tools/firmware_analysis/audit_legacy_reference.py \
  /path/to/anker-solix-api/src/anker_solix_api \
  --output /tmp/legacy-reference-audit.json
python3 -m unittest discover -s tools/firmware_analysis \
  -p test_legacy_reference.py -v
```

The standard-library tool checks both input SHA-256 hashes and parses Python
syntax without importing or executing upstream code. The committed
[result](../tools/firmware_analysis/expected_results/legacy-reference-audit.json)
contains only public command metadata. **Four synthetic tests pass**, covering
model isolation, inherited field/range overrides, nonexecution, and rejection
when A1761 is missing. This is source inspection, not a firmware replay or
live compatibility test. The app symbol evidence is separately retained in
the ignored private directory; no app binary or raw phone log is published.

The newly published vendor package also supports a separate reproducible
firmware replay:

```sh
python3 tools/firmware_analysis/extract_c1000_original.py --output /tmp/a1761-images
python3 -m unittest discover -s tools/firmware_analysis -p test_c1000_original_package.py -v
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_c1000_original_commands.py \
  --output /tmp/c1000-original-commands.json
```

The extractor has **seven** focused structural/corruption tests. The replay
has **1,317** synthetic cases: 768 fast-charge input/bit combinations, 512
Smart inputs plus actual F8 serialization, 36 raw-power/readback combinations,
and one actual library-info response. Startup decompression also executes
unchanged. [Expected results](../tools/firmware_analysis/expected_results/c1000-original-commands.json)
contain command tables and synthetic values only. Python **3.12.3** and Unicorn
**2.1.4** were used; `--firmware` accepts an external decoded image with the
same exact hash.

Command acknowledgement, deferred configuration persistence, Smart-mode file
persistence and response transport are recorded substitutes. Peripherals are
not mapped; a peripheral write fails. The power-clamp segment seeds its prior
stack value, while the actual raw getter and model-variant getter execute.
These tests prove bounded instruction behavior, not MQTT authentication,
whole-device safety, physical power routing, or installed-v1.5.1 equivalence.

## Concrete next prerequisites

1. Obtain a matching A1761 radio/ESP32 image through a public or separately
   authorized source, or recover the A1761 app serializer. The verified main
   package does not contain a listed radio component; its controller tables
   and module callbacks can guide that next step.
2. Recover the exact A1761 Wi-Fi request and activation/binding sequence from
   that parser or a readable app session. Do not infer it by adding `0x4000`
   to unrelated commands or substituting Gen 2 payloads.
3. Only after framing and restoration are understood, test an original-only
   isolated AP capture. Observe DNS/TLS/API bootstrap before designing local
   replies, then establish status delivery before any native control trial.

The verified package and replays permit more offline work without a station
action. Forced discharge remains a separate controller question; successful
Wi-Fi or MQTT setup would not itself prove that capability.
