# Gen 2 preference candidates

## Scope and evidence

This audit uses the bundled **C1000 Gen 2 / A1763 main 1.1.4.9** image,
SHA-256 `21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`,
loaded at `08005000`. It does not establish behavior on C2000 Gen 2, C300,
or original C1000 firmware. No station command, network request or hardware
access was performed for this audit.

**Later live validation:** brightness **1→2→3→1**, port memory **1→0→1** and
screen timeout **30→60→30 s** subsequently passed native C1000 Gen 2
readback/restoration and are now exposed through the guarded tool interfaces.
That trial's final independent BLE check was unavailable. See the
[separate live report](c1000-native-preferences-validation.md); the findings
below describe the preceding offline investigation.

The [preference replay](../tools/firmware_analysis/emulate_preference_candidates.py)
extends the existing instruction replay without modifying its suites. Real
parsing, handlers, setters, getters, A4 serialization, brightness duty lookup,
and copying of language into LCD state execute. Persistence, transports, LCD
event delivery and software timer boundaries retain the documented substitutes.
Neither LCD hardware nor asynchronous output shutdown policy is simulated.

All A4 offsets below address the **TLV value including its leading type `04`**,
excluding the tag and length. Full A4 is 34 bytes. Native group `0103` corresponds
to BLE `4103`; use the established transport-specific A1 and timestamp rather
than copying emulator envelopes into a live session.

## Brightness: a bounded candidate for live validation

| Item | Exact evidence |
| --- | --- |
| Field | `0103/A3`, typed value `01` followed by one level byte |
| Levels | `1` low, `2` medium, `3` high |
| Setter | `0802b490`; persistent byte `20001d6c` |
| Readback | Getter `0801a648`, A4[18], SDK metric `display_brightness` |
| Duty lookup | `0801a55c`; table at `080316c0` is `10,20,60,100`; index is saved byte masked with `3` |

Levels 1/2/3 select internal duty values 20/60/100. Those are firmware lookup
values, not measured luminance. **Zero is not a fourth brightness level:** it
calls the display-off boundary and retains the previous saved brightness.
Nonzero writes request persistence and a display event, including an unchanged
value. Raw invalid values such as 4 and 255 are stored and alias the duty table;
the replayed settings validator does not reject them. A public setter must
validate 1/2/3 before sending.

This setting is decoded but not exposed as a public SDK, CLI, browser or HA
control at the start of this audit. A guarded live trial can save a known 1/2/3
baseline, change one level, obtain fresh full A4 and D9 samples, then restore the
saved level. Preserve charging power, limits, reserve, modes, plans, timeouts,
temperature unit, alert, port memory and AC/DC output state. ACK alone does not
confirm the setting. A4[22] is runtime display-timer activity and may wake or
expire; protect saved brightness and timeout separately. Never restore a
display-off baseline by inventing a brightness value of zero.

Use normal/Standard operation without an active tariff for the initial trial.
The existing replay also found an unresolved special-mode callback through
`08011730` when RAM `2000039a` bit 1 is set. This is a reason to exclude that
mode from the first test, not a reason to change internal RAM on a station.

## Ambient light: acknowledged but unimplemented

The handler looks up `0103/A7`, reads the byte after its type, logs
`Ambient Light`, and calls **`0802b318`, which consists of `bx lr`**. Every
byte value leaves the replayed persistent settings and A4 unchanged, with no
persistence request or LCD boundary call. The common handler still sends an
ACK and schedules a `0421` refresh; an already active common display timer can
also mark its display event.

Consequently, neither a generic app action name nor a successful ACK establishes
an ambient-light feature on this firmware. There is no justified ambient enum,
getter, telemetry mapping or public control for this model from these findings.
Do not send speculative ambient-light writes to C2000.

## Language: raw storage exists; enum remains unresolved

`0103/A9` accepts a typed one-byte value. Setter **`0802b4f4`** stores it at
**`20001ea7`** (`SETTINGS + 0x15f`) and requests persistence. Getter
**`0801a654`** returns it as **A4[26]**. All 256 synthetic byte values round-trip
unchanged and pass the replayed settings validator. These are accepted raw
bytes, not 256 supported languages.

LCD refresh code at `08024bf0` calls that getter and **`08023a50`**, which copies
the byte into LCD state **`20004383`**. The audit proves this copy only; it does
not execute the display controller or identify visible text changes.

Two default-selection paths (`08029b44` and `0802ab90`) choose codes **0, 4, 1**
for product/region option values **0, 1, 2** respectively. Language names and the
complete valid enum are not established. The current SDK does not decode this
field as a language metric or expose a setter. Retain the numeric mapping for
later app/LCD analysis; do not add a guessed selector or perform arbitrary live
language changes. A later test needs an app-derived enum, supported display,
saved raw baseline, fresh A4[26] confirmation and restoration.

## Other missing controller preferences

| Candidate | Command and readback | Readiness for implementation |
| --- | --- | --- |
| Gen 2 Smart AC output | `0101/A6 = 01 + 0/1`; setter `0802b30c`, byte `20001d67`, A4[8] | Exact storage/readback; asynchronous low-load policy may switch AC off |
| Gen 2 Smart DC output | `0102/A4 = 01 + 0/1`; setter `0802b35c`, byte `20001d68`, A4[13] | Exact storage/readback; asynchronous low-load policy may switch DC off |
| Output port memory | `0103/A8 = 01 + 0/1`, A4[23] | OFF clears recovery bookkeeping and cancels two timers; restoring ON does not restore that transient state |
| AC frequency | `0101/A5 = 01 + byte`; setter `0802b2d8`, A4[7] | Loader accepts 50/60; changing inverter frequency is outside a benign preference test |

The Smart setters store semantic **0 off / 1 on** directly on this Gen 2 image;
do not reuse original-C1000 wire/status conventions. Their eight synthetic
storage cases preserve output flags during the handler only. They do **not**
prove that outputs remain enabled afterward. Existing AC and DC consumer paths
at `080134fc` and `08014a08` inspect low load and those saved settings. A future
control requires explicit disclosure of automatic output shutdown, a noncritical
test load, telemetry confirmation and restoration. These are not charging or
grid/battery selection controls and must not be exercised on the server-backed
C2000 from C1000-only evidence.

Port-memory effects are already covered in the
[general-setting investigation](c1000-general-settings.md). Disaster-preparation
group `005e` still needs a complete plan and activation audit. Timer group
`0091` is a separate investigation and was not exercised here.

## Reproduction

Install the existing [analysis requirements](../tools/firmware_analysis/requirements.txt)
and follow the [firmware reproduction guide](firmware-analysis-reproduction.md).
The bundled image is hash checked; `SOLIX_FIRMWARE_DIR` can override its directory.
Assertions are required: do not run with `python -O` or `PYTHONOPTIMIZE`.

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/solix-preference-replays \
  python3 tools/firmware_analysis/emulate_preference_candidates.py
cmp /tmp/solix-preference-replays/preference-candidates-results.json \
  tools/firmware_analysis/expected_results/preference-candidates-results.json
```

The independent replay covers **1,060 synthetic cases**: 512 ambient byte/timer
combinations, 512 language byte/timer combinations, 24 brightness combinations,
four exact settings/A4 restorations and eight Smart AC/DC storage cases. It is
not part of the existing combined replay count. Published results contain only
synthetic settings, code addresses and firmware provenance.
