# Gen 2 factory USB transport and stream limits

## Result and scope

The public **A1763 C1000 Gen 2 main 1.1.4.9** image has a complete software
path from USB OUT endpoint 3 through the factory parser to replies on IN
endpoint `81`. **23 synthetic instruction cases pass.** This supplies a
different firmware transport from the still-uninstalled
[radio diagnostic command table](gen2-diagnostic-table-ownership.md).

No USB enumeration, cable, charging port, station command, output change or
network access was performed. Physical access to this controller USB interface
is **unverified**. These findings do not establish that an external USB-C or
USB-A power socket exposes it, or that another model implements the same path.
There is no new USB SDK/CLI control.

Input: bundled `MainMcu-decoded.bin`, 198,656 bytes, loaded at `08005000`,
SHA-256 `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.

## Actual path

| Entry / state | Role |
|---|---|
| Startup `08005a8c..08005aa2` | Decompresses the USB class callbacks and device descriptor into RAM |
| Class callback table `200009b0` | Init `08014c30`, deinit `08014c02`, setup `08014cac`, control RX `08014b38`, DataIn `08014b70`, DataOut `08014bc8` |
| Ring initializer `0800f55c` | RX descriptor `20006b70`, buffer `20006b90`, size `0464`; TX descriptor `20006b80`, buffer `20006ff4`, size `0164` |
| DataOut `08014bc8` → `08011608` | Enqueues bytes only for endpoint 3, clears received-length state and rearms a 64-byte receive |
| Main entry `08020624` → `08011640` | Registers parser `0802f6ac` with repeating-timer period argument 10 |
| Parser `0800de18` → dispatcher `0802f6ac` | Parses the inner factory stream and dispatches its property table |
| Reply serializer `0800dbdc` → `08011628` | Appends the framed reply to the TX ring |
| USB service `08014d24` / DataIn `08014b70` | Drains up to 64 bytes per send boundary and chains completion-driven sends |

Startup produces an 18-byte device descriptor at `200009cc`:

```text
12 01 00 02 02 00 00 40 e9 28 8a 01 00 01 01 02 03 01
```

It contains **VID `28e9`, PID `018a`**, device class `02`, USB version
`0200` and endpoint-zero packet size 64. These identify a firmware descriptor,
not an observed host device. No physical serial number was read or published.

The stream uses the [existing inner format](gen2-diagnostic-getter-audit.md#inner-parser-and-serializer):
`EE 00 length_LE16 selector property_LE16 data CRC_BE16 FC 55`.
There is no BLE `ff09`/TLV/account wrapper in the replayed USB path. The suite
uses only selector-0 model/version getters (`0001`/`0002`), independently
checks their constant 16-byte replies and executes real CRC instructions.
That does not make arbitrary factory entries safe.

## Limits a future USB client must respect

**Keep one request outstanding.** Once a complete request is present, parser
`0800ded8` reads up to `041f` bytes from RX, rather than only that frame's
declared length. Two concatenated 11-byte requests produce only the first
27-byte reply; the second request is discarded. Receiving and parsing one
request before submitting the next works in the separate case.

**Drain replies promptly.** The rings reserve one slot: RX holds at most
1,123 bytes; TX holds at most **355 bytes**. Enqueue returns are ignored by
both DataOut and the reply serializer. Fifteen model queries, parsed separately
without TX service, create 405 reply bytes but retain only 355: 13 complete
frames plus four bytes of a truncated frame. An RX overflow case similarly
retains 1,123 of 1,152 incoming bytes while rearming every receive.

**Do not assume a send return schedules recovery.** The pump consumes ring
bytes and sets busy byte `20000968` without checking the USB send return.
With synthetic return 1 and no completion callback, ten later pump calls
leave busy set and a subsequent reply waiting. This tests the ignored return
and missing-completion state; it does not identify the peripheral driver's
real error conditions or prove that those bytes physically reached a host.

**Incomplete input can expire.** Nine unchanged request bytes survive four
parser invocations and are discarded on the fifth. Growing fragmented input
resets that counter; 1-, 7- and 64-byte fragmentation cases complete correctly.
Timer interrupt cadence, scheduling and USB arrival timing are excluded, so
these are invocation counts, **not measured milliseconds**.

The DataIn full-64-byte branch requests an additional zero-length send before
draining the next chunk. The 355-byte replay produces packet lengths
`64,0,64,0,64,0,64,0,64,0,35`; packet boundaries are not factory frame boundaries.
The zero-length branch passes endpoint index 1 after masking direction;
nonempty sends use `81`.

## Side effects and further work

Every accepted factory frame still reaches the shared upgrade-reset-timer
stop, even a getter. The [getter audit](gen2-diagnostic-getter-audit.md#side-effects-these-are-not-passive-enumeration-requests)
documents that effect and other hazardous entries. USB transport does not
provide a complete disaster-plan export or remove those prerequisites.

The next useful evidence would be physical enumeration of this specific
interface on a noncritical station or documented controller service access,
then a single bounded model/version exchange with explicit restoration and
no firmware update in progress. Exposing a USB backend requires framing,
one-request serialization, queue/timeouts and actual reply-route validation.
No physical port or pin assignment is inferred from this firmware audit.

## Reproduction and substitutions

```sh
python3 tools/firmware_analysis/emulate_gen2_usb_transport.py \
  --image firmware/c1000_gen2/1.1.4.9/MainMcu-decoded.bin \
  --output /tmp/gen2-usb-transport-results.json \
  --manifest /tmp/gen2-usb-transport-manifest.json
```

Install the [analysis dependencies](../tools/firmware_analysis/requirements.txt).
The script checks the exact image hash, requires assertions and writes
synthetic results/source hashes. It uses the prior getter harness, with real
FIFO initialization/enqueue/count/read/peek, parser, getters, serializer and
USB callback/pump instructions. Write guards protect saved settings and the
input/output word throughout the guarded USB cases.

Host substitutes cover memory primitives/logging, USB receive/send boundaries,
timer registration/start and synthetic endpoint completion bookkeeping. Actual
USB enumeration, peripheral registers, DMA/interrupts, electrical ports, OS
drivers, full boot/mode qualification and concurrent arrivals are excluded.
