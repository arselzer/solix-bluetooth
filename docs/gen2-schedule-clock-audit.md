# Gen 2 tariff clock and UTC-offset retention

## Finding and scope

The C1000 Gen 2 main controller **1.1.4.9** does not clear an existing timezone
offset when synchronization supplies zero. Its radio **0.3.3.0** also encodes
a zero timezone offset as the sentinel `-1`, which the controller ignores.
Consequently, an `Etc/UTC` service profile and correct UTC status timestamps
do **not** establish that the controller evaluates schedule hours in UTC.

This September 30, 2026 audit combines retained local observations, static
radio tracing and **47 actual ARM instruction replay cases**. It made no
device or network request. The offset behavior was first recorded in the
[earlier clock analysis](mqtt-power-offline-followup.md); the new replay links
it directly to plan storage, active-tariff reporting and boundary changes.
These code addresses apply to C1000 firmware; C2000 equivalence is unproven.

The packaged control helper rejects activation of hourly C1000 plans while
the configured timezone has zero UTC offset. Saving an inactive plan and
all-day tariffs remain available; C2000 behavior is unchanged. A nonzero
timezone still needs observed clock/selection validation—it is not a measured
RTC readback. The later [local-time trial](c1000-charging-and-reserve-validation.md)
records a natural boundary separately from this offline audit.

## Retained live observation

A C1000 trial used a service profile configured as `Etc/UTC` and this plan:

| Tariff | Start | End |
| --- | --- | --- |
| Peak (`1`) | 00:00 | 15:00 |
| Off-Peak (`3`) | 15:00 | 24:00 |

All **63 MQTT status frames** containing the enabled plan between
**14:58:05.699 and 15:01:31.498 UTC** reported Off-Peak. The first such frame's
`FE` timestamp was 14:58:05 UTC. Across the whole retained run, 561 timestamped
status frames trailed receipt by 0.086–1.207 seconds, so gross UTC skew was
not apparent in `FE`.

The plan bytes were correct: `D9` held count `02` and triplets
`01 00 0f 03 0f 18`. This observation does not reveal the controller's stored
offset or raw RTC. A retained UTC+1 or UTC+2 setting would explain the early
Off-Peak selection, but neither was read directly in this trial. Raw captures,
identities and service configuration remain private.

## Clock path in the recovered firmware

| Stage | Address | Relevant behavior |
| --- | --- | --- |
| Radio SNTP callback | `4203b6d2` | Receives UTC; obtains the applicable timezone offset. Before its callback at `4203b7be`, negates a nonzero offset or supplies `-1` for zero. |
| Radio timezone timer | `420382a4` | Reads current time through `42035dce`, selects the applicable offset through `42036660`, and uses the same negation/zero-sentinel rule at `420383de`. |
| Radio callback registration | `42048b00` | Registers `42043026` through `4203b968`; this callback forwards UTC and offset to `4203e3ee`. |
| Radio clock packet builder | `4203e3ee` | Builds untyped four-byte A1 UTC and A2 offset, then sends internal function `10`, command `0026` through `4204fcae`. |
| Main clock handler | `0800cc18` | Keeps prior UTC for `0`/`ffffffff`; keeps prior offset for `0`/`ffffffff`; otherwise updates cached and persisted offset. |
| Main RTC synchronization | `08014668` | Attempts `RTC = UTC − offset`; RTC readiness failure skips the write despite outer success ACK `00`. |
| Main status timestamp | `08018e4c` | Emits `FE = RTC + stored offset`, thereby hiding a retained offset when UTC synchronization succeeds. |

The radio rows are static findings; this audit does not emulate SNTP, radio
tasks or UART delivery. The controller rows execute in the new replay with
synthetic RTC registers. Offset here means **seconds west of UTC**: `-7200`
makes the RTC two hours ahead of UTC.

At 14:58:05 UTC with the two-slot plan, the actual controller instructions give:

| Previous offset | Incoming offset | RTC hour | `FE` | Active tariff |
| --- | --- | --- | --- | --- |
| `0` | `0` or `-1` | 14 | 14:58:05 UTC | Peak |
| `-3600` | `0` or `-1` | 15 | 14:58:05 UTC | Off-Peak |
| `-7200` | `0` or `-1` | 16 | 14:58:05 UTC | Off-Peak |

A repeated zero-offset sync after a nonzero sync retains the nonzero offset.
Supplying a different nonzero offset updates it in the replay; this finding
is not an instruction to send internal clock writes to a live station.

## Schedule evaluation and recomputation

Selector `0801bcc4` requires enabled mode, bound/network readiness and power
gate `200004be.0`. It then reads RTC through `08029cbc`, converts the epoch
through `08005210`, and compares `tm_hour` against stored triplets:

```text
first slot satisfying start_hour <= RTC_hour < end_hour wins
otherwise return tariff 0
```

The hour is `(RTC // 3600) % 24`. There is no additional timezone application
or valid-clock check here. Minutes and seconds affect the result only when
the RTC crosses an hour. Overlapping slots use their stored order; an
overnight period must be split at midnight.

**This selector has no cached active tariff.** Repeated execution on one
emulator instance, with unchanged plan/settings, yields Peak → Off-Peak → Peak
at 14:59:59 → 15:00:00 → 14:59:59. Its only memory writes are stack and calendar
conversion scratch space. Actual `D9` serialization matches every result.
Updating, disabling and re-enabling the plan also takes effect on the next
invocation, with no initialization wait in these paths.

This does not establish the live scheduler's invocation interval or eliminate
separate power-policy state. Tariff selection, status transmission and physical
power-flow switching are distinct observations.

## Reproduce and use the result

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-clock-audit \
  python3 tools/firmware_analysis/emulate_schedule_clock.py
```

The tool validates the published main image hash, runs the actual plan parser,
handler, clock synchronization, tariff selector and `D9`/`FE` builders, and
writes `schedule-clock-results.json`. It substitutes persistence, transport,
logging, ticks, refresh and backup-status calculation. RAM, RTC registers and
readiness/power gates are synthetic; no firmware is flashed. The 47 cases are
separate from the existing combined replay count.

Before claiming a successful hourly schedule test, establish the controller's
effective local hour independently, then record both sides of a natural hour
boundary with `D9`, `FE` and measured power flow. Merely relabeling the local
service profile as UTC or receiving a clock ACK cannot provide that evidence.
The known all-day controls remain useful because they do not depend on which
hour the controller believes it is.
