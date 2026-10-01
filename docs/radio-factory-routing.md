# Gen 2 radio diagnostic routing

## Scope and result

Offline investigation, 2026-10-01. The input is the public **C1000 Gen 2
(A1763) radio 0.3.3.0** image distributed alongside main firmware 1.1.4.9:

- File: `firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin`
- SHA-256: `e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`

Function `0x0c` has a dedicated diagnostic forwarding handler. Its only
accepted normalized command is `0x0000`, and its only accepted incoming
ports are BLE (`0`) and the controller UART (`2`). The handler neither
validates the embedded diagnostic message nor chooses its destination from
the embedded `A1` source byte.

**This is not proof that a diagnostic request is safe on an original C1000.**
The original A1761 radio image has not been replayed. A matching radio version
string does not establish identical instructions, routing or controller
behavior. See [the original MCU tunnel investigation](c1000-f0-diagnostic-tunnel.md)
for the separate, version-limited MCU findings and malformed-length hazard.

## Routing and whitelist

Application startup at `0x42048824..0x42048836` registers function `0x0c`
with handler `0x42043aec`. Registration uses a table at `0x3fc8bedc`, with
16-byte entries and up to three callbacks per function. Registration and
dispatch reject function IDs above `0x17`; each registered handler applies
its own command policy. This is not a claim that every function below that
bound is available to clients.

Dispatcher `0x4204feee` forms the command as
`((frame[7] << 8) & 0x0f00) | frame[8]`. The factory handler then accepts
only zero at `0x42043b42`:

| Input path | Normalized command | Result |
| --- | --- | --- |
| BLE port `0`, function `0c` | `0000` | Send function `0c`/command `0000` to controller port `2` |
| Controller port `2`, function `0c` | `0000` | Send function `0c`/command `0000` to BLE port `0` |
| Generic parser on port `1` or `5` | `0000` | Return without forwarding |
| Factory handler on either accepted port | Any nonzero command | Return without forwarding |

Transport startup at `0x42048852..0x42048878` registers port `2` with
`0x420415ba`, port `0` with `0x4203fcf0`, and port `5` with `0x4203c98a`.
The latter two wrap the BLE send and native network send paths respectively.
The replay captures the outgoing queue boundary; it does not run a UART,
BLE stack or network transmitter.

## Encryption classification occurs first

The generic receiver `0x4204d14e` verifies `ff09`, frame length and the outer
XOR checksum, then queues a copy. Queue processor `0x4204d30e` supplies the
port and session context to payload parser `0x4204ffbe`.

At `0x42050212..0x42050220`, **bit `0x40` of command byte 7 determines
whether the payload is encrypted**. A command number of `0x4000` therefore
means encrypted command zero; `0x0000` means a clear payload.

- With the marker set, the parser selects its configured cipher. The GCM
  path calls `0x42050e98`, then passes the decoded payload to dispatch.
  The CBC alternative is visible at `0x42050a46..0x42050a58`; this replay
  exercises GCM, not CBC.
- Without the marker, the parser dispatches the original payload pointer
  and length at `0x42050b08..0x42050b12`. An established encrypted session
  does not cause an unmarked packet to be decrypted here.

The synthetic missing-marker case confirms that valid GCM ciphertext,
wrapped in command `0000`, reaches the controller send builder unchanged.
The generic TLV parser runs, but the factory handler ignores its descriptors
and forwards the raw payload. Inner framing, lengths and CRCs are not
validated in this handler. The MCU's interpretation is a separate matter.

## MQTT differs from BLE

The native MQTT raw-data callback `0x42043a18` branches on the normalized
command before generic parsing:

- Commands `0000..003f` enter `0x4204d824`/`0x4204d14e` as **port `5`**.
  Consequently function `0c`/command zero reaches the factory handler but
  is rejected by its port check. Changing `A1` to `20`, `21` or `22` does
  not change this result.
- Commands above `003f` go through wrapper `0x420439cc` directly to the
  controller send builder. This branch bypasses both the factory handler
  and payload decryption. Synthetic `0040`/`4040` cases show that distinction;
  they are **not alternative diagnostic requests**. The controller has its
  own function/command checks.

The replay begins at this raw-data callback, after native JSON/base64
extraction. It does not emulate the MQTT connection or JSON envelope.

## Reply, body and checksum behavior

Wrapper `0x42043aa8` supplies the original body pointer and length to
`0x4204fcae`, with function `0c`, command zero, destination port and a
timeout field of `300`. Single-frame builder `0x4204f75c` constructs a new
outer frame:

```text
ff 09 <length little-endian> 03 01 0c <command big-endian> <body> <XOR>
```

The outer destination byte becomes `01`, even for a BLE input with `00`.
The body, including `A1`, the two-byte `A2` length, inner CRC and inner
trailer, is preserved exactly. The outer XOR is recomputed by actual
firmware routine `0x42051bfc`.

A later controller response entering port `2` is a separate invocation of
the same handler. It always selects BLE port `0`, including synthetic
source bytes `00`, `20`, `21` and `22`. It is not an MQTT response selected
by `A1`. The send builder applies the destination port's encryption state:
the replay checks both clear and GCM BLE responses, while the controller
port uses a synthetic clear session. Whether a real BLE peer is present
and whether physical transmission succeeds are outside the replay.

## Reproduce the 96 synthetic cases

Requires Python, Unicorn and `cryptography`:

```sh
python3 tools/firmware_analysis/emulate_radio_factory_routes.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output /tmp/radio-factory-routes-results.json \
  --manifest /tmp/radio-factory-routes-manifest.json
```

Checked-in expected results and manifest are under
`tools/firmware_analysis/expected_results/radio-factory-routes-*.json`.
Coverage is 64 combinations of source/port/incoming encryption/BLE reply
encryption, six rejected commands, six MQTT callback cases, three damaged
outer frames, eight unregistered functions, five opaque-body cases and
four outer destination values.

Registration, validation, queue consumption, parsing, dispatch, routing,
framing and XOR execute the image's instructions. Allocation, OS queues,
logging, session keys/state, MTU and AES-GCM primitives are explicit host
substitutes. A memory-write allowlist permits only the function registry,
fragment bookkeeping, synthetic allocation space and stack. Unexpected
instruction paths fail. All data is synthetic; no phone logs, pairing IDs,
device identifiers or private credentials are included.
