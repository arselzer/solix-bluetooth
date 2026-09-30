# SOLIX Link

Local monitoring and controls for Anker SOLIX power stations over **Bluetooth**
or **native MQTT on an isolated Wi-Fi AP**. Includes an async Python library,
CLI, fixed terminal dashboard, optional FastAPI/Vue web dashboard, and a
Home Assistant integration for the local gateway.

## Features and devices

- Battery, temperature, power, output states and supported settings.
- Charging power and charge limits with fresh telemetry confirmation.
- Gen 2 native MQTT: backup reserve, hourly Time-of-Use plans, battery discharge
  with mains connected, and confirmed return to grid.
- C1000 Gen 2 native MQTT: 100–1200 W charging, fast charge, temperature unit,
  off-grid alert, display brightness/timeout, output-port memory and guarded
  lower discharge limit without silently changing reserve.
- Original C1000: temperature units, fast charge, AC/DC Smart modes and Device
  Timeout through Bluetooth, the bridge and gateway interfaces; isolated Wi-Fi setup.
- Authenticated JSON HTTP, SSE and Prometheus for multiple stations.
- Up to eight Gen 2 stations on one isolated AP, with separate certificates,
  telemetry and command queues.
- Web dashboard with device selection, power/battery charts and explicit write
  confirmation. Deployment needs no Node runtime or external assets.

| Device | Python Bluetooth | Native local MQTT | Tested / limitations |
| --- | --- | --- | --- |
| C1000 Gen 2, A1763 | Monitoring, charge limits/power, display timeout, fast charge | Charging/fast/reserve, tariffs, temperature/alert, display/memory, discharge floor | Main 1.1.4.9 / radio 0.3.3.0; older 1.1.4.3 uses legacy BLE |
| C2000 Gen 2, A1783 | Monitoring, power/cap, display timeout | Charging/reserve, tariffs and return to grid | Main 2.1.6.4; AC-output writes blocked |
| Original C1000, A1761 | Monitoring and twelve controls on legacy; Prime brightness/power/device timeout | — | Legacy 1.5.1; Prime 1.7.1 verified with settings restored; other Prime controls unvalidated |
| C300/C300X AC, A1722/A1723 | Monitoring, AC output, light, charging power, display timeout | — | C300X tested; C300 sibling untested; C300 DC unsupported |
| Solarbank 3 E2700 Pro, A17C5 | Separate Web Bluetooth app | — | Browser telemetry tested; no Python profile |

Native MQTT connects the station itself to the AP service. The optional
BLE-to-MQTT bridge reads Bluetooth and publishes to your existing broker.
Two simultaneous native stations passed simulated TLS tests; a physical
multi-station AP test is outstanding. See the [Python guide](python/README.md).

## Start locally

BLE requires Python 3.11+ and a working Bluetooth adapter. Install in an
isolated environment, for example with pipx:

```sh
pipx install './python[server,mqtt,tui]'
solix-link                         # interactive terminal dashboard
solix-link scan
solix-link --help                  # scriptable CLI and server commands
```

The terminal dashboard scans, selects, connects and pairs supported devices.
A line-based interactive fallback is available. For saved-device CLI usage:

```sh
solix-link add --name office --model c1000 --address AA:BB:CC:DD:EE:04
solix-link monitor --name office
solix-link set-charge-power --name office --watts 300
```

C300 AC and original C1000 firmware 1.5.1 use legacy BLE without a pairing ID.
Original C1000 firmware 1.7.1 uses explicit Prime selection; monitoring,
brightness, charging power and Device Timeout were verified with an existing
app ID. Generated-ID pairing on this model remains unverified. Prime Gen 2
pairing generates a local ID and can require one short **main power button**
press. Save the ID for reconnects. Generated-identity native MQTT is verified
on C1000 Gen 2; C2000 native provisioning uses an already working identity.
See [pairing and version notes](docs/gen2-protocol.md).

## Browser, API and Home Assistant

Start the gateway for saved Bluetooth devices:

```sh
# Load a generated token from an owner-only file.
SOLIX_HTTP_TOKEN="$(cat /path/to/http-token)" \
  solix-link serve --config /path/to/config.json --web-ui --allow-control
```

Open `http://127.0.0.1:8765/` and enter the token. Omitting `--allow-control`
makes the gateway read-only. Native AP stations use `ap-service-serve --web-ui`;
the AP worker must separately allow controls. CLI/private socket writes use
`--name` when several stations share the AP.

- [Web dashboard](docs/web-dashboard.md): setup, charts, controls and screenshots.
- [Shared isolated AP](docs/multiple-ap-devices.md): registration, provisioning,
  device selection and certificate routing.
- [Gateway API](docs/gateway-home-assistant.md): JSON commands, SSE, metrics and deployment.
- [Home Assistant component](custom_components/solix_link/README.md): sensors,
  charging controls, selectors, alert switch and tariff actions.

HA has standalone contract tests; actual HA runtime compatibility still needs
testing. Session charts are not persistent energy accounting. Measured power
can feed HA's Integral helper; firmware energy-counter units remain unverified.

## Development

The Python package lives in `python/solix_link/`, the gateway Vue dashboard in
`dashboard/`, and the separate Web Bluetooth protocol explorer in `src/`.
The explorer provides packet logs, CSV export and research tools; some older
legacy-model maps differ from the newer Python decoders.

```sh
npm ci
npm run dev                       # Web Bluetooth application
npm run build
npm run build:dashboard            # Vue check + bundled gateway assets
PYTHONPATH=python python3 -m pytest python/tests home_assistant_tests -q
npm run test:dashboard             # synthetic browser fixture only
```

Install the Python `server` extra plus `pytest`, `httpx` and `aiohttp` for
server/HA tests. Browser checks need Playwright Chromium; setup is in the
web-dashboard guide. Commit Vue sources and compiled Python assets together.

## Protocol research and firmware

- [Gen 2 protocol](docs/gen2-protocol.md), [original C1000](docs/c1000-original-protocol.md)
  and [C300](docs/c300-protocol.md): wire formats and hardware evidence.
- [Firmware findings](docs/firmware-findings.md): handlers, readiness, tariffs,
  checksums and update verification.
- [Firmware inputs](firmware/README.md): recovered vendor images, hashes and provenance.
- [Original-C1000 preferences](docs/c1000-preferences-validation.md) and
  [isolated Wi-Fi](docs/c1000-original-wifi-validation.md); direct MQTT remains open.
- [Charging and reserve validation](docs/c1000-charging-and-reserve-validation.md):
  native fast charge, lower charging power, reserve floor and cached reconnect.
- [Native display/memory validation](docs/c1000-native-preferences-validation.md):
  confirmed C1000 Gen 2 settings and restoration; final BLE check unavailable.
- [Reproduce 1,842 offline cases](docs/firmware-analysis-reproduction.md):
  original instruction execution with synthetic inputs and explicit substitutions.
- [Charging follow-up](docs/c1000-charging-control-followup.md): reserve side
  effects, mirrored limits and why 0 W is unavailable as charge pause.
- [Energy accounting](docs/gen2-energy-counter-investigation.md): sampling,
  scheduler gaps and nominal Wh arithmetic; calibrated units remain unverified.
- [Smart auto-off policy](docs/c1000-smart-auto-off-policy.md) and
  [tariff clock](docs/gen2-schedule-clock-audit.md): recovered behavior and limits.

Firmware results are version-specific. Idle sleep, calibrated energy and
actual alert delivery need further hardware validation.
Keep IDs, credentials and raw captures in ignored `.solix-private/` files.
Device tests record baselines, confirm fresh readback and restore settings.
The development C2000 powers servers; its AC output is never toggled.

## Credits and license

Protocol references: [SolixBLE](https://github.com/flip-dots/SolixBLE),
[AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE),
[HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) and
[anker-solix-api](https://github.com/thomluther/anker-solix-api).
Repository software: MIT. Vendor firmware inputs retain their own provenance
and notices; the software license grants no rights to vendor firmware.
