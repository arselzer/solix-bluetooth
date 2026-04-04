# Anker Solix Bluetooth

A web app for local Bluetooth control and monitoring of Anker Solix devices, without requiring the Anker app or cloud account.

Tested with:
- **Solarbank 3 E2700 Pro** (A17C5) — telemetry monitoring
- **Anker SOLIX C1000** (A1761) — telemetry monitoring + control commands

Built with Vue 3, TypeScript, and the Web Bluetooth API. Protocol based on reverse engineering from the [SolixBLE](https://github.com/flip-dots/SolixBLE) project with additional findings from live device testing.

## Features

- **Local BLE connection** — connects directly via Bluetooth, no cloud or account needed
- **Encrypted communication** — full ECDH key exchange and AES-CBC encryption
- **Live telemetry** — decodes real-time device data:
  - Solar input power (per-panel PV1–PV4)
  - Battery percentage, health, cycles, temperature, power
  - House demand, consumption, grid power
  - AC/DC output power, switch states
  - Device settings (charge limits, timeouts, UPS mode)
- **Control commands** (C1000) — AC/DC on/off, display, LED light modes
- **Command scanner** — brute-force scan command space for reverse engineering
- **Protocol log** — TLV-annotated log with full decrypted hex for analysis
- **Raw packet viewer** — hex dump of all TX/RX BLE packets

## Supported Devices

| Device | Model | Telemetry | Control | Notes |
|--------|-------|-----------|---------|-------|
| Solarbank 3 E2700 Pro | A17C5 | Yes | Not yet | Streams telemetry every ~3s automatically |
| Anker SOLIX C1000 | A1761 | Yes | Yes | Needs status request to trigger telemetry |

## How It Works

### BLE Protocol

Anker Solix devices use a custom BLE GATT service with encrypted binary communication:

| Component | UUID |
|-----------|------|
| Service | `8c850001-0302-41c5-b46e-cf057c562025` |
| Write (app → device) | `8c850002-0302-41c5-b46e-cf057c562025` |
| Notify (device → app) | `8c850003-0302-41c5-b46e-cf057c562025` |

### Connection Flow

1. **Device discovery** — filters by BLE name prefix ("Solarbank", "Anker", etc.)
2. **GATT connect** — auto-retry up to 3 times (first attempt often fails)
3. **ECDH key exchange** — 5-stage negotiation using P-256 curve
4. **AES-CBC encryption** — key = first 16 bytes of shared secret, IV = bytes 16–31
5. **Telemetry** — Solarbank streams every ~3s; C1000 responds to status requests

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

| Type | Meaning | Example |
|------|---------|---------|
| `0x00` | ASCII string | Serial number |
| `0x01` | uint8 | Switch states |
| `0x02` | uint16 LE | Power values (W) |
| `0x03` | uint32 LE | Energy counters |
| `0x04` | bytes/string | Firmware info |
| `0x05` | float32 LE | Solarbank power values |

### Known Commands (C1000)

| Command | Name | ON Payload | OFF Payload |
|---------|------|------------|-------------|
| `0x4040` | Status Request | `a10121` | — |
| `0x4041` | Partial Status | `a10121` | — |
| `0x404a` | AC Toggle | `a10121a2020101` | `a10121a2020100` |
| `0x404b` | DC Toggle | `a10121a2020101` | `a10121a2020100` |
| `0x404c` | Display Mode | `a10121a20201XX` | — |
| `0x404f` | Light Mode | `a10121a20201XX` (0/1/2) | — |
| `0x4046` | Display Timeout | `a10121a20302XXXX` | — |
| `0x4052` | Display Toggle | `a10121a2020101` | `a10121a2020100` |

### Telemetry Parameters

#### Solarbank 3 Pro (A17C5) — 494 bytes, float32 values

| ID | Name | Type | Notes |
|----|------|------|-------|
| `0xa2` | Serial number | string | |
| `0xa5` | Battery % aggregate | float | /10 |
| `0xa6` | Battery health | float | /10 |
| `0xab`–`0xae` | Solar/output power | float32 | Watts |
| `0xb0`–`0xb3` | Discharge/demand/consumption/grid | float32 | Watts |
| `0xb6` | Battery power | int | Signed |
| `0xbd` | Grid power | int | Watts |
| `0xc7`–`0xca` | PV1–PV4 power | int | Per-panel |
| `0xd5` | Grid to home | int | Watts |
| `0xfe` | Anti-replay timestamp | uint32 | Increments each packet |

#### C1000 (A1761) — 254/312 bytes, uint16 values

| ID | Name | Notes |
|----|------|-------|
| `0xa5`–`0xa9` | AC in/out, DC out, USB-C, USB-A | Power (W) |
| `0xab` | Battery percentage | % |
| `0xac` | Battery power | Signed (W) |
| `0xae` | Solar input | W |
| `0xaf`/`0xb0` | AC/DC switch state | 0=off, 1=on |
| `0xb3`/`0xba` | Temperature / Battery temp | /10 = Celsius |
| `0xb5`/`0xb6` | Battery cycles / health | 511 = no battery? |
| `0xbb` | AC output active | 0/1 |
| `0xcc` | DC output active | 0/1 |
| `0xd0` | Serial number | ASCII string |
| `0xd1` | Capacity | 1000 = 1000Wh |
| `0xd2` | Max AC input | 720W |
| `0xd7`/`0xd8` | AC/DC enabled | Config flags |
| `0xda` | Min SoC reserve | 50 = 50% |
| `0xc1`/`0xc3` | Max charge/discharge SoC | 100% |
| `0xfd` | Model name | "A1761_30Ah" |

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
    constants.ts    — UUIDs, ECDH key, negotiation packets, param maps, command codes
    crypto.ts       — ECDH P-256 key exchange, AES-CBC/GCM encrypt/decrypt
    packet.ts       — FF09 packet framing, checksum, parsing
    telemetry.ts    — TLV decoder with auto type-byte detection
    connection.ts   — BLE connection, negotiation, fragment reassembly
    utils.ts        — Hex conversion, byte manipulation
    types.ts        — TypeScript interfaces
  components/
    ConnectionPanel.vue   — Connect/disconnect with status
    TelemetryDisplay.vue  — Grouped telemetry grid
    CommandPanel.vue      — Quick commands + custom hex input
    CommandScanner.vue    — Brute-force command scanner
    LogViewer.vue         — TLV-annotated protocol log with copy
    RawPackets.vue        — Hex packet viewer
  App.vue           — Main app with tabbed interface
```

## Reverse Engineering

### Command Scanner
The Scanner tab sends commands across a configurable range (e.g., `0x4000`–`0x40FF`) and logs responses. This maps the command space without needing to decompile the app.

### Capture Decoder
Enable BLE HCI snoop logging on Android, use the Anker app, then decode with:
```bash
tshark -r btsnoop_hci.log \
  -Y "(btatt.opcode == 0x52 || btatt.opcode == 0x1b) && btatt.value contains ff:09" \
  -T fields -e frame.number -e frame.time_relative -e btatt.opcode -e btatt.value
```

### Key Findings
- The ECDH private key is hardcoded in the Anker app (`7dfbea61...`)
- TLV type byte `0x05` = IEEE 754 float32 (Solarbank uses floats for power values)
- C1000 uses uint16 (`0x02`) for the same logical fields
- The C1000 doesn't stream telemetry — it responds to `0x4040` status requests
- Solarbank sends 3 fragments (2×253B + 1 small) with sequence bytes; C1000 sends 2 fragments without
- Device public key is at the end of the cmd `0x21` response, after 3 prefix bytes (`00 a1 40`)
- Negotiation cmd `0x29` response contains device info (chip, firmware, serial) in TLV format

## Status

This is an active reverse engineering project. Both Solarbank 3 and C1000 connect, decrypt, and display telemetry successfully. C1000 control commands (AC/DC toggle, display, lights) are confirmed working.

**Working:**
- BLE connection with auto-retry
- ECDH key exchange + AES-CBC decryption
- TLV telemetry parsing with type-byte auto-detection
- C1000 control commands (AC, DC, display, lights)
- Solarbank 3 real-time telemetry monitoring
- Command scanner for discovering new commands

**Not yet implemented:**
- Solarbank control commands (not reversed by anyone yet)
- Setting write commands (min SoC, charge limits, timeouts)
- Multi-device simultaneous connection
- Capture decoder (decrypt btsnoop logs offline)

## Credits

- [flip-dots/SolixBLE](https://github.com/flip-dots/SolixBLE) — Python BLE library, protocol foundations
- [thomluther/AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE) — original Solarbank BLE project
- [flip-dots/HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) — Home Assistant integration

## License

MIT
