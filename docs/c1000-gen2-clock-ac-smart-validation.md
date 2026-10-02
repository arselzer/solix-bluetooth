# C1000 Gen 2 native clock brightness and AC Smart

Live trial on **2026-10-02**, A1763 main **1.1.4.9**, radio **0.3.3.0**,
using the deployed local AP/gateway and HA **2026.7.4**. Baseline: 100% SOC,
AC on, DC off, Standard/no tariff, clock disabled, transfer idle and both
output countdowns zero. The original C1000 and C2000 AC outputs stayed on.

## Clock windows

Each window's saved brightness flag passed **Normal → High → Normal**.
Native `0091` carried only source A1=`22`, typed `AC` (first) or `AD` (second)
value `01 <0|1>`, and the FD millisecond timestamp. It omitted clock-enable/theme
A2, window endpoints and asset fields. The response is `0891`.

The worker requires complete 24-byte DA, exact supported firmware, disabled
clock, idle transfer, binary saved flags, Standard mode and inactive countdowns.
After ACK it confirms two fresh status responses. Only the requested DA byte
may change; the peer window and other DA bytes, A4/D9 configuration, mains and
outputs are protected. A4 display-activity byte 22 may vary within its Boolean
domain. Failed confirmation sends no retry or automatic rollback.

Both controls appear in the fixed terminal UI, line menu, CLI, HTTP/browser
and optional HA configuration selects. Actual HA registered both selects
disabled by default. The live writes used the native SDK; forty synthetic HA
cases cover the select-service mapping and safety gates.

The clock remained disabled. This confirms saved flags, not physical brightness,
clock schedules, visible content, hidden staging state or reboot persistence.
The [121-case preservation audit](gen2-clock-screen-preservation.md) explains
why clock enable/theme/asset writes remain excluded.

## AC Smart and output setup

Only the noncritical C1000 Gen 2 AC output was temporarily switched off.
AC Smart then passed **OFF → ON → OFF**, followed by AC-output restoration.
Native `0101`/A6 changed the saved Smart flag; output setup/restoration used
separate `0101`/A2 requests. Both have response `0901`.

AC Smart requires fresh AC off, known 0/1 flag, main 1.1.4.9 and both timers
zero. It protects full A4/D9, mains and outputs through two new reports; only
A4 byte 8 may change, apart from allowed display activity. The private output
setter independently checks firmware/timers and protects the same settings.
C2000 is rejected before I/O. Output switching is excluded from HTTP/HA and
the generic command schema; only the private operator SDK/CLI exposes it.

AC Smart is a saved inactivity policy. Enabling it does not guarantee a new
grace period and may later shut AC off at low load. This trial does not prove
automatic shutoff timing, relay timing or electrical continuity.

## Retained evidence and next boundaries

The bounded trial lasted **12.41 seconds**. Independent MQTT decoding counted
four `0091` writes and four `0101` writes: two Smart changes and two output
changes. There were 26 Gen 2 status requests, and 30 full DA reports including
periodic telemetry. First/final complete DA blocks matched. All recorded
charging, output, timeout, preference, reserve and mode baselines across the
three stations were restored. The original/C2000 received only two status
requests each during this interval; neither received a setting write.

Raw logs, identities, snapshots, complete trial results and HA draft stay in
restricted private evidence. [92 instruction replays](gen2-native-output-readiness.md)
matched independently: an off output clears a positive timer during its normal
off task. An eventual countdown trial therefore needs an expendable output-on
load, ample duration and early cancellation. No live timer was written here.
