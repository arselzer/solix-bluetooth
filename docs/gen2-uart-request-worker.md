# Gen 2 internal UART request worker and completion limits

Offline analysis dated 2026-10-01 of public **A1763 C1000 Gen 2 main
1.1.4.9**. No device, network or public command was used. The findings apply
to the internal main-to-module UART implementation in this image; they do
not establish original C1000, C2000 or another firmware's behavior.

This connects the actual transport worker to the callbacks in
[PV bridge and restart](gen2-pv-bridge-and-restart.md). It qualifies that
document's failure-count interpretation: **some unsuccessful requests are
discarded without a callback, so they do not advance its failure counter.**

## Entry points and descriptor ownership

| Address | Role |
|---|---|
| `0800e334` | Initializes two queues and registers the worker timer |
| `08027e40` | Enqueues a 24-byte descriptor |
| `0801b518` | Pops the priority queue, then the ordinary queue |
| `0801b400` | Sends one descriptor and counts down completion timeout |
| `0801b720` | Serializes write requests |
| `0801b54c` | Validates and dispatches buffered internal replies |
| `0801b2d4` | Bulk DSP-status completion callback |
| `0801649c` | MPPT configuration completion callback |

The active descriptor is at `200034e8`; busy state is byte `200003a4`.
Descriptor offsets:

| Offset | Meaning established by the worker |
|---|---|
| `+0` | Completion callback pointer, or zero |
| `+4` | Worker countdown, overwritten after serialization |
| `+8` | 16-bit module selector |
| `+10` | 16-bit register |
| `+12` | 16-bit requested read length or write payload length, in bytes |
| `+16` | Allocated write-payload pointer, or zero |
| `+20` | Zero selects a read; nonzero selects a write |

Each queue has 20 storage slots and **19 usable descriptors**, because one
slot distinguishes full from empty. Queue argument 1 selects priority;
the other reviewed argument 0 selects ordinary. The pop helper always tries
priority first. Insertion mode 0 appends, 1 prepends, and 2 appends while
discarding the oldest entry if full. The overflow path does not invoke the
discarded descriptor's callback. These are internal queue arguments, not
public command or register values.

## One send, followed by a countdown

Registration at `0800e364` passes worker `0801b401` to the repeating-timer
installer with **period argument 10**. The replay records that registration;
it does not execute the timer interrupt or measure its physical cadence.

When idle, the worker first clears the active descriptor, then pops one
queued item. It marks busy and emits an uppercase `MAIN` frame through
internal port 2:

- Read operation `1001`: fixed 18-byte frame, requested register and length.
- Write operation `1002`: 18 bytes plus payload. The write serializer rejects
  lengths above 50, without emitting a frame.
- Both use the actual CRC-16/MODBUS helper with initial `ffff` and polynomial
  `a001`; the numeric CRC is stored little-endian.

The worker does **not** check the UART send return value or the write
serializer's return value. It then frees a nonzero payload pointer and sets:

```text
countdown = floor(requested_bytes * 6 / 100) + 13
```

The same invocation immediately decrements the countdown once. Later worker
invocations decrement it while busy. On reaching zero, the callback receives
`(0, NULL, 0)`, and active descriptor, busy state and buffered-RX bookkeeping
are cleared. No retransmission occurs inside this worker.

Examples from actual-instruction replay:

| Request | Timeout callback at invocation, counting initial send |
|---|---:|
| Two-byte write or 12-byte MPPT configuration | 13 |
| 50-byte write | 16 |
| 146-byte bulk DSP-status read | 21 |

Twenty tests cover read/write lengths and synthetic UART returns 0/1,
including an oversized 51-byte write. Even that rejected serialization
leaves a countdown and eventually fails through the callback. This is an
offline boundary case, not a proposed command.

The table gives **worker invocation counts**, not measured milliseconds.
Queue backlog, scheduling, sleep, interrupt timing and receive interleavings
are not modeled. For retries after failure, see the higher MPPT policy in
the preceding document; it is a separate layer from this send-once worker.

## Receive acceptance differs from correlated completion

The parser searches the buffered bytes for lowercase `main`, waits for the
declared full frame and validates CRC. It then dispatches operations `1001`
and `1002`. Response correlation compares **only the register** with the
active descriptor. It does not require matching selector, request operation,
or expected response length in the replayed path.

### Read responses

For operation `1001`, selector 1 can update the DSP cache at `20003f00`;
selector 4 has a separate BMS-cache path. This happens **before** checking
whether the register matches the pending request. A matching register invokes
the callback with `(1, payload_pointer, reported_length)`.

The parser then clears the active descriptor and busy state, even when the
register did not match and no callback ran. A one-byte read payload for a
pending 146-byte read can therefore produce a successful callback while
refreshing only one byte. A matching register under another selector can
produce a callback without refreshing the DSP cache at all.

### Write responses

For operation `1002`, matching register plus initial payload bytes **`aa ee`**
invokes callback success. Other initial bytes invoke failure. The callback
receives the reported payload pointer/length in either case.

A write response with the wrong register does not invoke the pending callback.
It clears busy state, but retains the active descriptor/countdown until the
next worker invocation. That invocation sees idle, clears the descriptor and
tries the next queued item. The old timeout is therefore lost.

### Rejected or incomplete frames

| Synthetic receive condition | Effect on an active request |
|---|---|
| Complete matching frame with bad CRC | Clears busy; descriptor/countdown discarded next worker call; no callback |
| Complete frame with unsupported operation | Same discard path; no callback |
| Incomplete header or declared body | Retains active request; ordinary countdown can expire |
| 20 bytes without `main` magic | Retains active request; ordinary countdown can expire |

The bad-CRC result requires a complete frame with the recognized `main`
header and usable declared length; it is not a claim about every arbitrary
corrupt byte sequence. Full-buffer resynchronization and concurrent IRQ
arrival are not covered. Replays also verify that none of these paths silently
resends the pending frame during 25 subsequent worker calls.

## Actual callback consequences

### Bulk status: six failures require six callbacks

An integrated test queues the real `08016600` bulk-status request and receives
one complete successful `0100` response. The actual callback sets its counter
to 1. It then queues six more bulk reads, each followed by a valid unrelated
`0126` read response:

- The incoming `0126` bytes update that part of the DSP cache.
- No bulk-status callback is delivered.
- The counter remains 1; the bulk cache is not cleared.

Six subsequent bulk requests with **no replies**, each allowed to expire
after 21 worker invocations, do invoke six failures. The callback then clears
all 146 cache bytes and requests error 25/display refresh 10, as previously
observed. Error priority may select a different public reported code.

Thus neither the absence of communication-failure state nor a successful
callback establishes that all status fields are fresh. Conversely, a zeroed
cache does not prove physical mains loss, AC interruption, fault clearing or
energy transfer. This is a software-cache observation, with physical outputs
outside the replay.

### MPPT configuration: accepted reply queues a start

The suite executes real builder `0802a664`, sends its 12-byte register `0016`
configuration and supplies a correlated write reply. `aa ee` reaches actual
callback `0801649c`; with the synthetic current DC-input flag set, that
callback queues register `0015` word `0057`. The next worker call serializes
that second request. A negative reply delivers failure and queues no start.

This verifies the transport-to-callback link. It does not establish that a
physical DSP accepted or executed the start, or reveal an external route to
the separate `0059` fault-clear word.

## Reproduction

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/uart-request-worker \
  python3 tools/firmware_analysis/emulate_uart_request_worker.py
```

Use the repository's firmware-analysis dependencies. The script enforces
the main-image SHA-256; `SOLIX_FIRMWARE_DIR` optionally selects the directory
containing the exact published image. Expected outputs are
`tools/firmware_analysis/expected_results/uart-request-worker-*.json`.

**45 cases:** 20 send/timeout boundaries, 15 reply-correlation/parser cases,
7 queue-order/capacity cases and 3 integrated real-callback cases. Actual
ARM instructions execute queue initialization/push/prepend/pop/overflow,
worker countdown, serializers, CRC, receive parser, communication counters,
configuration/start builders and the selected callbacks. Each inspected state
asserts saved settings and the global input/output word are preserved.

Substitutes are UART port-2 send/return, allocation/free, timer registration
and activation, logging, memory helpers, alarm/display delivery, synthetic
RX buffers and the invocation schedule. UART ISR/DMA, physical peripheral,
timer interrupt, higher retry policy, radio/app forwarding and concurrent
interleavings are excluded. No production protocol or recovery API changes
are made by this investigation.
