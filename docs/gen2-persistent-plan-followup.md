# Gen 2: persisted plans and complete backup readback

Offline follow-up, 2026-10-01, for **C1000 Gen 2 main 1.1.4.9**. This work
adds reproducible readback counterexamples and narrows cancellation behavior.
No device or network operations were performed. No C2000 behavior is inferred.

## What has already been validated live

The [charging and reserve record](c1000-charging-and-reserve-validation.md)
already covers a 95% reserve-floor transition and 60-second hold, 100/200 W
charging below full, cached-profile AP recovery, and the natural Vienna
18:00 Peak-to-Off-Peak boundary. These do not need to be repeated simply to
establish basic charging control.

| Remaining question | Evidence needed |
|---|---|
| Nonempty tariff/reserve persistence across radio reconnect | Complete fresh D9 plan/reserve readback before and after reconnect; observe resumed policy without resending activation. Existing AP recovery used Standard mode. |
| Controller behavior across local server restart | Restart only the local service, distinguish retained controller plan from startup commands or profile replay, then read fresh status. |
| Long-term Never/offline availability | Extended observation with recorded load and radio conditions. Minutes under load do not establish days while idle. |
| Backup-preparation restoration | Complete saved manual record, all three automatic records, both switches, and active state; D9 alone cannot supply this. |

Full device restart is a separate experiment from radio/server reconnect.
The existing firmware zero-offset clock hazard also means the clock profile
must be checked before interpreting scheduled-boundary results.

## Identical D9, different saved configurations

`emulate_backup_readback.py` executes the existing actual-ARM selector and D9
builder. Six paired counterexamples produce **byte-identical D9 status** from
different 38-byte saved configurations:

- Empty automatic records versus an enabled future automatic window.
- Different future automatic windows stored in different slots.
- Manual stored maximum 60 versus 100, with both active and dormant windows.
- Automatic stored maximum 60 versus 100, with both active and dormant windows.

The D9 builder at `080190d4` includes manual start/end and the currently
selected window, but omits all stored maximum bytes and dormant automatic
records. Getter `0801a608` returns full records internally; the inspected
callers are cancellation, active selection and this partial serializer.
This inspection has not established an external full-record read operation.

Consequently, even a fresh “no active backup” status cannot prove that there
are no saved automatic records. A reported active maximum from another source
would also be insufficient to recover dormant records.

## Cancellation: current time matters

The cancellation helper `080093f4` compares saved windows with current UTC
time. Its test is **`start <= now < end`**, rather than comparing one window's
interval with another. Ten additional actual-handler cases establish:

- Manual `005e`, A4=`0`, A5=`0` disables the manual switch and invalidates
  automatic records covering **now**, even when their automatic switch is off.
- Future and expired automatic records survive that operation.
- Exact start is included; exact end is excluded. The active selector uses an
  inclusive end, so selection and cancellation differ at that instant.
- The manual record itself survives this switch-only disable.

The disabled-automatic case is particularly relevant: status is inactive, yet
the manual disable can replace a hidden record with `64ffffffffffffffff`.
Descriptions of “overlapping” cancellation in the
[earlier investigation](gen2-disaster-plan-investigation.md) should be read
as windows covering the current time, not arbitrary interval overlap.

## Requirement for a reversible backup trial

Save each nine-byte record (`maximum`, `start_u32le`, `end_u32le`) for manual
and all three automatic slots, plus both switches, before a write. Enabling a
new manual window can change active charging policy, including its power
ceiling; turning it off is not a universal inverse. Automatic writes can
replace all three slots, and clear-all is destructive.

Until complete original records are known, continue passive status collection
and offline replay. The existing SDK's inactive-backup guard remains useful
for tariff control, but it does not authorize a restore that modifies backup
records. All of these conclusions are firmware-specific.

## Reproduce

```sh
python tools/firmware_analysis/emulate_backup_readback.py \
  --firmware-dir firmware/c1000_gen2/1.1.4.9 \
  --output /tmp/backup-readback-results.json
```

Requires Unicorn. **16 cases pass**; input SHA-256 is checked against
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
Output includes only synthetic plans and hashes. Inherited calendar, hardware,
transport and persistence substitutes remain; no flash persistence or
electrical behavior is tested.

## Separate original-C1000 reliability result

The original C1000 **main 1.7.1/radio 0.3.3.0** held Never for 30 seconds and
reconnected after a local server restart in 2.256 seconds without Bluetooth
activation. All protected settings were restored. This is a separate original
model result, not Gen 2 nonempty-plan persistence or a long-term availability
claim. See [the original native record](c1000-original-mqtt-followup.md).
