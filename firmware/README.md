# Firmware analysis inputs

`c1000_gen2/1.1.4.9/` contains the **A1763 C1000 Gen 2** vendor application
images recovered from the owner-authorized official update on 2026-09-28:
main **1.1.4.9**, radio **0.3.3.0**, and its six controller-package components.
They are update payloads, not a full device flash/NVS dump. No C2000 firmware
has been recovered.

The main-controller package was reassembled by chunk index, with duplicate
chunks required to match. Each component was decoded with the repeating CRC-8
XOR mask described in [the findings](../docs/firmware-findings.md). The radio
was independently checked against its embedded image hash and RSA-3072/PSS
signature. Its saved transfer includes 48 bytes after the signature sector.

`manifest.json` lists exact sizes, hashes, manifest version bytes and checksum
values. MainMcu CRC-16/MODBUS, MainBMS CRC-16/XMODEM and LCD byte sum match.
Both DSP containers have verified nested block CRCs; their whole-component
checks and SW2505PD's checksum remain unresolved. These are not all-purpose
integrity or update-acceptance guarantees.

The publication check found no matches for retained private configuration
identifiers in UTF-8/UTF-16 and no complete private PEM key. The radio includes
public certificate roots and PEM parser delimiter strings; a private-key
delimiter alone does not establish an embedded private key. Phone captures,
BLE session keys, account credentials and raw live-device logs remain private.

## Reproduce observations

The [offline replay tools](../tools/firmware_analysis/) default to this input
directory and reproduce **1,842 Gen 2 synthetic cases**. Their emulation uses
substituted hardware/services and cannot establish physical behavior.
See [setup and limits](../docs/firmware-analysis-reproduction.md).

Keep firmware inputs separate from live device configuration. The repository
provides no firmware-flashing command or tested replacement firmware.

## Original C1000 input

[`c1000_original/1.5.9/`](c1000_original/1.5.9/README.md) contains the untouched
public vendor A1761 OTA package and its five decoded controller/DSP/BMS images.
The downloaded bytes match the public size and MD5; the manifest records
SHA-256 and component integrity checks. DSP strings identify 230 V hardware.
The package has no listed radio image and does not match the tested station's
installed 1.5.1 controller. A separate replay covers **1,317 synthetic cases**;
its substitutions and unresolved container signature are documented there.

## Vendor notice

These are vendor firmware inputs. Copyright remains with the respective
firmware authors; the repository's software license does not grant rights to
vendor firmware. Embedded notices and unmodified application bytes are retained.
The accompanying analysis scripts and synthetic outputs are repository software.
