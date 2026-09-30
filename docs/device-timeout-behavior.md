# Device Timeout, idle sleep and control availability

## Scope and practical conclusion

This is offline evidence for **C1000 Gen 2 (A1763), main 1.1.4.9**. The [replay](../tools/firmware_analysis/emulate_device_timeout.py) executes 155 synthetic cases against the published firmware. No station, network, output or real timer was used.

**Device Timeout = 0 (Never) disables the configured timeout entry. It does not disable all sleep.** Keeping Never is appropriate when avoiding this automatic shutdown route, but it cannot guarantee Bluetooth advertising, MQTT connectivity, recovery from faults, or access while another client holds the connection. Original C1000 and C2000 implementations are not established by this image.

## Setting and readback

The tool exposes `set-device-timeout` on original C1000 legacy Bluetooth and
C1000 Gen 2 Prime/native MQTT. Choices are **0, 30, 60, 120, 240, 360, 720 or
1440 minutes**; zero is labelled Never. C300 and C2000 writes are excluded.
The terminal, browser, authenticated HTTP and HA selector use the same command.

```sh
solix-link set-device-timeout --name c1000-original --minutes 0
solix-link ap-service-set-device-timeout --directory /path/to/private-ap --minutes 0
```

Original C1000 also supports bridge topic
`solix_gen2/original/set/device_timeout` with nonretained `{"minutes":0}`.
Record a baseline before choosing a finite timeout: the station may turn off
when idle. Success requires fresh telemetry; no failed write is automatically
retried or rolled back.

- Native `0103`, A6 value `02` followed by a little-endian unsigned 16-bit minute count.
- Setter `0802b420` stores the count at settings `20001d48 + 11` and requests persistence. It does not directly cancel or restart a timer.
- Getter `0801a5fc` returns that count; the complete C1000 type-04 A4 block reports it at **A4[14:16]**.
- Zero means Never in the consumer. Nonzero values become `minutes × 60` RTC seconds.

### Physical checks on 2026-09-30

| Station / transport | Trial | Confirmed result |
| --- | --- | --- |
| Original C1000 A1761, code 151; legacy BLE | `4045`, A2/type02: **720 → 0 → 720 min** | Saved Never and restoration of original 12-hour setting |
| C1000 Gen 2 A1763, main 1.1.4.9 / radio 0.3.3.0; Prime BLE | `4103`, A6/type02: **0 → 0** | Fresh Never readback; Never kept |

Three final samples matched protected outputs, charging, limits and other
reported settings. Both AC outputs remained on; no output or Wi-Fi command
was sent and C2000 was excluded. The original trial validates D2 readback and
its legacy command, not the A1763 internal sleep implementation. The Gen 2
trial is idempotent; finite Gen 2 shutdown behavior and the native MQTT timeout
write remain untested on hardware. Native framing and confirmation failures
have synthetic transport tests. No real shutdown or waveform test was run.

The ignored owner-only archive retains notifications, raw fields, baselines,
results and restoration checks. SHA-256:
`9688eb349d3560c98b4b7562242db3146e2c0b8d7798661d24169ddaedcdf773`.

A second C1000 Gen 2 idempotent Never trial verified the strengthened BLE
setter against complete fresh A4/D9/output blocks before and after the write.
Three final samples again matched the baseline. Its private archive SHA-256 is
`a574877b265083647320656a69b206812457de1bb1dfc315a41fe04a7e8dc80e`.

The replay checks storage boundaries including 65535 to understand the handler, not to endorse every unsigned value as a supported UI choice. A production setter should use its documented choices, verify fresh readback, preserve unrelated configuration and avoid changing output timers.

## Two separate timing stages

### Inactivity before sleep

`0800eff0` registers callback `0800f124` at a repeating interval of 1000 software-timer units. That callback maintains a separate countdown at `200004da`:

1. Activity resets the countdown to 60.
2. Without qualifying activity, each callback decrements a nonzero countdown.
3. At zero, it calls the RTC wake-table selector `0802b5e8`.

The main loop independently calls `0800f07c`. Its first check reads this countdown; zero permits reaching the physical sleep routine at `0800f018`. **Device Timeout is not read by this sleep-entry check.** Four cases replay the actual predicate and stop at the physical boundary: idle reaches it with both Never and a 30-minute timeout; active PV state resets the countdown and prevents reaching it.

The interval and count are consistent with nominal one-minute inactivity, but no elapsed real time or physical sleep transition was measured. Software timer delivery and hardware sleep are outside this replay.

### Configured timeout after inactivity

The selector maintains four wake entries at `200034a0`. Entry 1 is Device Timeout (`0802b640..0802b664`):

- Zero marks this entry disabled, so it does not participate in nearest-alarm selection.
- A nonzero unarmed entry receives deadline `RTC now + minutes × 60`.
- An already armed entry retains its previous deadline.

The selector can still consider other wake entries when Device Timeout is zero. Its final call to `080146c8` only logs calendar time; it is not a shutdown or sleep operation.

At the selected timeout alarm, dispatcher `08029b7c` invokes `08029bb8`. This creates or activates a one-shot timer with interval 5000 units and callback `08008728`. That callback ORs **event bit `1000`** into `20000436` and requests immediate processing by the main event handler. It does not directly cut power.

Static inspection of the downstream handler shows additional busy and power-state guards at `0800697e..080069ca`, followed by a BMS shutdown path when they permit it. The replay stops before this consumer: generating a synthetic event is not evidence that shutdown would occur under every physical state.

## What counts as activity

The idle callback tests controller state and timers; it does not compare raw input/output watt measurements. Its exact predicate was replayed with each relevant input isolated:

| Source | Condition that resets the countdown |
| --- | --- |
| State word `20000164` | Any of bits 0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 18, 20 or 23 |
| Same state word, bits 4–5 | The two-bit value equals **1** |
| Gate byte `200004be` | Bit 0 set, or bit 1 set while special-mode getter `08015494` is false |
| Display-state query `0801bf3c` | Its software timer is active |
| Getter `0801883c` | Byte `200028fa` equals 1 |
| Getter `08019754` | Byte `20000728` is nonzero |

Bit 0 is the previously traced PV/DC input state. The gate's bit 0 is the separately studied AC-module readiness flag. Other flags are intentionally left as raw conditions unless their producer is independently understood; their presence does not establish a particular load threshold.

The AC output enabled telemetry getter returns true for **any nonzero** bits 4–5, while this idle predicate specifically requires value 1. Eight additional cases demonstrate the difference. A single `ac_output_enabled` boolean cannot replace the full inactivity predicate. The physical producers of the AC substates were not executed, so these synthetic combinations do not predict whether a particular real load will sleep.

The IoT-enable state bit 28, associated with the firmware's enable/disable-IoT button path, does **not by itself** reset this countdown in the one-bit tests. This is not a simulation of a connected BLE or MQTT session: other state flags and timers can differ while connected.

## Changing a timeout that is already scheduled

Six cases isolate a subtle distinction between saved configuration and queued work:

1. Schedule a finite timeout and mark the RTC alarm armed.
2. Run the actual setting setter with a replacement value, including zero.
3. Call the scheduler again while its global armed flag remains set.

The scheduler's early return at `0802b5f8..0802b60e` runs before it reads the new timeout. The existing table/deadline remains. Invoking wake-reset `0802b1d0` clears all four entry-arm flags; the next selection then honors the replacement, including skipping zero.

That reset is called after sleep when wake-cause getter `08019460(13)` returns zero (`0800f0cc..0800f0da`). This is a conditional path, not a claim that every wake clears every alarm. The command transport, wake-cause producer, interrupt delivery and already queued callback cancellation are not modeled here.

Consequently, fresh A4 = 0 proves the saved Never setting. It does not independently prove immediate cancellation of every previously queued event. Conversely, this synthetic armed-state experiment does **not** show that a normal app or SDK write leaves a live shutdown pending; an ordinary communication wake may already have taken the reset path. Do not invent a cancellation command or toggle outputs to work around this uncertainty.

## Reproduction and boundaries

Use the [published offline requirements and firmware input rules](firmware-analysis-reproduction.md):

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-timeout-replays \
  python3 tools/firmware_analysis/emulate_device_timeout.py
cmp /tmp/solix-timeout-replays/device-timeout-results.json \
  tools/firmware_analysis/expected_results/device-timeout-results.json
```

| Group | Cases |
| --- | ---: |
| Timeout selection and deadline preservation | 18 |
| Pending alarm, replacement setting and wake reset | 6 |
| Individual state bits | 64 |
| Power gate and special mode | 32 |
| Display and auxiliary busy states | 16 |
| AC substates versus enabled telemetry | 8 |
| Sleep-entry boundary | 4 |
| Alarm callback and main-event request | 6 |
| Idle timer registration | 1 |
| **Total** | **155** |

Actual setting/getter, idle predicate, RTC arithmetic, wake-table selection/reset, callback dispatch and event-bit instructions execute. Timer installation/activation, persistence, IRQ configuration, calendar formatting and event delivery are recorded substitutes. Hardware sleep is a stopping boundary. There is no BMS shutdown execution, sensor/port-state producer, operating-system timer scheduler, transport session or physical radio model.

Input is `MainMcu-decoded.bin`, SHA-256 `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`. Tested with Python 3.12.3 and Unicorn 2.1.4. The new manifest records source/result hashes independently of existing replay manifests. All published results are synthetic; no private identifiers, credentials or captures are included.
