# Original C1000 and C1000 Gen 2 chain validation

## Setup and versions

On **2026-09-30**, the original **C1000/A1761** received AC input from the
**C1000 Gen 2/A1763**. An HP switch on the original C1000's AC output was an
explicitly expendable test load. The laptop ran the current Python library.
The C2000 was excluded from every connection and command.

| Device | Protocol / version evidence | Initial configuration |
| --- | --- | --- |
| Original C1000 | Legacy P-256/AES-CBC; B3 `029700`, decimal code 151, reference formatting 1.5.1 | SOC 100%, AC on, DC off; charging limit 1000 W; display off, timeout 30 s, brightness 2, light 0; device timeout 720 min; fast charge off |
| C1000 Gen 2 | Prime; main 1.1.4.9, radio 0.3.3.0; saved working pairing identity | SOC 100%, AC input/output on, DC off; charging limit 1200 W; caps 100%/1%, reserve 10%; Standard, no tariff, zero slots; device timeout Never; fast charge off |

The original firmware formatting has not been independently confirmed in the
app. Main temperature and health reported 22°C and 100%; these are telemetry
values, not independent measurements of battery health.

## Library controls and restoration

The original C1000 used the public `set_c1000_setting` method. Every write
required fresh telemetry matching its expectation. Unrelated settings on both
stations were compared with the captured baseline.

| Original setting | Trial / restored baseline | Wire command |
| --- | --- | --- |
| Display enabled | off → on → off | `4052` |
| Display timeout | 30 → 60 → 30 s | `4046` |
| Display brightness | 2 → 3 → 2 | `404c` |
| Light mode | 0 → 1 → 0 | `404f` |
| AC charging limit | 1000 → 300 → 1000 W | `4044` |
| DC output | off → on → off | `404b` |
| AC output | on → off → on | `404a` |

The Gen 2 public charging-power method also passed 1200 → 300 → 1200 W.
Its AC-output test used a separate model-guarded research `4101`/A2 write,
not a new public HTTP output command. Three final fresh samples confirmed
baseline restoration on both devices; the result recorded no restoration errors.
Not every allowed setting value, C1000X sibling or other firmware was tested.

## Upstream interruption

With original AC off, its measured AC output fell to zero and the upstream
Gen 2 output changed from approximately 180 W to 51 W. The expendable switch
restarted after original AC was restored; output-enable readback preceded
the return of its load.

With Gen 2 AC deliberately off for approximately twelve seconds, four fresh
original-C1000 samples kept AC enabled and reported 50–52 W. Both supplies
were then restored. These samples support continued battery-backed output
at the sampling times; they do not measure millisecond transfer interruption,
waveform quality or a certified UPS transfer time.

The original A5 input-power and BC charging-source fields stayed zero with
and without upstream power. Do not infer missing mains from either. A5 may
report battery charging rather than bypass input; this needs a partially
discharged, actually charging battery test.

## Runtime decoder correction

While AC was connected, the user observed **99.9** on the LCD. Captures included
a larger A4 estimate (`02900d`, raw 3472) and the `02ffff` sentinel. During
upstream interruption, A4 raw values 153–178 corresponded to 15.3–17.8 hours
under the reference's 0.1-hour unit.

The decoder now retains `time_remaining_raw` and marks estimates above 999
as `time_remaining_minutes="unknown"`. This clears an earlier cached numeric
estimate rather than publishing hundreds of thousands of minutes for `ffff`.
The observed estimates were not verified by a full discharge run.

## Server check

A subsequent check ran one `MonitorService` with both physical C1000s and
the authenticated FastAPI routes through an in-process ASGI HTTP client.
Each device changed to 300 W through `POST /devices/{name}/commands`, then
returned to its original 1000/1200 W limit. Responses contained newer telemetry
timestamps, matching limits and AC enabled. Final readback preserved the other
recorded settings. `/devices` returned two available stations without BLE
addresses or serials. This exercised the live BLE/server control path; it did
not run Home Assistant or an original-C1000 end-to-end MQTT broker test.

Both batteries were full. All charging-limit tests establish stored settings
and restoration, not actual charging-power enforcement.

## Retained evidence

Owner-only baselines, notification captures, raw TLVs, scripts, control journal,
server responses and results are retained in ignored
`.solix-private/c1000-chain-20260930-110819/`. The first concurrent-handshake
attempt failed before writing; sequential retries and the later dual-device
service succeeded. Connection failures and successful data were both retained.
An archive and per-file SHA-256 manifest accompany the private evidence.
Archive SHA-256: `ed70d28c56dba3e86a0a47b27b0be67604489ad81d6edf9663e58f56d0bb2faf`.
This report includes only selected settings and observations, without pairing
identities, device addresses, serials or phone data.

See [original C1000 support and remaining work](c1000-original-protocol.md).
