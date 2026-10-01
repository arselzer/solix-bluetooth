# Original C1000: F0 GPIO getter through the diagnostic tunnel

Offline investigation dated 2026-10-01 using public **A1761 MainMcu 1.5.9**.
The installed original C1000 runs **1.7.1**; this replay does not establish its
behavior or the radio's BLE/MQTT forwarding rules. No hardware, network,
persistent storage or physical output was accessed.

This follows the GPIO lead in [AC Smart blockers](c1000-smart-blocker-followup.md).
There is a concrete application envelope for the getter, but it uses a separate
diagnostic function. It is not application command `00f0` or `40f0`.
The separate [installed-1.7.1 transport attempt](c1000-f0-live-transport-limit.md)
did not validate this getter over Bluetooth and required restoring the test
station's AC output. It is not included in the offline preservation claim.

## Exact request

| Layer | Value |
|---|---|
| Outer function | `0c` |
| Outer command | `0000` |
| Body | `a1 01 SS a2 0b 00 ee 00 08 00 00 f0 00 da a5 fc 55` |
| `SS` | `20`, `21` or `22`; all three execute identically in this MCU |
| Inner selector | `00`; do not substitute the outer source here |
| Inner property | Little-endian `00f0` |
| Inner request data | Empty |

The `A2` field has a **two-byte little-endian length**, followed directly by
the diagnostic frame. This differs from ordinary setting TLVs. There is no
`FE` timestamp and no setting value.

The inner frame is exactly:

```text
ee 00 08 00 00 f0 00 da a5 fc 55
```

Inner layout: `EE`, reserved byte, little-endian length, selector byte,
little-endian property, optional data, two CRC bytes, `FC 55`. The length is
`data_length + 8`; total frame length is `data_length + 11`.

CRC-16/MODBUS uses initial `ffff`, reflected polynomial `a001`, and covers
bytes 1 through `6 + data_length`. The conventional numeric CRC is emitted
**most significant byte first**. For `00 08 00 00 f0 00`, the CRC is `daa5`.
Firmware helper `08008d4c` returns the byte-swapped numeric value, which the
serializer stores little-endian. The replay checks both implementations.

For reference, a complete clear MCU packet with source `22` is:

```text
ff091b0003010c0000a10122a20b00ee00080000f000daa5fc5508
```

This is the MCU-facing framing, not a BLE authentication/encryption recipe.
The MCU also accepts destination-byte variants `00` and `02` in replay. A
BLE-side experiment should retain the established transport's header,
authentication and encryption conventions. The public package contains no
radio image, so changing a header cannot establish radio support.
The MCU accepts **clear-body** command `4000` as well as `0000`: its dispatcher
masks the command high nibble. This does not mean encrypted bytes can be sent
with command `0000` on BLE.

## Dispatcher and response proof

The replay executes this path rather than calling F0 directly:

| Address | Role |
|---|---|
| `08022684` | Validates outer `FF09`, length and XOR checksum |
| `08022284` → `08022584` | Parses and dispatches the outer function |
| `08022d6c` / `080289c4` | Registers function `0c` with `080160e8` |
| `200000d8` → `0800aaa4` | Command `0000` copies `A2` data into the diagnostic RX ring |
| `080276bc` → `0800d888` | Parses `EE...FC55` and selects the diagnostic property table |
| `08029be0` → `0801255e` | Selector 0, property `00f0` getter |
| `08007fb8(3,2)` | Reads GPIO input register `40011408`, bit 2 |
| `0800d6b0` | Serializes the one-byte result into the diagnostic TX ring |
| `08012de4` → `08016134` | Polls and wraps the diagnostic reply |
| `08012b38` → `08022634` → `08015020` | Builds and queues the outer packet |
| `08023fc0` → `080220f4` | Sends through the configured port callback |

The request installs/restarts a 20-tick reply poller. Once the diagnostic TX
length is stable between poller calls, it emits a separate response. There is
**no normal setting ACK**, and the reply does not echo the request source.

| GPIO bit 2 | Inner reply |
|---|---|
| Clear | `ee00090000f00000aa1bfc55` |
| Set | `ee00090000f000016adafc55` |

The outer body is `a10100a20c00` followed by that inner reply. Function remains
`0c`; command is `0000`, or `4000` when the existing port-0 encryption flag is
set. The serializer's flag behavior is proved; the replay does not execute
radio encryption.

The asynchronous reply always selects **port 0**, even when a synthetic
incoming request uses port 1. Normal setting-ACK suppression fields
`20000d13` / `20000d30` do not suppress it. Therefore a caller must listen for
the separate diagnostic response rather than wait for an `0800` ACK or infer
that a timeout means F0 did not execute.

## Getter safety and the upgrade-timer side effect

F0 reads the GPIO and returns its boolean value. The tested complete path
does not write charge limits, Fast/Smart modes, output flags, output timers,
DSP/BMS caches, persistent settings or hardware registers. The harness rejects
any such write, and fails if an output event, normal ACK, persistence helper
or system-reset routine is invoked.

The shared diagnostic dispatcher nevertheless has a specific side effect:
`080253b4` stops the timer whose ID is stored at **`200004f0`**, if allocated.
That timer is created at `08006474..08006484` in the branch logged as
**“upgrade mode event”** at `080063ca`. Its period is 1,800,000 ticks; its
callback `08012f74` invokes `0800d2c4`, which writes the AIRCR system-reset
request. With the previously established millisecond tick, this is a nominal
30-minute upgrade-mode reset timer. The replay executes the registration
block and cancellation, never upgrade entry or the reset callback.

Thus F0 is a setting-free getter **with upgrade-timer cancellation**, rather
than a completely side-effect-free request. It must not be used during an OTA
or upgrade-mode session. It does not start that timer or request reset.
Valid frames with an unsupported selector also cancel it, because cancellation
precedes property lookup; invalid framing/CRC does not reach cancellation.

The surrounding diagnostic table contains other operations. These findings
justify only the exact empty-data F0 request; they do not authorize arbitrary
diagnostic commands or property enumeration on a loaded station.

### Incorrect encryption framing can be consequential

The getter preservation result requires the exact **clear, correctly decoded
17-byte body**, including embedded length 11. The tunnel handler does not
validate that its two-byte length fits the received body. After copying into
the RX ring, `0800aadc..0800aae2` increments an **8-bit** counter and compares
it against that **16-bit** length. Values above 255 cannot terminate the loop.

Three bounded, offline cases prove the boundary: length 255 returns, while
256 and 300 remain in the loop after 100,000 instructions. Those cases do not
execute the diagnostic dispatcher or GPIO getter. No watchdog/reset is
simulated; a real controller stall could have consequences beyond the RAM
preservation checks. These malformed cases must never be sent to hardware.

There is corroborating **static evidence from the different C1000 Gen 2 radio
0.3.3.0 image**, SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`:

- `42050212..4205021c` tests outer packet byte 7, mask `40`.
- A clear bit branches to `42050adc`, then stores the original body pointer
  at context `+14` and dispatches at `42050b12`, without the decryption path.
- A set bit selects the decryption path at `42050994`; GCM mode calls its
  decrypt helper at `42050a0c` before dispatch at `42050acc`.

This supports treating `0x4000` as a transport encryption flag. It is not
proof of the original radio's function-`0c` forwarding or a complete radio
replay. If ciphertext were forwarded without decryption, arbitrary bytes at
offsets 4–5 would be interpreted as the tunnel length. That is a concrete
failure hypothesis, not proof of the cause of any physical incident.

## Remaining uncertainty

- The bit's electrical meaning is still unknown. Do not label it “mains
  present,” “bypass” or “AC Smart blocked” without physical correlation.
- Main 1.7.1 may differ. Its radio may filter function `0c`, route replies
  elsewhere, or expose it only over one transport.
- Do not turn this firmware lead into a runtime query until radio routing and
  encryption are validated separately. A missing reply is not evidence of a
  different GPIO state, and retries with guessed framing are inappropriate.
- Gen 2 models and the server-powered C2000 are outside this analysis.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_f0_tunnel.py \
  --firmware firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-f0-tunnel.json
cmp /tmp/original-f0-tunnel.json \
  tools/firmware_analysis/expected_results/original-f0-tunnel-results.json
```

**94 cases pass:** 72 valid source/GPIO/ACK-route/encryption/reset-timer cases,
eight invalid inner frames/selectors, three invalid outer frames, two incoming
port cases, three outer destination variants, two clear-body command-flag
variants, three bounded length-loop cases and one reset-timer registration.
The SHA-256-checked MCU image is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
The adjacent manifest records tool, helper, dependency and result hashes.
All published fixtures use synthetic values and public firmware metadata.
