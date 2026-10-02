# Gen 2 clock-screen restoration and scalar display controls

## Result and scope

Offline **A1763 C1000 Gen 2 main 1.1.4.9** audit dated 2026-10-02.
[121 synthetic instruction cases](../tools/firmware_analysis/emulate_gen2_clock_screen_preservation.py)
extend the [clock-screen protocol](gen2-timer-plan-investigation.md) with opaque
configuration preservation, fresh serializer behavior and a reachable
restoration counterexample. No station, Bluetooth, MQTT, SSH, cloud, actual
asset download or display/output hardware was accessed.

**Fresh DA cannot provide lossless A2-only enable restoration.** A2 writes a
hidden first-window enable bit as well as the visible clock/theme flags. DA
does not export that hidden bit. In contrast, brightness selectors and individual
window endpoints have complete readback of their affected fields and preserve
the rest of the 280-byte configuration in the replay. These are concrete lower
risk candidates for later C1000-only validation; no runtime controls were added.

## Why A2 restoration can change hidden state

Native `0091` handler `0800c314` first copies all `0118` hex =280 current bytes
from `20001d8e` into the shared staging buffer at `20002914`. When A2 exists:

1. `0800c348` stores its flags byte at staged offset `10` hex.
2. `0800c34a..0800c352` copies flags bit 7 into staged byte 0, bit 0.
3. The direct commit path copies the entire staging buffer back and requests
   persistence through `0802b39c`.

DA serializer `08018730` exports flags from offset `10` and brightness bit 1
from byte 0. It **omits byte 0, bit 0**. It also omits text and reserved window
bits. Three pairs of distinct configurations therefore have identical DA:
different first-window enable, different text, or different reserved window bits.
The serializer is not a complete configuration backup.

The missing bit is not merely a malformed synthetic possibility. The actual
one-shot schedule callback produces this sequence:

| Step | Flags bit 7 | First-window bit 0 | DA observation |
| --- | ---: | ---: | --- |
| Enabled one-shot window, inside its window | 1 | 1 | Clock enabled |
| Expiry via `0802d2b8` → `0802b36c(0)` | 0 | 1 | Clock disabled |
| A2 enable then restore original flags | 0 | 0 | Identical disabled DA |

Helper `0802b36c` updates only flags bit 7 and requests persistence; it does
not normalize the first-window bit. The replay executes selection at 08:00,
expiry at 17:00, then A2 set/restore. Only the hidden configuration byte differs
afterward, while the exact 24-byte DA value is unchanged. A first-window enable
inferred solely from DA flags can therefore be wrong.

Seventy-two A2 roundtrip cases vary nine raw flags, both hidden first-enable
values, readiness and the system busy bit. All unknown metadata, text, other
window bits and endpoints are preserved. The only restoration mismatch is the
hidden enable bit when its baseline differs from original flags bit 7. There
is no demonstrated independent ordinary writer for that hidden bit, so this
audit does not propose A2 as an exactly reversible experiment.

## A pending asset transfer is a separate blocker

Every `0091` request copies current configuration over shared staging **before
testing optional fields**. Thus avoiding A3/A4 does not protect an existing
pending transaction.

Two cases seed different pending asset metadata and transfer state 1, then send
A2-only or brightness-only. Both overwrite the old staged metadata, start no
new transfer and leave status 1. Executing the actual success callback afterward
commits the replacement staging contents. The physical transfer is excluded;
the concrete finding is the lost pending RAM transaction.

Any later scalar display control should require fresh **transfer state 0**.
State 1, state 2, missing or conflicting status should decline. An ACK and
absence of a newly started transfer are insufficient guards.

## Lower risk scalar candidates

When only one of these fields is sent, A2 is absent, so the hidden first-window
enable is not normalized. All request values below exclude the TLV tag/length:

| Field | Typed value | Stored change | Complete affected DA readback |
| --- | --- | --- | --- |
| `AC`, first-window brightness selector | `01 <0 or 1>` | Byte 0, bit 1 only | Typed DA byte 18 |
| `AD`, second-window brightness selector | `01 <0 or 1>` | Byte 6, bit 1 only | Typed DA byte 19 |
| `A8`, first start | `02 <minutes_LE16>` | Bytes `[2:4]` | Typed DA `[12:14]` |
| `A9`, first end | `02 <minutes_LE16>` | Bytes `[4:6]` | Typed DA `[14:16]` |
| `AE`, second start | `02 <minutes_LE16>` | Bytes `[8:10]` | Typed DA `[20:22]` |
| `AF`, second end | `02 <minutes_LE16>` | Bytes `[10:12]` | Typed DA `[22:24]` |

These are clock/display tags, including hexadecimal tag `AC`; they do not
refer to the station's AC output. Selector zero/one chooses firmware brightness
values 5/20 when that clock window is active. Physical luminance and visible
theme names remain unverified. Minute endpoints follow controller-local RTC;
equal endpoints mean all day and overnight windows have the behavior described
in the preceding audit.

Sixteen brightness and sixteen endpoint roundtrips start with clock enable
bit 7 clear, status 0, opaque text/metadata and nonzero reserved window bits.
They vary hidden first-enable, selector value and readiness. After temporary
set and restore, all 280 clock bytes match exactly; the resource-locator buffer
is unchanged, no transfer starts, and all 415 saved settings bytes outside the
intended field remain unchanged. Output-state bits are unchanged except the
existing LCD activity bit 20, which display helpers can update.

This is preservation evidence for optional-field writes, not a need to reconstruct
opaque assets from DA. Their actual bytes survive because the handler copies
and edits the complete current structure. Concurrent app writes and real timer
tasks were not scheduled by these cases.

## Fresh DA and the alternative query

DA has exactly 24 typed bytes beginning with `04`. Unlike FE, this callback
has no refresh-mode branch retaining old fields. Nine cases execute modes 1,
2 and 3 with transfer status 0, 1 and 2; each rebuilds the same current DA and
leaves all saved configuration, locator, output flags and status unchanged.
The [ordinary status audit](gen2-backup-query-investigation.md) establishes
native `0100` → full serializer mode 1 → DA; a correlated fresh `0900` response
is the existing useful baseline transport.

Native `0092` does not solve the hidden-enable problem. Two new collision cases
return identical replies for opposite hidden first-enable bits. That reply
contains asset metadata, formatting, weekday and optional text, rather than a
raw 280-byte configuration. Failure status 2 is cleared after responding.
Continue using ordinary DA instead of adding this state-changing query.

## Actionable next validation

The first later trial can validate the **first-window brightness selector** on
the noncritical C1000 Gen 2 while its clock screen is disabled:

1. Serialize commands per device. Obtain a fresh explicit `0100`/correlated
   `0900`; require exact DA length/type, clock-enable bit 7 clear and transfer
   state zero. Keep the raw DA value, not only the scalar decoder metrics.
2. Record A4/D9 settings and AC/DC state. Save DA byte 18, which must be zero
   or one. Do not change A2, asset metadata, text, locator or other windows.
3. Send native `0091` with only the usual source/timestamp metadata and
   `AC = 01 <opposite selector>`. This harness tests the controller parser and
   handler; it does not establish a BLE opcode or a new radio delivery route.
4. Require an ACK and fresh full status. Compare the entire raw DA except byte
   18, confirm the intended byte, status zero and unchanged power settings/state.
5. Restore `AC = 01 <original selector>` even if verification fails after a
   delivered write. Verify the complete raw DA baseline and protected settings
   again. Do not call `0092` or clear an asset failure to force the trial.

The disabled-clock trial validates storage/readback without proving visible
brightness. A later render test needs separately established clock configuration
and user observation. Unexpected missing readback, active clock, asset status,
unsupported model/version or concurrent changes should stop the experiment.
No C2000 display or output experiment is authorized by this audit.

## Reproduction and limits

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-clock-screen-preservation \
  python3 tools/firmware_analysis/emulate_gen2_clock_screen_preservation.py
cmp /tmp/gen2-clock-screen-preservation/gen2-clock-screen-preservation-results.json \
  tools/firmware_analysis/expected_results/gen2-clock-screen-preservation-results.json
cmp /tmp/gen2-clock-screen-preservation/gen2-clock-screen-preservation-manifest.json \
  tools/firmware_analysis/expected_results/gen2-clock-screen-preservation-manifest.json
```

Requires the published analysis dependencies and exact main image, 198656 bytes,
loaded at `08005000`, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
`SOLIX_FIRMWARE_DIR` can supply the same image externally.

Cases: three DA collisions, 72 A2 roundtrips, 16 brightness and 16 endpoint
roundtrips, one reachable one-shot counterexample, two pending-stage overwrites,
nine DA mode/status cases and two `0092` hidden-state collisions. Public results
contain synthetic values only. Raw disassembly stays private.

Real parser, optional-field handler, stage/commit copies, display helpers,
schedule/visibility, one-shot callback, asset-success callback and serializers
execute. ROM is read/execute-only; instruction writes stay in synthetic RAM.
ACK/logging/persistence, software timers and normal-display timer query,
allocation and asset-transfer startup are inherited substitutes. RTC, readiness
and qualified input-power state are synthetic. Actual task scheduling, flash,
asset download, app/radio/cloud, display rendering, electrical outputs and
physical protections remain excluded. Other models and firmware are unproved.
