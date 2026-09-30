# Anker Solix Bluetooth

A web app for local Bluetooth control and monitoring of Anker Solix devices. The C1000 Gen 2 and C2000 Gen 2 can be paired locally with a generated client ID and a short main power button press when using the Prime protocol.

Tested with:
- **Solarbank 3 E2700 Pro** (A17C5) — telemetry monitoring, daytime solar confirmed
- **Anker SOLIX C1000** (A1761) — telemetry monitoring + control commands
- **Anker SOLIX C300X AC** (reference model A1723) — Python telemetry, reconnects, AC output, light, charging-power limit, and display timeout verified
- **Anker SOLIX C1000 Gen 2** (A1763) — live BLE telemetry and AC/DC output controls on this laptop
- **Anker SOLIX C2000 Gen 2** (A1783) — live BLE telemetry and verified charge-cap, charging-power, and screen-timeout controls from Python and MQTT, with AC output left on

Built with Vue 3, TypeScript, and the Web Bluetooth API. Protocol based on reverse engineering from the [SolixBLE](https://github.com/flip-dots/SolixBLE) project with additional findings from live device testing and Anker app decompilation.

## Features

- **Local BLE connection** — connects directly via Bluetooth, no cloud or account needed
- **Encrypted communication** — ECDH with AES-CBC or AES-GCM, depending on model
- **Live telemetry** — decodes real-time device data with auto type-byte detection:
  - Solar input power (per-panel, dual-input confirmed with 2x400W panels)
  - Battery percentage, health, cycles, temperature, charge/discharge power
  - House demand, consumption, grid power
  - AC/DC output power, switch states
  - Device settings (charge limits, timeouts, UPS mode)
- **Control commands** (C1000) — AC/DC on/off, display, LED light modes
- **Periodic status polling** — auto-requests full telemetry every 10s
- **Auto-reconnect** — automatically reconnects on unexpected disconnect
- **Command scanner** — brute-force scan command space for reverse engineering
- **CSV export** — snapshot or continuous recording at 10s intervals
- **Protocol log** — TLV-annotated log with full decrypted hex and copy button
- **Session persistence** — logs and telemetry survive page reloads
- **Wake Lock** — prevents browser from suspending when backgrounded
- **Clear state** — reset logs/telemetry when switching between devices

## Supported Devices

| Device | Model | Telemetry | Control | Param Style | Notes |
|--------|-------|-----------|---------|-------------|-------|
| Solarbank 3 E2700 Pro | A17C5 | Yes | Scan only | float32 (495B) | Streams telemetry, dual solar inputs confirmed |
| Anker SOLIX C1000 | A1761 | Yes | Yes | uint16 (312B) | AC/DC toggle, display, lights confirmed |
| Anker SOLIX C300/C300X AC | A1722/A1723 | Python: C300X tested | Python: AC output, light, charging power, display timeout | Typed TLV | Legacy CBC, no account ID; C300 sibling untested; historical browser map needs correction |
| Anker SOLIX C1000 Gen 2 | A1763 | Yes | AC/DC On/Off; charge limits, AC charging power, display timeout, and fast charge switch | Packed TLV | Firmware 1.1.4.3: legacy CBC; 1.1.4.9: Prime GCM and button pairing. Controls verified after update; AC on/DC off restored |
| Anker SOLIX C2000 Gen 2 | A1783 | Yes | Python/CLI/MQTT charge cap, charging power, and screen timeout; browser monitoring only | Packed TLV | Prime GCM; generated client ID paired by main button press |

The [Python library, CLI, HTTP server, and local MQTT bridge](python/README.md) provide local monitoring for C300 AC and these Gen 2 models and can feed Home Assistant or other servers. The Python client, CLI, and MQTT bridge expose C300 screen timeout, the C2000 upper charge cap, AC charging-power limit, and 30/60-second screen timeout, plus verified C1000 Gen 2 charge limits, AC charging power, display timeout, and fast charge settings. Raising the C2000 cap from 90% to 95% started charging at the configured 500 W limit while AC output stayed on. The browser exposes C1000 Gen 2 charge limits, AC charging power, and AC/DC switches. The CLI can join either Prime station to a WPA2 AP over Bluetooth; C1000 Gen 2 API setup is experimental, and direct device MQTT remains under investigation. C2000 output controls remain disabled. On both tested Prime stations, the browser generates a 40-character client ID, saves it locally for the selected Bluetooth device, and prompts for one short main power button press if registration returns `09`. Click **I pressed the main button** after pressing it. Later connections reuse the saved ID without another press. You can also enter an existing 40-character ID. The Python library exposes its generated ID for the caller to save in Home Assistant configuration. See the [versioned protocol notes](docs/gen2-protocol.md) for observed behavior and open questions.

[C300/C300X AC findings](docs/c300-protocol.md) include live USB-C charging,
firmware identifiers, and restored control tests. The Python package
also includes [original C1000/A1761 support ready for testing](docs/c1000-original-protocol.md):
its new decoder and controls have synthetic tests, **no hardware
verification**. Controls use normal APIs with validation and fresh telemetry
confirmation, without an opt-in flag. These legacy profiles need no Prime pairing ID. C300 DC is
not supported by these profiles.

Prime registration is encrypted using a fresh ECDH session key. Local pairing on both tested stations required no Anker account ID: each first rejected our generated ID with `09`; after one short main power button press and a registration retry in the same BLE connection, each accepted the ID and streamed telemetry. They accepted the same ID again on reconnect. [Anker's C2000 guide](https://salesforce-knowledge-download.s3.us-west-2.amazonaws.com/000032532/en_US/000032532.pdf) documents this physical pairing confirmation. The phone's active Bluetooth connection prevented the laptop from seeing the C2000 advertisement during our test, so temporarily disconnect the phone if discovery fails. The C2000's AC output remained on throughout testing; do not press the separate AC output button.

## Research status

The [firmware findings](docs/firmware-findings.md) document the recovered C1000
Gen 2 1.1.4.9 controller and radio code, integrity checks, command handlers, and
remaining questions. Its Time-of-Use logic requires binding and network readiness;
the radio normally reports readiness only after both Wi-Fi and MQTT connect.
The [encoding audit](docs/c2000-tou-encoding-audit.md) now reconciles its schedule
count/triplets with retained C2000 data.
[Native local MQTT](docs/local-mqtt-investigation.md) now works on C2000/main
2.1.6.4 with TLS client certificates, saved-settings reconnect, live telemetry,
and verified charging-power changes. The [corrected Peak trial](docs/c2000-corrected-peak-trial.md)
now verifies battery discharge with mains connected and AC output enabled,
using entirely local MQTT. The [packaged CLI follow-up](docs/c2000-offpeak-grid-return.md)
also verified Off-Peak grid return before clearing Standard. Python now exposes
guarded reserve, hourly plans and flow-confirmed recovery, a [terminal dashboard](python/README.md#terminal-dashboard-and-ha-gateway),
and an [authenticated HTTP/HA gateway](docs/gateway-home-assistant.md).
An optional [FastAPI/Vue web dashboard](docs/web-dashboard.md) adds local charts
and confirmed controls without a Node runtime or CDN. C1000 Gen 2
[temperature and off-grid alert settings](docs/c1000-general-settings.md)
are also verified through native MQTT.
[Multiple stations on one isolated AP](docs/multiple-ap-devices.md) share
one gateway while retaining separate certificates, telemetry and controls.
The HA component is prepared and contract-tested; its runtime integration
remains unverified. Timed schedules/reserve-floor behavior need live validation.
The [offline tariff/energy follow-up](docs/tariff-energy-followup.md) adds
101 replay cases and a passive binary energy-report decoder with unverified units.
The packaged MQTT bridge communicates with the stations over BLE. Raw captures, firmware,
credentials, and reproducible private analysis stay in the ignored
`.solix-private/` directory.

## How It Works

### BLE Protocol

Anker Solix devices use a custom BLE GATT service with encrypted binary communication:

| Component | UUID |
|-----------|------|
| Service | `8c850001-0302-41c5-b46e-cf057c562025` |
| Write (app -> device) | `8c850002-0302-41c5-b46e-cf057c562025` |
| Notify (device -> app) | `8c850003-0302-41c5-b46e-cf057c562025` |

### Connection Flow

1. **Device discovery** — filters by BLE service and name, including `SOLIX` Gen 2 advertisements
2. **GATT connect** — auto-retry up to 3 times (first attempt often fails)
3. **ECDH key exchange** — P-256 negotiation with fresh session keys
4. **Encrypted session** — legacy AES-CBC on C1000 Gen 2 firmware 1.1.4.3; Prime AES-GCM on C1000 Gen 2 firmware 1.1.4.9 and the tested C2000 Gen 2
5. **Telemetry** — Solarbank streams every ~3s; C1000/C300X respond to status requests. Gen 2 devices need a `4100` subscription; Prime stations may first need a short main power button pairing confirmation
6. **Auto-polling** — full status requested every 10s for continuous updates

### Packet Format

```
[FF09] [Length 2B LE] [Pattern 3B] [Command 2B] [Payload nB] [Checksum 1B]
```

- `030001` = encryption negotiation
- `03010f` = encrypted session data
- Checksum = XOR of all preceding bytes
- Length is little-endian uint16, equals total packet size

### TLV Data Format

After decryption, all data uses Tag-Length-Value encoding:

```
[ParamID 1B] [Length 1B] [TypeByte 1B] [Value nB] ...
```

The **type byte** (first byte of each value) indicates the encoding:

| Type | Meaning | Used by |
|------|---------|---------|
| `0x00` | ASCII string | All devices (serial numbers) |
| `0x01` | uint8 | C1000/C300X (switch states, settings) |
| `0x02` | uint16 LE | C1000/C300X (power values in W) |
| `0x03` | uint32 LE | All devices (energy counters) |
| `0x04` | bytes/string | All devices (firmware info, config) |
| `0x05` | float32 LE | Solarbank 3 (power values in W) |

### Known Commands

#### All Devices
| Command | Name | Payload |
|---------|------|---------|
| `0x4020` | Device Capabilities | `a10121` |
| `0x4030` | Firmware Versions | `a10121` |
| `0x4040` | Full Status Request | `a10121` |
| `0x4041` | Partial Status | `a10121` |

#### C1000 (confirmed via live testing)
| Command | Name | ON Payload | OFF Payload |
|---------|------|------------|-------------|
| `0x404a` | AC Toggle | `a10121a2020101` | `a10121a2020100` |
| `0x404b` | DC Toggle | `a10121a2020101` | `a10121a2020100` |
| `0x404f` | Light Mode | `a10121a20201XX` (0/1/2) | — |
| `0x4052` | Display Toggle | `a10121a2020101` | `a10121a2020100` |

#### Solarbank 3 (scan results, function TBD)
| Command | Response | Possible Function |
|---------|----------|-------------------|
| `0x4050` | Boolean toggle | Feed-in grid? |
| `0x4057` | Boolean toggle | Off-grid mode? |
| `0x405e` | Boolean toggle | Self-consumption? |
| `0x4081` | Boolean toggle | Green energy priority? |
| `0x409a` | Boolean toggle | Unknown |

### Telemetry Parameters

#### Solarbank 3 Pro (A17C5) — 495 bytes, float32 values

| ID | Name | Notes |
|----|------|-------|
| `0xa2` | Serial number | ASCII string |
| `0xa5` | Battery percentage | % |
| `0xa6` | Temperature | Celsius (confirmed: matches ambient) |
| `0xab` | Solar power total | float32 W (sum of both inputs) |
| `0xac` | PV yield total | float32 W |
| `0xad` | Output power | float32 W (to home) |
| `0xae` | Charge power | float32 W (to battery) |
| `0xb0`-`0xb3` | Cumulative kWh counters | float32 (discharge, demand, consumption, grid) |
| `0xb9` | Output limit setting | 200W |
| `0xba` | Home load setting | uint32 config |
| `0xbd`/`0xbe` | Grid power/import limit | 800W / 600W |
| `0xc0` | Feed-in limit | W |
| `0xc5` | Battery charge current | float32 W |
| `0xc6` | Solar input 2 | float32 W (second panel set) |
| `0xc7` | Solar input 1 | float32 W (first panel set) |
| `0xd4` | Temperature 2 | Confirms 0xa6 is temperature |
| `0xd5`/`0xd6` | Max output/charge power | 3600W / 1200W |
| `0xfe` | Anti-replay timestamp | Increments each packet |

#### Original C1000 and C300/C300X AC

The historical browser field tables conflict with current reference maps
and the new C300 capture. Use the separate [original C1000 reference map](docs/c1000-original-protocol.md)
and [live C300 findings](docs/c300-protocol.md) for Python work. In particular,
C300 `B1=1049` is a firmware version code, not battery capacity; `BB` is
battery percentage, `C8` is the verified display timeout, and `CF` is light
mode. C300 `BC` and `CD` remain ambiguous. The browser decoder has not yet been
updated to these corrected Python mappings.

## Requirements

- **Browser**: Chrome or Edge (Web Bluetooth API required)
- **HTTPS**: Web Bluetooth only works on secure origins (localhost works for dev)
- **Proximity**: BLE range, typically a few meters from the device

## Getting Started

```bash
npm install
npx vite --host
```

Open the URL in Chrome, click **Connect**, and select your device. You may need to press the IoT button on the device to enable BLE discovery.

For remote access (e.g., from phone), tunnel with cloudflare:
```bash
cloudflared tunnel --url http://localhost:5173
```

### Development checks

```bash
npm run build
node --import tsx --test tools/test-gen2-reconnect.ts
PYTHONPATH=python python3 -m pytest python/tests -q
```

The browser build checks TypeScript. The offline GATT tests cover Prime and
legacy reconnects, paired-ID reuse, cancellation, and the C2000 output-command
guard. Install `./python[server,mqtt]` and `pytest` to include the HTTP tests.
These checks do not contact a power station.

## Project Structure

```
src/
  protocol/
    constants.ts    — UUIDs, negotiation packets, legacy device param maps
    crypto.ts       — ECDH P-256 key exchange, AES-CBC/GCM encrypt/decrypt
    packet.ts       — FF09 packet framing, checksum, parsing
    telemetry.ts    — TLV decoder with auto type-byte detection
    connection.ts   — BLE connection, negotiation, fragment reassembly, auto-poll
    prime.ts        — Gen 2 Prime negotiation and encrypted telemetry
    utils.ts        — Hex conversion, byte manipulation
    types.ts        — TypeScript interfaces
  components/
    ConnectionPanel.vue   — Connect/disconnect/clear with status
    TelemetryDisplay.vue  — Grouped telemetry grid with CSV export
    CommandPanel.vue      — Device-specific quick commands + custom hex input
    CommandScanner.vue    — Brute-force command scanner with presets
    LogViewer.vue         — TLV-annotated protocol log with copy
    RawPackets.vue        — Hex packet viewer
  App.vue           — Main app with tabbed interface, wake lock, session persistence
tools/
  decode-capture.ts — Offline capture decoder (Node.js)
  test-gen2-reconnect.ts — Synthetic GATT regression tests
python/
  solix_link/       — Async BLE library, CLI, HTTP server, MQTT bridge and isolated AP
  solix_gen2/       — Compatibility imports for existing clients
  tests/           — Synthetic protocol and service tests
custom_components/solix_link/ — Prepared local-gateway Home Assistant integration
home_assistant_tests/ — Standalone HTTP contract tests (HA runtime not required)
docs/
  app-reverse-engineering.md — Anker app decompilation findings
  gen2-protocol.md  — Versioned live Gen 2 observations
  firmware-findings.md — Offline C1000 firmware analysis and open questions
  local-mqtt-investigation.md — Native MQTT parser replays and isolated AP trials
```

## Reverse Engineering

### Command Scanner
The Scanner tab sends commands across a configurable range (e.g., `0x4000`-`0x40FF`) and logs responses. Supports configurable first byte, payload presets, and delay. Stops automatically on disconnect.

### App Decompilation
The Anker app (`com.anker.charging`) is a Flutter app. We extracted 200+ method names, 100+ set methods, and analytics tracking events from `libapp.so`. See [docs/app-reverse-engineering.md](docs/app-reverse-engineering.md) for full findings.

### Capture Decoder
Enable BLE HCI snoop logging on Android, then decode captures from our web app with:
```bash
tshark -r btsnoop_hci.log \
  -Y "(btatt.opcode == 0x52 || btatt.opcode == 0x1b) && btatt.value contains ff:09" \
  -T fields -e frame.number -e frame.time_relative -e btatt.opcode -e btatt.value \
  | npx tsx tools/decode-capture.ts
```
Note: Only works for captures from our app (known ECDH key). The official Anker app generates fresh keys per session.

### Key Findings
- The Anker app generates a **fresh ECDH keypair each session** (not the SolixBLE hardcoded key)
- TLV type byte `0x05` = IEEE 754 float32 (Solarbank uses floats, C1000/C300X use uint16)
- The Solarbank 3 sends 3 fragments (2x253B + 1 small) with sequence bytes that must be stripped
- C1000 sends 2 fragments without sequence bytes
- The tested C300X AC sends two fragments with `12`/`22` counters; strip them before CBC decryption (see the C300 notes)
- Device public key is after 3 prefix bytes (`00 a1 40`) in the cmd `0x21` response
- `0x4030` returns firmware versions including model code ("A17C5", "A17C5_mcu", "A17C5_esp32")
- `0x4020` returns device capabilities (31B on Solarbank)
- Temperature confirmed at `0xa6` (not battery health) — matches `0xd4` and ambient temp
- Solar inputs at `0xc7` (input 1) and `0xc6` (input 2) — sum matches `0xab` total

## Status

This is an active reverse engineering project. Device and firmware verification
is tracked separately in the Python and protocol documentation; historical
browser observations do not verify new Python implementations.

**Working:**
- BLE connection with auto-retry and auto-reconnect
- ECDH key exchange + AES-CBC decryption
- TLV telemetry parsing with type-byte auto-detection
- C1000 control commands (AC, DC, display, lights)
- Solarbank 3 real-time telemetry monitoring (daytime solar confirmed)
- C300X AC Python telemetry, reconnects, AC output, light, charging-power limit, and screen timeout
- Periodic status polling (10s interval)
- Command scanner for discovering new commands
- CSV export (snapshot or continuous recording)
- Session persistence (logs survive page reloads)
- Wake Lock (prevents browser suspension)
- GitHub Pages deployment

**Not yet implemented:**
- Solarbank control commands (command scan done, write payloads need Frida)
- C300X AC battery-percentage cap, display brightness, and corrected browser decoding
- Original C1000 Python hardware validation (experimental support is ready)
- Setting write commands (min SoC, charge limits, timeouts)
- Multi-device simultaneous connection

## Credits

- [flip-dots/SolixBLE](https://github.com/flip-dots/SolixBLE) — Python BLE library, protocol foundations
- [thomluther/AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE) — original Solarbank BLE project
- [flip-dots/HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) — Home Assistant integration

## License

MIT
