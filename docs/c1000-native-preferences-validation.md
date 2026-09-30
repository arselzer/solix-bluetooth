# C1000 Gen 2 native display and port-memory validation

## Device, versions and baseline

On **2026-09-30**, C1000 Gen 2 / **A1763**, main **1.1.4.9** and radio
**0.3.3.0**, completed seven preference writes through the isolated local MQTT
service. No Anker cloud request or C2000 setting change was part of this trial.

The fresh native baseline was brightness **1 / Low**, screen timeout **30 s**,
port memory **on**, Device Timeout **Never**, charging limit **1200 W**, charge
bounds **100% / 1%**, reserve **10%**, Standard mode / no active tariff / zero
slots, and fast charge off. AC output and mains were **on**; DC output was
**off**. A separate BLE baseline agreed before the native trial. Clock-screen
DA was disabled, with its transfer state recorded for the unchanged-state guard.

## Live writes and confirmation

Native command group is `0103`; these typed field values omit the ordinary
transport envelope. A4 offsets include its leading type `04`, excluding the
tag/length bytes.

| Preference | Field | Live sequence | Readback |
| --- | --- | --- | --- |
| Display brightness | A3 = `01` + level byte | **1 → 2 → 3 → 1** | A4[18], `display_brightness` |
| Output port memory | A8 = `01` + boolean byte | **1 → 0 → 1** | A4[23], `port_memory_enabled` |
| Screen timeout | A4 = `02` + unsigned seconds, little endian | **30 → 60 → 30 s** | A4[16:18], `display_timeout_seconds` |

Each of the **seven writes** had a successful response and fresh complete
**A4 + D9** confirmation. AC output, mains and DC output matched the recorded
baseline in every captured check. Charging power, limits, reserve, fast charge,
Device Timeout and tariff configuration stayed unchanged. Runtime LCD activity
at A4[22] was allowed to wake/expire; saved brightness and timeout were checked
separately. Disabled DA clock-screen and transfer state also stayed unchanged.

After restoration, **three fresh final native snapshots** matched the baseline.
The subsequent independent BLE final check was **unavailable: the station was
not advertising**. This trial therefore establishes native readback/restoration,
not an independent final BLE confirmation. It does not measure optical
brightness or electrical continuity between telemetry samples.

Port-memory OFF also clears recovery bookkeeping and cancels recovery timers
in the recovered firmware. Turning it ON restores the saved preference but
does **not** reconstruct that transient state. Matching final A4/D9 bytes is
not a claim that every internal recovery timer was restored.

## Public controls

Only the **C1000 Gen 2 native MQTT** profile exposes these new controls.
C2000 and other models are unchanged; no Gen 2 BLE brightness command is added.
An already running AP worker must have `--allow-control`. Specify `--name`
when multiple stations share its directory:

```sh
sudo /path/to/venv/bin/solix-link ap-service-set-display-brightness \
  --directory /path/to/private-ap --name office --level 2
sudo /path/to/venv/bin/solix-link ap-service-set-display-timeout \
  --directory /path/to/private-ap --name office --seconds 60
sudo /path/to/venv/bin/solix-link ap-service-set-port-memory \
  --directory /path/to/private-ap --name office --enabled off
```

These are explicit examples of writes. Record current values before a temporary
test and restore those values afterward. The tool does not automatically restore
settings on exit or retry failed writes.

Brightness additionally requires fresh **Standard / no active tariff** status
and an inactive clock screen with no transfer in progress. The guarded service
refuses missing or conflicting clock-screen readback before sending it and
checks that clock-screen configuration stays unchanged afterward.

| HTTP command | Required field | Allowed values |
| --- | --- | --- |
| `set-display-brightness` | integer `level` | 1 Low / 2 Medium / 3 High |
| `set-display-timeout` | integer `seconds` | 0 Never, 10, 20, 30, 60, 300, 1800 |
| `set-port-memory` | boolean `enabled` | `true` / `false` |

Use the selected station's advertised capabilities on
`POST /devices/{name}/commands`. Both worker and HTTP gateway must enable
controls; HTTP requires a token. The fixed terminal dashboard, line guide and
browser expose these preferences. HA exposes brightness/screen-timeout selects
and a port-memory configuration switch, subject to fresh valid readback and
capability/token checks; actual HA runtime compatibility remains untested.

For a running `LocalMqttServer`, guarded async methods are
`set_display_brightness(level)`, `set_display_timeout(seconds)` and
`set_port_memory(enabled)`. `NativeMqttCommands.display_brightness`,
`.display_timeout` and `.port_memory` only build requests; they cannot confirm
application. Use the guarded service or RPC rather than treating an ACK as proof.

## Firmware and synthetic evidence

Brightness levels 1/2/3 select internal duty values 20/60/100. **Zero is not a
brightness level**: it reaches display-off behavior while retaining the saved
brightness, so public brightness setters reject it. The remaining timeout
choices above are permitted by firmware/SDK range evidence; only **30/60 s**
were exercised in this live trial.

The [preference audit](gen2-preference-candidates.md) includes **1,060 offline
instruction cases**, with synthetic RAM/timers and substituted transports,
persistence and display delivery. SDK/interface/HA contract and browser tests
use fake stations. These checks support encoding and guards without extending
the physical validation to other firmware, timeout values, displays or models.
Ambient light is a no-op on this image; language enums remain unresolved and
are not exposed as controls.

Raw trial packets, IDs and credentials remain in the ignored private archive.
Result archive SHA-256:
`b04237da1ea8c9f63d1e3d91f31c666fc1ebb68b601c6d5670f002da1068243c`.
