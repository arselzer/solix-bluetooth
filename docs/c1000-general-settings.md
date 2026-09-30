# C1000 Gen 2 general settings

## Scope

Retained C1000 Gen 2 main 1.1.4.9 only. The instruction replays below establish
firmware behavior under their listed substitutions. Separate live validation
is recorded at the end; neither establishes identical C2000 behavior. The
offline replay itself performed no hardware or network operations.

The actual TLV parser (`080225a6`), lookup (`08022576`), `0103` handler
(`0800c530`), relevant setters/getters, software timer queries/event marking,
A4 builder (`0801a018`), and refresh descriptor builder (`08013d14`) execute.
Substitutes are persistence scheduling (`080238f4`), response transport
(`08008060`), logger, memcpy/memset, named LCD-event dispatch, and final refresh
queue transport (`08016a70`). Display-off (`0801be60`) and special-mode refresh
(`08011730`) are recorded boundaries. Their downstream effects are not replayed.

## Results and exact wire/readback mapping

All offsets below include the leading type04 byte of the 34-byte A4 field.
Native command is 0103; BLE equivalent is 4103. Use the normal A1 and timestamp
conventions for the chosen transport and include only the single target field.

| Setting | Typed field | Safe domain | Readback | Actual behavior |
| --- | --- | --- | --- | --- |
| Temperature unit | A5 = 01 + byte | 0 Celsius / 1 Fahrenheit | A4[20] | Setter0802b55c stores raw byte at20001d71; no clamp. |
| Off-grid alert | B0 = 01 + byte | 0 off / 1 on | (A4[32] >> 1) & 1 | Setter0802b2fc stores raw byte at20001ed0; field shorter than2 is ignored. |
| LCD brightness | A3 = 01 + byte | 1 low / 2 medium / 3 high | A4[18] | Setter0802b490 stores nonzero raw byte at20001d6c, schedules persistence and display event. Zero calls display-off and keeps prior brightness. |
| Display switch | A2 = 01 + byte | 0 off / 1 on | A4[22] | Only acts when exactly0/1 differs from actual display state; calls a toggle path, not a persistent boolean setter. |
| Output port memory | A8 = 01 + byte | 0 off / 1 on | A4[23] | Setter0802a294 normalizes nonzero to1. OFF also clears inverter recovery bookkeeping and cancels two recovery timers. |
| Device timeout | A6 = 02 + uint16 LE | App list:0,30,60,120,240,360,720,1440 minutes | uint16 LE A4[14:16] | Setter0802b420 stores raw uint16 at20001d59. No range check. Existing Never=0 must be preserved. |

The firmware also accepts invalid temperature and alert bytes. Alert telemetry
exposes only the low bit, but the consumer requires the stored byte to equal1.
For example3 appears enabled in readback but does not enable the consumer.
A client must reject every value except0/1 before encoding these fields.

Brightness duty lookup at0801a55c masks the saved value with3 and indexes
080316c0, containing10/20/60/100. Normal levels1/2/3 therefore map20/60/100.
Values4 and255 are stored unmodified yet alias the hardware lookup; do not expose
them. Nonzero brightness writes mark the display event even if unchanged.
When RAM2000039a bit1 is set, the path additionally sets bit3 and calls08011730;
its physical meaning and full downstream behavior remain unresolved.

## Common command side effects

Every command, including a rejected short B0 or no target field, acknowledges
and constructs an unsolicited0421 refresh descriptor: function15, delay50 in
the scheduler's units. When the timer queried by0801c2bc is active, the handler
also marks an existing display event pending through080106b4. The event loop and
callbacks were not executed. Thus a display transition or refreshed screen timer
can follow a setting write even when the requested setting alone is persistent.

Temperature and alert exhaustive tests changed only their own byte in the full
0x190-byte persistent settings region. The synthetic AC state word20000164
remained0x30. This establishes the synchronous handler's behavior under the
listed substitutions, not complete asynchronous hardware safety.

Port memory OFF calls0800fbe4 after clearing the saved switch. It zeroes
2000218c..20002197 and20000141 and cancels the timer IDs from20000144/145.
Re-enabling the switch does not restore that transient recovery state. A matching
A4[23] restoration therefore is not a full-state rollback.

Nonzero device timeout is used by0802b5e8 to establish a deadline of RTC plus
60*minutes, whose callback ultimately arms08008728. Keep Never; no timeout test
is proposed. Display OFF reaches a larger UI/display shutdown callback, whose
full effects were deliberately not substituted away in safety conclusions.

## Off-grid alert consumer

Twenty additional executions run0800d02c with the real saved-setting getter
0801a5a0 and inhibit getter0801bc7c. A change of200004be bit0 must persist for
five calls. Only a1->0 transition with alert exactly1 and200003e9 equal0 queues
event0x59, flag1, one-byte SOC payload. An additional unchanged sample does not
repeat it. The opposite transition and invalid2/3/255 settings emit no event.

SOC getter08018384 is substituted with73, and notification queue0800f76c is
captured. All actual CPU writes are limited to stack and three alert debounce
bytes200001f4..200001f6. There is no output-state write in this replayed branch.
This establishes notification generation, not delivery to an app, an audible
alarm, or behavior after a real mains interruption.

## Test counts and reproducibility

`emulate_general_settings.py`:1,063 passed:1,024 exhaustive temp/alert byte and
common-display-timer cases,4 exact restoration cases,12 brightness,8 display,
4 memory,9 timeout,2 malformed alert cases.

`emulate_offgrid_alert.py`:20 passed. Total1,083.

Run with `PYTHONPATH=python:/tmp/solix-analysis-tools:/tmp/solix-ble-deps python3
.solix-private/firmware-analysis/emulate_general_settings.py`, and similarly for
`emulate_offgrid_alert.py`. Results and SHA256 manifest remain owner-only. Manifest SHA256:
`ee400a509161c91d9108f126b4dae4ea19e6cdec296db4655899214d416dc992`.

## Live test procedure

C1000 only; obtain a fresh confirmed model/version and full A4 with length34 and
type04. Require initial target value0/1. Record AC/DC state, mains, charging
limit, upper/lower limits, reserve, usage/tariff/plan, fast charge, timeouts,
display switch/brightness, and memory. Change temperature only, confirm A4[20]
and unchanged protected power/settings fields, then restore the exact original
boolean and confirm. Repeat separately for B0 if desired, checking A4[32]bit1.
Do not interrupt mains merely to validate alert delivery.

Use a guaranteed restore path once a write is attempted; an ACK alone is not
proof of application or restoration. Keep raw captures private. Do not copy raw
B0 values from an invalid baseline based solely on the one-bit readback. Other
A4[32] bits must remain unchanged; countdown/readings may naturally evolve.
Brightness should be deferred if restoring it would also require display OFF;
its saved level does not encode the original display state. No C2000 test is
proposed from this C1000-only evidence.

## Live validation: 2026-09-30

A1763, main **1.1.4.9**, radio **0.3.3.0** reconnected to the isolated
AP using the existing generated local identity and certificates. No new BLE
provisioning, cloud request, internet route, or C2000 setting change was used.

Baseline: Celsius, off-grid alert off, 100% battery/idle, mains connected,
AC on/DC off, Standard/none/zero slots, reserve 10%, charge bounds 100%/1%,
1200 W charging limit, fast charge off, device timeout Never.

- Native `0103 A5` changed Celsius → Fahrenheit → Celsius.
- Native `0103 B0` changed alert off → on → off.
- Each command was confirmed with fresh native telemetry, followed by a separate
  delayed status read. An acknowledgement alone was not treated as success.
- All 18 recorded protected settings matched after each restoration and in
  three final native samples. Independent BLE confirmed the same baseline
  after stopping the AP. AC output stayed on in every captured sample.
- The alert's real outage notification, sound, and app delivery remain untested.
  This trial verified setting storage/readback; it did not interrupt mains.

The SDK checks a fresh, complete type04/34-byte A4 and complete D9, preserves
unrelated setting bytes (allowing naturally decreasing output countdowns and
active tariff changes at a boundary), and requires the target value to read
back exactly. Invalid types, unsupported models and disabled controls fail
before I/O. Writes are never automatically retried or restored by the SDK.
The bounded research trial supplied its own explicit restoration procedure.

CLI examples for an already running writable AP service:

```sh
solix-link ap-service-set-temperature-unit --unit fahrenheit
solix-link ap-service-set-off-grid-alert --state on
```

Restore the values you recorded, rather than assuming the defaults above.
These controls are C1000 Gen 2 only; they are not exposed for C2000.

Raw API/MQTT/packet logs and the test scripts remain owner-only in the ignored
private archive. Verified result archive SHA256:
`bee46a789b5cee5181680c27088d910910e0639f69cff880785b9c8671cad8e5`.
