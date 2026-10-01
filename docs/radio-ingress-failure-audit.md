# Gen 2 radio frame rejection and decryption failures

## Result and scope

**47 offline instruction cases pass** against the published A1763 C1000 Gen 2
radio **0.3.3.0** image, SHA-256
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.
No station, controller, account, network or device command was used. These
findings do not establish original C1000 or C2000 behavior.

The firmware distinguishes invalid outer framing, an absent/invalidly sized
encrypted body, and a failed decryption primitive. It also lacks a complete
minimum-frame check in the reviewed outer receiver. A configured byte-dump
callback can receive both halves of the synthetic session material on the
decryption-failure branch.

## Encrypted-body failures

The receiver `4204d14e` validates sync, declared length and XOR before copying
a frame into its queue. Queue processor `4204d30e` calls parser `4204ffbe`.
At `42050212`, command high-byte bit `40` chooses encrypted payload handling;
the [header guard](ble-encryption-framing.md) remains necessary.

Using synthetic established sessions, the actual parser and error serializer
produce these results:

| Input / observation | Decrypt primitive reached | Reply status byte |
| --- | --- | --- |
| Marked frame with empty body | No | `08` |
| CBC selected, nonempty body length not divisible by 16 | No | `08` |
| GCM ciphertext/tag modified or truncated; host primitive fails | Yes | `01` |
| Synthetic nonzero GCM/CBC primitive result | Yes | `01` |

`01` here is the radio parser's error result, not a generic definition of every
controller or device error. Primitive returns `1`, `3` and `ffffffff` all reach
the same GCM failure branch; the replay does not identify an ESP error enum.
A valid GCM encoding of empty plaintext still reaches dispatch: an empty
**wire body** and successfully decrypted empty **plaintext** are different.

The failure cases never enter the function dispatcher or factory handler and
never enqueue controller traffic. Error serializer `4204fa68` and immediate
reply formatter `4204f846` execute actual instructions and XOR calculation.
For the chosen function `0c`/command `4000` cases they preserve the header,
set response bit `08`, and create command `4800`. BLE port 0 and native raw
MQTT callback port 5 produce a reply for the incoming port. This suite stops
at port-send wrapper `42053ec0`; the separate
[RSSI route audit](radio-rssi-routing.md) follows that wrapper further.

Session-key provision and cipher selection are explicit host substitutes.
GCM authentication uses Python's AES-GCM with a fixed synthetic key/nonce/AAD;
CBC failure returns and CBC reply encryption are substituted too. This tests
the firmware's response to those primitive results, **not** hardware key
exchange, certificate/account validation or the internal AES implementation.

## Outer framing has a minimum-length gap

The reviewed receiver checks a nonnull buffer and nonzero length, but has no
ten-byte minimum check before inspecting header offsets 1–3.

- With caller lengths **1–3**, the actual instructions read past that stated
  input length. The harness supplies readable padding and records the read
  provenance; it does not emulate a physical allocation fault.
- Consistent length/XOR envelopes of **5–9 bytes** are accepted into the queue,
  even though they cannot contain the full protocol header and checksum.
- Zero length and the selected 1–4-byte cases do not queue a frame. For four
  bytes, checksum and the overlapping length-high byte cannot both satisfy
  the selected `ff09` envelope.
- Damaged sync, declared length or XOR is rejected. Two concatenated complete
  frames passed as one receive buffer are rejected because the supplied length
  differs from the first frame's declared length.

All short-frame cases stop after the receiver. **The downstream parser is not
executed**, and no claim is made about its eventual effects, physical transport
acceptance, allocator contents or exploitability. Do not send these synthetic
inputs to a station. The Python `parse_packet()` already rejects lengths below
ten, mismatched declared lengths and invalid XOR; no runtime change is needed.

## Failure byte dumps can contain session material

GCM failure calls byte-dump wrapper `42051ba0` at `42050a2e` and `42050a38`;
the CBC failure branch has corresponding calls at `42050a7a` and `42050a84`.
Each supplies 16 bytes from request context offsets `612` and `622`.

Executing actual formatter `42051a5c` and logging routine `42051ace` confirms:
when callback word **`3fc906dc`** is nonzero, both synthetic key halves are
formatted as hex and passed to that callback at level 4. With no callback,
neither line is emitted. These calls are **logging, not key clearing**.

The suite installs a synthetic callback solely to observe this branch. It
does not establish whether production logging enables it or where logs go.
Treat retained device/debug logs as private: this finding reinforces the
repository's restricted-capture policy and provides no reason to provoke
authentication failures on a live device. Public results report booleans and
counts; no captured key or identifier is included.

## Reproduction

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_radio_ingress_failures.py \
  --image firmware/c1000_gen2/1.1.4.9/c1000-radio-validated.bin \
  --output /tmp/radio-ingress-results.json \
  --manifest /tmp/radio-ingress-manifest.json
```

The script enforces the image's exact size/hash and requires assertions.
Synthetic results and source/hash manifest are in
`tools/firmware_analysis/expected_results/radio-ingress-*.json`.
Coverage: eight GCM failure cases, three primitive-return variants, fourteen
body-length/cipher cases, three valid GCM cases, ten short outer envelopes,
five outer corruptions and four logging-callback cases.

OS allocation/queues, general logs, session state and AES primitives are host
substitutes. Receive validation, parser branches, selected byte-dump formatter,
dispatch, response formatting and checksums execute real RISC-V instructions.
Write guards and instruction limits apply. Physical BLE/MQTT/UART, controller
interpretation, concurrent arrivals and a complete radio boot are excluded.
