# Gen 2 command 0091: clock screen and display schedule

## What the command actually controls

The previously unidentified “timer/clock plan” is a **clock-screen/theme
configuration**, including two display time windows. It is not evidence of
another charging scheduler. Recovered C1000 Gen 2 main **1.1.4.9** strings
identify `set Clock Theme`, `Time Screen Tmr Start` and `Screen update success`.
The command's downstream functions write LCD state, clock visibility and
brightness; the asset-update branch starts a separate resource transfer.

This September 30, 2026 investigation used published firmware and retained
local telemetry. **60 synthetic actual ARM instruction cases** and **11 Python
decoder tests** pass. No station, SSH, cloud or other network request was made.
Rendering on a physical display, app labels, and C2000 compatibility remain
unverified. See the [earlier candidate list](firmware-findings.md).

## Payload and retained state

Main dispatch entry `08032ed8` maps application command `0091` to handler
`0800c314`. Fields are optional updates to the existing 280-byte settings
structure at `20001d8e`; changes are first assembled at `20002914`.

The table shows the conventional typed value, excluding the TLV tag and
length. The handler skips the type byte but does not comprehensively validate
types, lengths or ranges. These are offline schema findings, not a public
write API.

| Tag | Typed value | Stored offset / effect |
| --- | --- | --- |
| A2 | `01 <flags>` | `+10`: bit 7 enables clock screen; low nibble selects theme. Bit 7 also becomes first-window enable at `+00.0`. |
| A3 | `04 <4 opaque bytes>` | `+11`: asset metadata; a change may trigger resource transfer. Exact meaning unknown. |
| A4 | `03 <uint32 LE>` | `+0c`: second asset metadata field; a change may trigger transfer. Not established as a timestamp. |
| A5 | `00 <text>` | `+17`: text buffer cleared before copying; exact app meaning unknown. |
| A6 | `00 <resource locator>` | Separate buffer at `200027fa`; used by asset-transfer startup. No transfer occurs from A6 alone. |
| A7 | `01 <value>` | `+15`: normalized to zero/one and forwarded into LCD formatting. Exact user-facing meaning unknown. |
| A8 / A9 | `02 <uint16 LE>` | `+02` / `+04`: first start/end, minutes since local midnight. |
| AA | `01 <mask>` | `+16`: Monday-first weekday bits 0–6; zero has one-shot behavior. |
| AB | `01 <flag>` | `+06.0`: second window enabled; only the low bit is used. |
| AC / AD | `01 <flag>` | `+00.1` / `+06.1`: first/second brightness selector, low bit only. |
| AE / AF | `02 <uint16 LE>` | `+08` / `+0a`: second start/end, minutes since local midnight. |

Offsets above are hexadecimal. The name of A6's string field is inferred from
its transfer use; neither a valid locator syntax nor a supported asset format
has been established. No resource is fetched by the replay.

Theme values 0–2 reach the LCD theme field directly. Value 3 selects a
separate LCD flag and sends theme 0; other low-nibble values are clamped to 0
downstream. These internal mappings do not identify the visible designs.

## Window matching and timer behavior

Evaluator `0801a808` reads **controller-local RTC**, converts it to minute of
day and weekday, then checks window 1 before window 2. A match returns LCD
brightness **5** when its brightness flag is zero or **20** when one.
These are firmware values, not calibrated physical brightness measurements.

- Normal windows use `start <= minute < end`.
- Overnight windows work directly. The after-midnight portion checks the
  **previous day's** weekday bit.
- Equal start and end means all day on an eligible weekday.
- With overlapping windows, the first matching enabled window wins.
- Weekday mask zero matches without a day filter and marks one-shot state.
  On expiry, the timer clears clock-enable bit 7, persists settings and stops
  its timer. It does not mean “disabled” or “every day forever.”

Visibility helper `08018848` also requires clock-enable bit 7, qualified AC
input gate `200004be.0`, and an RTC year **later than 2024**. A running normal
display timer can defer entry into the clock screen. The selector alone
does not check all these visibility conditions.

The clock-screen timer callback is `0802d2b8`. Static initialization at
`0800e458` registers it through the repeating-timer helper `080107f4` with
period **20,000**, nominally 20 seconds under the
[recovered millisecond timer clock](gen2-energy-counter-investigation.md).
This audit invokes callbacks directly; it does not measure live scheduling
latency. A pending or failed asset-transfer status suppresses callback display
updates until that status is resolved.

As with tariffs, schedule times follow the effective local RTC, not necessarily
the timezone written in a service profile. See the
[offset-retention audit](gen2-schedule-clock-audit.md).

## Asset updates, ACKs and protected state

Changing the four bytes at `+11` or the integer at `+0c` while network readiness
is true enters an asynchronous resource-update path. Routine `0802c264` is
identified by `ftp start thread`; this audit substitutes that boundary.

The full configuration remains staged until callback `08029e18` reports
success. Success commits it and refreshes the display; failure leaves the old
configuration and sets status 2. The handler still acknowledges the original
request. If system bit 23 is set, the handler can acknowledge without starting
the transfer or committing the changed settings. If transfer startup rejects
the request, the handler nevertheless marks status 1. Offline/unready handling
instead commits the staged fields directly. These are reasons to preserve
asset metadata and verify fresh telemetry after any eventual control test.

**An A2-only update leaves asset metadata unchanged and avoids that branch.**
In the replay it changes clock enable, first-window enable, LCD state and timer
operations. Its display update can change system bit 20. Every case checks that
other system bits, including the synthetic enabled-output baseline, and the
separate charge-power, charge limits, reserve and Time-of-Use settings are
unchanged. ACK transport, timer operations, persistence backend and actual
hardware are substituted, so this is bounded software evidence rather than a
live safety guarantee. No C2000 write is proposed.

## Telemetry and readback

The main telemetry table registers `DA`, binary type `04`, to serializer
`08018730`. Its typed value has **24 bytes**:

| Typed DA offset | Meaning |
| --- | --- |
| 0 | Binary type `04` |
| 1 / 2 | A2 flags / resource-transfer state (`0`, `1` pending, `2` failed) |
| 3–6 / 7–10 | Opaque A3 / A4 asset metadata |
| 11 | A7 normalized formatting flag |
| 12–13 / 14–15 | First start / end minutes, LE16 |
| 16 / 17 | Weekday mask / second-window enable |
| 18 / 19 | First / second brightness flags |
| 20–21 / 22–23 | Second start / end minutes, LE16 |

Retained native C1000 telemetry already contains this block in all **561**
status frames inspected. There is one unchanged baseline: clock disabled,
transfer status 0, formatting flag 1, first window **08:00–23:00**, weekday
mask `7f`, second window disabled, and both brightness flags zero. No private
asset metadata is reproduced here.

The optional read-only helper
[`decode_clock_screen`](../python/solix_link/clock_screen.py) accepts only
`Model.C1000_GEN2`, requires the exact type/length and emits scalar
`clock_screen_*` metrics. It omits asset metadata and keeps uncertain fields
explicitly raw. It has no device-control path.

Command `0092` uses a different reply layout: ACK byte, A1 readiness, A2 typed
16-byte metadata, and optional A3 text. Its handler `0800b644` **clears failure
status 2 after responding**. DA serialization does not have that side effect;
routine telemetry is the preferable existing source for passive observation.

## Reproduction and next work

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_timer_plan.py \
  --firmware-dir firmware/c1000_gen2/1.1.4.9 \
  --output /tmp/gen2-clock-screen.json
PYTHONPATH=python python3 -m pytest python/tests/test_clock_screen.py -q
```

The emulator verifies the published image hash and records hashes of its own
source and supporting modules. Actual instructions execute for TLV parsing,
the handler, copies/comparisons, settings storage, RTC conversion, window
evaluation, visibility, LCD state, timer callback and both readback formats.
Allocation, logging, persistence, replies, display-timer query, timer
operations and asset-transfer startup are bounded substitutes. These 60 cases
are separate from the combined firmware-replay total.

Useful next evidence would be a C1000 app clock-screen capture identifying
the visible theme names and A7 formatting option, followed by a reversible
A2-only display experiment with full baseline and readback. Asset downloads,
malformed field lengths and C2000 writes are outside the established behavior.
