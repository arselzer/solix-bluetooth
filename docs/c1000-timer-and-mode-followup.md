# Original C1000: timer, Smart mode and fast-charge follow-up

Offline investigation dated 2026-10-01, using public **A1761 main 1.5.9**.
The current station has since updated to main **1.7.1**; these instruction
addresses and results are not a hardware validation of that version. No
station, network, cloud, output or flash operation was performed here.

## Output timer expiry can request either direction

Commands `0042` / `0043` take A2 typed `uint32` seconds and operate the AC /
car-output countdown respectively. Their legacy BLE equivalents are `4042` /
`4043`; native framing uses the `00xx` form. Both end in the common
[conditionally suppressed ACK path](c1000-bypass-firmware-followup.md#writes-can-succeed-without-a-command-ack).

| Item | AC | Car/DC |
|---|---|---|
| Handler | `0800b990` | `0800ba88` |
| Remaining seconds | `200020b0` | `200020b8` |
| Previous display/report checkpoint | `200020b4` | `200020bc` |
| Enabled bit in `200020c4` | 0 | 1 |
| Cached output switch | `20000d0b` | `20000d0c` |
| Expiry dispatch | `08012588` | `08013da4` |
| Typed status | A2, `03 + u32le` | A3, `03 + u32le` |

Nonzero writes arm the corresponding countdown; zero clears its enabled bit,
remaining time and display counter. Zero cancellation leaves the previous
checkpoint value in RAM. That inactive checkpoint is not a remaining timer.
The handlers do not request persistent configuration storage.

The common countdown routine **`0800a73c` toggles the cached output switch at
expiry**: 1 becomes 0, while 0 becomes 1, then dispatches the corresponding
output request. It is not exclusively an auto-off feature. The physical output
handler and hardware can impose further conditions; replay captures the
request without executing it.

### Countdown uses the clock's time of day

Getter `080175dc` reads the RTC through `08023e18`, runs calendar conversion,
and calculates `hour*3600 + minute*60 + second`. The countdown subtracts the
difference from the previous time-of-day sample. It does not use a monotonic
duration timer:

| Clock change with 300 seconds remaining | Replayed result |
|---|---|
| Advance 1 second | 299 seconds |
| Delayed poll after 100 seconds | 200 seconds |
| Advance exactly 300 seconds | Expiry request |
| Jump forward one hour | Immediate expiry request |
| Jump backward one hour | No decrement at that sample |
| Midnight, or a poll skipping midnight | No decrement across the backward time-of-day transition |
| Same time on the next day | No decrement at that sample |

A2/A3 are current remaining time, not the original requested duration. The
replay follows the real getter/store path at `08010b7a` and serializer prefix
at `08009164` to establish that readback. The zero-at-midnight special case
also resets both previous-sample references.

These timers mask the respective Smart inactivity condition while enabled;
see the [existing Smart policy analysis](c1000-smart-auto-off-policy.md).
They do not select a charging source or preserve an AC output indefinitely.

## Complete F8 and Smart restoration

The actual serializer `080093f0..08009448` constructs a 21-byte F8 value:

```text
04  DC_mode  AC_mode  01 01  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

`04` is the structured-value type. DC/AC mode bytes are stored values
1=Normal, 2=Smart. In this image the next two bytes are literal 1 and the
remaining sixteen are literal zero; no additional setting meaning is assigned
to them. Command `0076` / `0077` takes A2 typed byte **0=Normal, 1=Smart**,
changes only its own saved mode byte and requests configuration persistence.

Eight complete round trips execute both mode handlers and the full F8
serializer. Only the selected mode byte changes, and all 24 bytes of the
surrounding saved-settings block are restored. These commands do not accept
or require a replacement packed F8 value.

For live checks on another version, preserve raw F8 byte-for-byte except the
one expected mode byte; after restoration compare the **whole original raw
value**, including type, length and unknown tail. Do not replace an unfamiliar
tail with the constants emitted by 1.5.9. Older observed main 1.5.1 uses the
short `01 DC AC` form; decoding must remain format-specific.

Smart mode is still subject to the inherited inactivity counter described in
the earlier investigation. Enabling Smart does not guarantee a fresh grace
period before output shutdown.

## Fast charge changes the allowance, not the charging-input gate

`005e` / `0800bd00` normalizes A2 to a boolean and writes bit 2 of `200020c4`.
There is no persistence request in that setter. The separate saved charging
ceiling remains unchanged.

The normal charging-policy block at `08014304..08014338` uses the exact double
constant **0.91** at `08014514`:

| Mode | Internal power allowance in this block |
|---|---|
| Normal | Integer conversion of saved ceiling × 0.91 |
| Fast, variant byte 0 | 1400 |
| Fast, other tested variant bytes | 1000 |

The replay verifies these values for saved ceilings 100, 500 and 1000 and
variant bytes 0, 1 and 2. These are internal policy values, not measured mains
power; later current/temperature/DSP limits still apply.

The periodic policy clears Fast when bit 0 of `20000690` is absent. Producer
`08021044` sets that bit on its insertion branch and clears it on its removal
branch; the latter schedules the logged `portAcOut` event, whose tail at
`08007282` also clears Fast. This is evidence of an AC-input presence/removal
path, not an AC-output-button instruction. Timer enable bits and AC output
bits remain unchanged in the replayed clearing blocks.

Turning Fast off can reduce a previously enabled boost to the saved normal
ceiling. It does not disable AC charging or request battery-only operation.
No additional external input-disable or bypass selector was established here.

## Implications for further live validation

A subsequent [main 1.7.1 DC Smart trial](c1000-prime-dc-smart-validation.md)
passed over Prime Bluetooth with DC output off, including a public-SDK repeat
and complete F8 restoration. That result is separate from the 73 offline
1.5.9 cases and does not validate timer expiry, Fast or AC Smart on 1.7.1.

- A DC Smart round trip while the car/DC output stays off is the least
  disruptive additional mode check. Require fresh full F8, unchanged outputs
  and charging settings, and complete F8 restoration.
- AC Smart needs the existing low-load/counter precautions. A successful
  setting response does not establish a new inactivity interval.
- A Fast trial can raise the charging allowance. Confirm the physical input
  setup and stable E5 readback, then restore the original value. E5 clearing
  itself is not a successful retained setting; do not keep re-enabling it.
- A timer trial requires an initially disabled timer, ample duration, stable
  clock, prompt zero cancellation, and fresh A2/A3 plus output confirmation.
  Expiry and transport loss must be treated as possible output changes.
  Restoring a saved nonzero remaining value would extend its deadline by the
  time spent testing; it is not an exact restoration.

The remaining items are prerequisites for a future trial, not authorization to touch another
station. No C2000 test follows from this analysis.

## Reproduce

```sh
python3 tools/firmware_analysis/emulate_original_timers_modes.py \
  --image firmware/c1000_original/1.5.9/MainMcu-decoded.bin \
  --output /tmp/original-timers-modes.json
cmp /tmp/original-timers-modes.json \
  tools/firmware_analysis/expected_results/original-timers-modes-results.json
```

Requires Unicorn. **73 new synthetic cases pass**: 36 clock/expiry/readback,
six cancellation, eight full F8 round trips, eighteen charging-allowance and
five Fast-clearing cases. Actual calendar, countdown, getter, serializer and
selected policy instructions run; RTC registers and elapsed time are synthetic.
Output dispatch, persistence, ACK and logging are substitutes. No electrical
waveform or whole-device persistence is simulated.

Input SHA-256 is checked against
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
The output records the script hash. Published expected results and a dependency
hash manifest contain only synthetic data; private address excerpts are
retained separately. This suite adds evidence without repeating the earlier
87 power-path or 48 ACK cases.
