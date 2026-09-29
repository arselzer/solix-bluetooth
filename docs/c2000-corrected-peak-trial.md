# C2000 corrected Peak trial

## Result and scope

On **2026-09-29**, a C2000 Gen 2 / **A1783**, main firmware **2.1.6.4**,
selected an active Peak tariff and supplied its AC load from the battery with
mains still connected. Control used native MQTT on the owner's isolated HA-node
AP, local API, NTP and TLS endpoint. The namespace had **no default route**;
no request was sent to Anker's cloud.

The live version block also reported controller **5.0.7.1**, inverter **5.0.7.0**,
BMS **9.3.3.0** and wireless module **3.3.0.0**. These are station-reported
versions; the recovered C1000 radio's `0.3.3.0` string is separate evidence.

AC output was enabled in every recorded sample. No AC-output switch, output
timer, diagnostic-register or firmware-update command was sent. Telemetry
does not establish waveform continuity or a measured transfer time.

This validates the [corrected schedule encoding](c2000-tou-encoding-audit.md).
Earlier inactive-Peak trials used malformed intervals and do not establish
that cloud activation or another control command is necessary.

## Baseline and guards

| Field | Fresh baseline | Final independent BLE check |
| --- | --- | --- |
| Battery | 91%, idle | 90%, idle |
| AC input / output | Connected / enabled, 937 / 937 W | Connected / enabled, equal power in three samples |
| Usage mode / tariff | Standard / none | Standard / none |
| Schedule count | 0 | 0 |
| Backup reserve | 10% | 10% |
| Upper / lower limits | 90% / 1% | 90% / 1% |
| AC charging limit | 1800 W | 1800 W |
| Fast charge | Off | Off |

Controller `0089` returned readiness prefix `34`. Before each normal write,
the worker checked fresh status, mains presence, AC-output enablement and
protected settings. It required no active AC-output timer and at least 90% SOC.
Restoration ran in `finally`, with bounded retries and a BLE fallback.

## Encoding and sequence

Native command **`0090`**, response **`0890`**, uses `A1=22` and `FD` containing
type `00` plus ASCII Unix milliseconds. The BLE equivalent is `4090`, with
`A1=21`; corrected activation through BLE alone was not tested here.

The values below include each field's type byte, without its TLV tag/length:

| Field | Store while Standard | Activate Peak | Clear while Standard |
| --- | --- | --- | --- |
| A2: usage mode | `0100` | `0101` | `0100` |
| A3 / A4 | `0100` / `0100` | Same | Same |
| A6: slot count | `0101` | `0101` | `0100` |
| A7: binary triplets | `04010018` | `04010018` | `0400` |

`A7` contains the type `04`, then **Peak / start hour 0 / exclusive end hour
24**. It has no embedded count. A3/A4 were retained as zero; their C2000 meaning
was not independently tested.

1. Store one corrected slot while Standard. Fresh `D9` had length **29**,
   count 1 at offset 6 and `01 00 18` at offsets 7–9. Standard/no tariff/idle
   persisted after two seconds. Clear it and confirm the baseline again.
2. Raise only backup reserve **10→85%** using `A5=0155` and confirm it.
3. Write the corrected plan with Time-of-Use mode. Fresh telemetry immediately
   selected **Peak** and reported **0 W AC input / 925 W AC output**, with mains
   connected. The battery-status field initially still said idle.
4. About **4.4 seconds** after the first confirmed Peak snapshot, battery status
   reported discharging. Three consecutive discharge samples showed **851,
   891 and 897 W** AC output, all with 0 W AC input. SOC changed 91→90%.
5. Stop after those three samples, about **8.7 seconds** after first Peak
   confirmation. Restore **Standard plus count 0 first**, then reserve **10%**.
   Fresh settings matched the baseline; no charging-limit write was needed.

The retained native request log contains **27 status reads, one readiness read
and six `0090` writes**. Decoding every outgoing native request confirmed that
the six writes contained only mode, schedule and reserve fields.

## Restoration is not an immediate power-flow guarantee

Fresh status confirmed Standard/no tariff/count 0/reserve 10%, but continued
to report discharging and 0 W AC input. A separate MQTT connection repeated
that observation approximately **3.0, 5.3 and 7.5 seconds** after restoration.

After AP shutdown, a separate BLE connection confirmed battery idle and equal
input/output power in three samples: **1344/1344, 1176/1176 and 1135/1135 W**.
The first was approximately **223 seconds** after settings restoration. There
were no readings between the last MQTT sample and that BLE check: **223 seconds
is an observation bound, not the measured return delay**. Elapsed time, AP
shutdown and the BLE handshake were not isolated as possible influences.
No extra charging, reserve or output command was sent to cause the return.

Automation must confirm actual power flow after restoring mode. A successful
acknowledgment or Standard setting alone is insufficient. A [later packaged
CLI trial](c2000-offpeak-grid-return.md) verified tariff-3 Off-Peak recovery,
then cleared Standard and confirmed fresh grid samples. The CLI/control socket
now exposes guarded plan/reserve/grid operations. Persistent operation, timed
transitions and reserve-floor behavior remain untested.

## Relevant firmware lead

Twelve offline cases in recovered **C1000 Gen 2 1.1.4.9** code reproduce a
possible retention mechanism. With charging-enable bit 6 and DC-charge bit 0
clear at RAM `0x20000164`, the policy branch at `0x08014d86` retains prior
internal mode bits 4–5 when tariff is none or Peak. Mid/Off-Peak selects mode 2
only with both readiness and power gates satisfied. Execution stopped before
timer/DSP activity, with synthetic RAM and RTC; no helper on the selected path
was substituted. This is **C1000 code evidence**, not proof of the C2000 return
mechanism or of an interruption-free Off-Peak recovery.

## Retained artifacts and cleanup

All three deployment attempts, including unsuccessful reconnect/startup
attempts, were archived locally under
`.solix-private/isolated-ap/corrected-tou-20260929-{1955,2008,2012}/`.
The successful archive retains scripts, inputs, manifests, raw BLE notifications,
MQTT/API traffic, packet capture, baseline, trial and both independent checks.
Its SHA-256 is
`35e362310ee1520332a758a0f0c181a689fdc543e9e25f23a209002d475faa76`.

The offline retention script, results and hashes remain in
`.solix-private/firmware-analysis/`. Private directories/files use 0700/0600.
Archives were copied and hash-verified before deleting temporary deployment
credentials on the HA node. Services and namespace were removed; the unused
lab adapter was left DOWN without an address. The station may retain the
isolated Wi-Fi profile while that AP is off.
