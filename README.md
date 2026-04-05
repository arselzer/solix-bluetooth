# Anker Solix Bluetooth

A web app for local Bluetooth control and monitoring of Anker Solix devices, without requiring the Anker app or cloud account.

Tested with:
- **Solarbank 3 E2700 Pro** (A17C5) — telemetry monitoring, daytime solar confirmed
- **Anker SOLIX C1000** (A1761) — telemetry monitoring + control commands
- **Anker SOLIX C300X** (A1753) — telemetry monitoring

Built with Vue 3, TypeScript, and the Web Bluetooth API. Protocol based on reverse engineering from the [SolixBLE](https://github.com/flip-dots/SolixBLE) project with additional findings from live device testing and Anker app decompilation.

## Features

- **Local BLE connection** — connects directly via Bluetooth, no cloud or account needed
- **Encrypted communication** — full ECDH key exchange and AES-CBC encryption
- **Live telemetry** — decodes real-time device data with auto type-byte detection:
  - Solar input power (per-panel, dual-input confirmed with 2x400W panels)
  - Battery percentage, temperature, charge/discharge power
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
| Anker SOLIX C300X | A1753 | Yes | Untested | uint16 (279B) | Serial at 0xc5, different param layout |

## How It Works

### BLE Protocol

Anker Solix devices use a custom BLE GATT service with encrypted binary communication:

| Component | UUID |
|-----------|------|
| Service | `8c850001-0302-41c5-b46e-cf057c562025` |
| Write (app -> device) | `8c850002-0302-41c5-b46e-cf057c562025` |
| Notify (device -> app) | `8c850003-0302-41c5-b46e-cf057c562025` |

### Connection Flow

1. **Device discovery** — filters by BLE name prefix ("Solarbank", "Anker", "C1000", etc.)
2. **GATT connect** — auto-retry up to 3 times (first attempt often fails)
3. **ECDH key exchange** — 5-stage negotiation using P-256 curve
4. **AES-CBC encryption** — key = first 16 bytes of shared secret, IV = bytes 16-31
5. **Telemetry** — Solarbank streams every ~3s; C1000/C300X respond to status requests
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

#### C1000 (A1761) — 312 bytes, uint16 values

| ID | Name | Notes |
|----|------|-------|
| `0xa5`-`0xa9` | AC in/out, DC out, USB-C, USB-A | Power (W) |
| `0xab` | Battery percentage | % |
| `0xac` | Battery power | Signed (W) |
| `0xae` | Solar input | W |
| `0xaf`/`0xb0` | AC/DC switch state | 0=off, 1=on |
| `0xb3`/`0xba` | Temperature / Battery temp | /10 = Celsius |
| `0xb5`/`0xb6` | Battery cycles / health | |
| `0xd0` | Serial number | ASCII string |
| `0xd1` | Capacity | 1000 = 1000Wh |
| `0xd7`/`0xd8` | AC/DC enabled | Config flags |
| `0xda` | Min SoC reserve | 50% |
| `0xfd` | Model name | "A1761_30Ah" |

#### C300X (A1753) — 279 bytes, uint16 values

| ID | Name | Notes |
|----|------|-------|
| `0xb1` | Battery capacity | 1049 Wh |
| `0xb3` | Total discharged | 508 Wh lifetime |
| `0xb4` | Temperature | 27°C |
| `0xb5` | Battery SoC | 123 |
| `0xbb`/`0xbc` | AC/DC power limits | 82W |
| `0xc5` | Serial number | "AZVSBK0F11400646" |
| `0xc6` | Max AC input | 330W |
| `0xc7` | Max solar input | 120W |
| `0xc8`/`0xc9` | Display/idle timeout | 30s / 60min |
| `0xcd` | Light mode | 2 = auto |
| `0xce` | Min SoC | 50% |

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

## Project Structure

```
src/
  protocol/
    constants.ts    — UUIDs, ECDH key, negotiation packets, 3 device param maps
    crypto.ts       — ECDH P-256 key exchange, AES-CBC/GCM encrypt/decrypt
    packet.ts       — FF09 packet framing, checksum, parsing
    telemetry.ts    — TLV decoder with auto type-byte detection
    connection.ts   — BLE connection, negotiation, fragment reassembly, auto-poll
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
docs/
  app-reverse-engineering.md — Anker app decompilation findings
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
- C300X sends 2 fragments (253B + 57B) without sequence bytes
- Device public key is after 3 prefix bytes (`00 a1 40`) in the cmd `0x21` response
- `0x4030` returns firmware versions including model code ("A17C5", "A17C5_mcu", "A17C5_esp32")
- `0x4020` returns device capabilities (31B on Solarbank)
- Temperature confirmed at `0xa6` (not battery health) — matches `0xd4` and ambient temp
- Solar inputs at `0xc7` (input 1) and `0xc6` (input 2) — sum matches `0xab` total

## Status

This is an active reverse engineering project. Three devices connect, decrypt, and display telemetry successfully. C1000 control commands are confirmed working.

**Working:**
- BLE connection with auto-retry and auto-reconnect
- ECDH key exchange + AES-CBC decryption
- TLV telemetry parsing with type-byte auto-detection
- C1000 control commands (AC, DC, display, lights)
- Solarbank 3 real-time telemetry monitoring (daytime solar confirmed)
- C300X telemetry monitoring
- Periodic status polling (10s interval)
- Command scanner for discovering new commands
- CSV export (snapshot or continuous recording)
- Session persistence (logs survive page reloads)
- Wake Lock (prevents browser suspension)
- GitHub Pages deployment

**Not yet implemented:**
- Solarbank control commands (command scan done, write payloads need Frida)
- C300X control commands (untested)
- Setting write commands (min SoC, charge limits, timeouts)
- Multi-device simultaneous connection

## Credits

- [flip-dots/SolixBLE](https://github.com/flip-dots/SolixBLE) — Python BLE library, protocol foundations
- [thomluther/AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE) — original Solarbank BLE project
- [flip-dots/HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) — Home Assistant integration

## License

MIT
