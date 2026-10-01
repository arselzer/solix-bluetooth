# Original C1000 Prime: DC Smart validation

Live validation on 2026-10-01 used **A1761 main 1.7.1**, radio **0.3.3.0**,
Prime/AES-GCM Bluetooth and the existing paired client ID. The original C1000
powered an expendable switch, with AC output on and car/DC output off. No
C2000 command was sent. Generated-ID pairing is still a separate open question.

## Command and result

`SolixMonitor.set_dc_power_saving_enabled(False)` selected Normal using `4076`,
A2=`01 00`; `True` restored Smart with A2=`01 01`. Both were confirmed by fresh
full status. In the complete 21-byte F8, only byte 1 changed: **2 → 1 → 2**.
Every other byte, including the unknown tail, remained unchanged.

A private prototype and a second trial using the public SDK each sent two
setting writes and recorded nine snapshots: baseline, two Normal confirmations,
a Normal hold, two Smart confirmations and three final baseline confirmations.
All 11 protected configuration fields and the whole original F8 were restored.
AC stayed enabled and DC stayed off in every sample. Raw notifications,
session material and detailed timestamps remain in the ignored private folder.

## Production behavior

The CLI command is `set-dc-power-saving --enabled on|off`; the SDK, gateway
and interactive interfaces use the same boolean operation. Prime requires a
**fresh DC-output-off reading before either direction**, rather than trusting
cached state. An active DC output is rejected before a write. Successful
confirmation requires fresh protected settings and complete F8, with exactly
the intended mode-byte change. Missing, reverted or changed protected data
fails the operation; the writer does not retry a setting automatically.

This adds a seventh verified **BLE Prime** preference. A subsequent
[independent native MQTT trial](c1000-native-dc-smart-validation.md) also
confirmed DC Smart, bringing that separate whitelist to seven. This Bluetooth
trial itself supplies no native transport evidence. Legacy 1.5.1 support
remains separate.

The test establishes mode storage/readback with DC off, not low-load shutdown
timing or behavior with DC powered. The [1.5.9 firmware follow-up](c1000-timer-and-mode-followup.md)
shows that Smart can inherit an inactivity counter; enabling it does not
promise a fresh grace period. AC Smart and output timers remain excluded from
Prime controls until separately validated.
