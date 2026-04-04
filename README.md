# Anker Solix Bluetooth

A web app for local Bluetooth control and monitoring of the Anker Solix Solarbank 3 E2700 Pro, without requiring the Anker app or cloud account.

Built with Vue 3, TypeScript, and the Web Bluetooth API. Based on protocol reverse engineering from the [SolixBLE](https://github.com/flip-dots/SolixBLE) project.

## Features

- **Local BLE connection** — connects directly to the Solarbank via Bluetooth, no cloud or account needed
- **Encrypted communication** — implements the full ECDH key exchange and AES-CBC encryption protocol
- **Live telemetry** — decodes and displays real-time device data including:
  - Solar input power (per-panel PV1–PV4)
  - Battery percentage, health, and charge/discharge power
  - House demand and consumption
  - Grid power, import, and export
  - Temperature
- **Protocol log** — color-coded log of all negotiation steps, encrypted/decrypted packets, and errors
- **Raw packet viewer** — hex dump of all TX/RX BLE packets for reverse engineering

## How It Works

### BLE Protocol

The Solarbank 3 Pro uses a custom BLE GATT service with encrypted binary communication:

| Component | UUID |
|-----------|------|
| Service | `8c850001-0302-41c5-b46e-cf057c562025` |
| Write (app → device) | `8c850002-0302-41c5-b46e-cf057c562025` |
| Notify (device → app) | `8c850003-0302-41c5-b46e-cf057c562025` |

### Connection Flow

1. **Device discovery** — filters by BLE name prefix "Solarbank"
2. **GATT connect** — connects to the service and subscribes to notifications
3. **ECDH key exchange** — 5-stage negotiation using P-256 curve to establish a shared secret
4. **AES-CBC encryption** — all subsequent data is encrypted with the derived key (first 16 bytes) and IV (bytes 16–31)
5. **Telemetry streaming** — device sends encrypted telemetry every ~3 seconds as fragmented BLE packets (large + small), which are reassembled and decrypted

### Packet Format

All packets follow this structure:

```
[FF09] [Length 2B LE] [Pattern 3B] [Command 2B] [Payload nB] [Checksum 1B]
```

- `030001` pattern = encryption negotiation
- `03010f` pattern = encrypted session data
- Checksum = XOR of all preceding bytes

### Telemetry Parsing

After decryption, telemetry uses a Tag-Length-Value (TLV) format:

```
[ParamID 1B] [Length 1B] [Data nB] ...
```

Each parameter's data has a leading type byte that is skipped before parsing the value (little-endian integers).

## Requirements

- **Browser**: Chrome or Edge (Web Bluetooth API required)
- **HTTPS**: Web Bluetooth only works on secure origins (localhost works for development)
- **Proximity**: BLE range, typically a few meters from the Solarbank

## Getting Started

```bash
npm install
npm run dev
```

Open `http://localhost:5173` in Chrome, click **Connect**, and select your Solarbank from the BLE device picker. You may need to press the IoT button on the device to enable BLE discovery.

## Project Structure

```
src/
  protocol/
    constants.ts    — BLE UUIDs, ECDH key, negotiation packets, telemetry param IDs
    crypto.ts       — ECDH P-256 key exchange, AES-CBC/GCM encrypt/decrypt
    packet.ts       — FF09 packet framing, checksum, parsing
    telemetry.ts    — TLV parameter decoder for Solarbank 3 Pro (A17C5)
    connection.ts   — BLE connection manager, negotiation state machine, packet reassembly
    utils.ts        — Hex conversion, byte manipulation, checksums
    types.ts        — TypeScript interfaces
  components/
    ConnectionPanel.vue   — Connect/disconnect with status indicator
    TelemetryDisplay.vue  — Grid of decoded telemetry values
    LogViewer.vue         — Protocol event log with auto-scroll
    RawPackets.vue        — Hex packet viewer
  App.vue           — Main app with tabbed interface
```

## Status

This is an early-stage reverse engineering project. The encryption handshake and telemetry decoding are implemented based on the SolixBLE Python project, but have not yet been fully tested against a live device. The negotiation commands use hardcoded values extracted from the Anker app — if Anker changes these in a firmware update, they may need to be updated.

**What works:**
- BLE connection and service discovery
- Packet framing and checksum validation
- ECDH key exchange structure
- AES-CBC decryption
- TLV telemetry parsing with all known Solarbank 3 Pro parameters

**What's not yet implemented:**
- Control commands (setting output limits, charge schedules, etc.) — SolixBLE hasn't reverse-engineered these for Solarbank either
- Multi-device support
- Persistent connection recovery

## Credits

- [flip-dots/SolixBLE](https://github.com/flip-dots/SolixBLE) — Python BLE library this is based on
- [thomluther/AnkerSolixBLE](https://github.com/thomluther/AnkerSolixBLE) — original Solarbank BLE monitoring project
- [flip-dots/HaSolixBLE](https://github.com/flip-dots/HaSolixBLE) — Home Assistant integration

## License

MIT
