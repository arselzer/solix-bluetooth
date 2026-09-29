# Isolated Wi-Fi and native MQTT tool

The Python package can run the local AP, device API emulator, NTP server and
native MQTT TLS endpoint previously used by the investigation scripts. This
path is experimental and implemented for **C2000 Gen 2 / A1783**. C1000 Gen 2
Wi-Fi joining is verified separately; its native MQTT bootstrap is not verified.
The existing `mqtt-bridge` command remains a BLE-to-broker bridge.

## Requirements and isolation

Use a Linux host with `ip`, `iw`, `hostapd`, `dnsmasq`, and a dedicated Wi-Fi
adapter supporting AP mode. The adapter must be administratively DOWN, have
no addresses, and match the configured phy. An active adapter or existing lab
namespace is refused. `lab-run` needs root to move the adapter into a network
namespace and bind the device's HTTP, MQTT and NTP ports.

The namespace contains only loopback and the AP interface. It has no default
route or connection to the host network; host routes and firewall rules are
unchanged. DNS answers only the configured local broker and `time.nist.gov`;
dnsmasq has no upstream resolver and retains its normal privilege drop. No
request is forwarded to Anker. Local binding responses emulate the device API;
they do not perform an Anker cloud-account binding.

## CLI setup

Install `[tui]` and run `solix-link` without arguments for the full-screen
terminal dashboard. It scans and merges nearby/saved stations, lets you save
new devices, and explicitly connect for BLE monitoring. Prime stations needing
pairing receive instructions to use `pair` or `interactive` first. Add
`--lab-directory` to `solix-link tui` to select an already-running native lab.
The dashboard does not start/stop an AP or change settings on exit.

`solix-link interactive` retains the guided setup workflow. It scans nearby
stations, combines them with saved devices, and guides pairing, BLE monitoring,
broker bridging and C2000 local-MQTT setup. Local setup can read the serial
over BLE and lists Wi-Fi adapters that are DOWN. Monitoring is the first control
choice. Starting an AP requires root; nonroot mode prints the exact command.
An AP started by the interactive session runs in a child process and is stopped
when that session ends. Scripted commands remain available below.

Install the Python package; add `[server]` for HTTP monitoring. First pair the
C2000 through the existing `pair` command and save its timezone. Put its serial
in an owner-only file; obtain it from retained local telemetry or the label.

```bash
chmod 600 .solix-private/device-serial
solix-link lab-init --name ups --serial-file .solix-private/device-serial \
  --directory .solix-private/local-mqtt --interface wlan_lab --phy phy1 --country AT
sudo /path/to/venv/bin/solix-link lab-run \
  --directory "$PWD/.solix-private/local-mqtt" --provision
```

`lab-init` generates a random WPA2 SSID/passphrase, local CA, server/client
certificates and serial-wrapped MQTT credential response. It refuses to
overwrite an existing directory. The account ID defaults to the saved BLE
client ID; `--account-id-file` can supply an existing app account ID without
putting it in shell history. Live bootstrap tests used the retained app ID;
the minimum account-ID requirements still need a separate test.

The client certificate and private key fields use the observed serial-derived
envelope; the root CA field is plain PEM. This wrapping is not secrecy from
someone who knows the serial. TLS requires the generated client certificate.

`--provision` sends `4024`/`4025` over the existing BLE pairing, after checking
fresh telemetry and the serial. It changes Wi-Fi/API/timezone configuration,
and leaves that local configuration saved when the AP stops. No power setting
is written. A `4825` timeout does not prove failure; successful native status
is the connection check. Subsequent runs may omit `--provision`. Radio retries
can be slow, so an unavailable snapshot during startup is expected.

```bash
sudo /path/to/venv/bin/solix-link lab-status --directory "$PWD/.solix-private/local-mqtt"
sudo /path/to/venv/bin/solix-link lab-readiness --directory "$PWD/.solix-private/local-mqtt"
```

Monitoring polls `0100` every five seconds. Availability requires a connected
station and telemetry younger than 30 seconds. A new connection invalidates
the previous reading. Retained MQTT messages cannot establish freshness or
acknowledge a request. Controller readiness exposes only interpreted prefix
fields; the opaque identifier remains private.

## Charging control

The endpoint starts read-only. Add `--allow-control` to `lab-run` to enable
explicit commands through its owner-only Unix socket:

```bash
sudo /path/to/venv/bin/solix-link lab-set-charge-power \
  --directory "$PWD/.solix-private/local-mqtt" --watts 1700
```

The charging-power limit accepts 300–1800 W in 100 W steps. A write requires
a fresh baseline, a successful `0901` reply and a new `0900` status with the
requested limit and unchanged AC-output state. The upper charge cap is also
available in 5% steps from 80% to 100%:

```bash
sudo /path/to/venv/bin/solix-link lab-set-charge-cap \
  --directory "$PWD/.solix-private/local-mqtt" --upper 95
```

The cap request uses `0103`/`0903` and omits the lower-limit field. Fresh status
must confirm the cap and preserve AC output, lower limit, reserve, charging
power, usage mode and slot count. A cap below the backup reserve is refused
before sending a write, because recovered firmware can clamp that reserve.
Commands are nonretained and serialized; a timeout closes that connection so a late
reply cannot confirm another write. A failed confirmation can still mean the
setting changed: inspect the next fresh status before retrying. The limit is
a charging setpoint; it does not redirect AC loads to the battery.
AC switching is not exposed by this endpoint.

## Reserve, hourly plans and grid return

Explicit native tariff controls require the same `--allow-control` worker:

```sh
sudo /path/to/venv/bin/solix-link lab-set-reserve \
  --directory "$PWD/.solix-private/local-mqtt" --reserve 85
sudo /path/to/venv/bin/solix-link lab-set-tou \
  --directory "$PWD/.solix-private/local-mqtt" \
  --mode standard --period peak:0:24
```

The second command stores an inactive plan. Replace `standard` with
`time_of_use` to activate it explicitly; **Peak can discharge the battery while
mains and AC output stay on**. The plan replaces all prior slots and persists
until changed. Up to six non-overlapping whole-hour slots use `peak`, `mid_peak`
or `off_peak`; hours are 0–24, end exclusive. Split overnight intervals at
midnight. Only single all-day slots have been verified live.

Reserve is an integer 5–100% in 5% steps and must fall between the current
lower limit plus 5 and the upper cap. Plan activation requires fresh complete
status, readiness, mains/output on, fast charge off and no active AC timer.
Writes require fresh plan/reserve and unchanged protected settings, not just ACK.

```sh
sudo /path/to/venv/bin/solix-link lab-grid \
  --directory "$PWD/.solix-private/local-mqtt" --timeout 30
```

Grid return selects native tariff 3 when needed, confirms three fresh grid
samples, clears to Standard/count 0, then confirms three more. It preserves
reserve, caps, charging power and outputs. At zero AC load, flow cannot be
confirmed. `--timeout` is 5–120 seconds per confirmation phase, excluding
transport overhead. A failed command can leave changed settings: inspect fresh
status. `lab-set-tou --mode standard` clears the plan but alone does **not**
confirm grid supply. Stopping the AP/tool does not reset a persistent plan.

## HTTP and Python integration

Run `lab-serve` in another process on the host, using the same private directory:

```bash
sudo /path/to/venv/bin/solix-link lab-serve \
  --directory "$PWD/.solix-private/local-mqtt" --host 127.0.0.1 --port 8765
```

It reuses `/devices`, `/devices/{name}`, `/health`, `/events` and `/metrics` for
Home Assistant or other monitoring clients. Bind a chosen LAN address when
needed; `SOLIX_HTTP_TOKEN` enables Bearer authentication. Monitoring is the
default. Add `--allow-control` to this command and the worker to enable strict
POST `/devices/{name}/commands`; a nonempty token is mandatory. It marks stale
worker files unavailable and needs no BLE connection. See the [gateway/HA
guide](gateway-home-assistant.md) and [prepared custom integration](../custom_components/solix_link/README.md).

Python exports `LabConfig`, `initialize_lab`, `load_lab`, `IsolatedAP`,
`InterceptService`, `LocalMqttServer` and `lab_request`. `InterceptService`
runs inside the isolated namespace; `lab_request` uses its filesystem Unix
socket from a host process. `LocalMqttServer` also offers `set_backup_reserve`,
`set_tou_plan` and `return_to_grid`, using exported `TouPeriod` values. Raw
`NativeMqttCommands` builders only encode packets; they do not confirm writes.
For an HA coordinator, pass a callback to
`LocalMqttServer`/`InterceptService`, or consume the host HTTP event stream.

## Captures, shutdown and recovery

Directories use mode `0700`; config, keys, status, API/MQTT captures and logs
use `0600`. Captures include private identifiers and must stay outside Git.
Full API headers/bodies and MQTT frames are retained locally for later analysis;
public status excludes the serial and raw fields. A passive handler for
`/equipment/logging/upload_pb_events` retains/decodes recovered energy groups
privately; units and C2000 behavior remain unverified. See [firmware follow-up](tariff-energy-followup.md).
Archive these files before
sharing a sanitized summary or removing a lab deployment.

Ctrl-C, SIGTERM and `--duration SECONDS` stop owned services, flush the AP
address, return the adapter to the original namespace and leave it DOWN. If
returning the adapter fails, the namespace is retained for recovery. A forced
kill or power failure can leave a namespace/socket; inspect and recover those
resources before starting again. The tool refuses to destroy a preexisting
namespace or socket. It does not restore former station Wi-Fi credentials.

See [reconnect and tariff findings](c2000-mqtt-reconnect-and-tariff.md) for live
results and remaining charging/discharge questions.

## Verification

On C2000 main **2.1.6.4**, the packaged CLI established the isolated AP/API/NTP
and native TLS MQTT connection, read live status and `0089`, and confirmed
**1800→1700→1800 W** through the packaged control socket. AC output stayed on.
An independent fresh BLE reading after AP shutdown confirmed the full baseline.
Initial rejected double-slash API paths and incorrectly wrapped CA fields were
captured, corrected and regression tested. The native HTTP adapter and guided
mode have automated tests; their entire interactive flow has not been exercised
on hardware. A subsequent [corrected Peak trial](c2000-corrected-peak-trial.md)
verified local scheduled discharge with mains connected and AC output enabled.
A [later public CLI trial](c2000-offpeak-grid-return.md) exercised packaged
reserve/plan commands and `lab-grid`: tariff 3 restored grid power before the
plan was cleared. Independent MQTT and BLE checks confirmed the baseline.
The new terminal dashboard and HA component have synthetic/contract tests;
their full UI/HA runtime flow has not been exercised on hardware.

The subsequent native cap test confirmed **90→95→90%** with a **300 W**
charging limit, producing charging telemetry and roughly 315 W above the AC
load. A 60-second trial using the old Peak encoding during charging reported
no active tariff. The [subsequent audit](c2000-tou-encoding-audit.md) found that
encoding malformed; corrected discharge was subsequently verified in the
linked Peak trial. Original
settings were restored; a separate read-only MQTT connection
confirmed them and battery idle. See the [full trial record](c2000-mqtt-reconnect-and-tariff.md#native-upper-cap-and-charging-state-peak-trial),
including failed BLE checks and the resulting service-shutdown fix.
