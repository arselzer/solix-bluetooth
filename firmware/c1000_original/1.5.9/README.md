# Original C1000 A1761 firmware: v1.5.9

These files come from an **unauthenticated public vendor OTA download** on
2026-09-30. They are update payloads, not a device flash dump or phone capture.
No firmware was installed during this investigation. The tested original
C1000 reports main version **1.5.1**, so this is a model-specific analysis
input, not an exact match for its installed controller.

## Provenance and checks

The [vendor package](https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2025/07/22/iot-admin/NMvH3hGsgGHRWgg5/A1761_AllPackets_V1.5.9_20250617_HighVoltage.bin)
was identified in [public A1761 update metadata](https://github.com/thomluther/anker-solix-api/discussions/221).
Its downloaded size **417860 bytes** and MD5
`ce53b545926bf5ce88e886e5da1b12dc` match that metadata. SHA-256 is
`a395b1763e82cbfa93c8125b35f460a5c86cfcc0e9754680953d4901c645c3f5`.
The original package remains byte-for-byte intact, including its 68 trailing
bytes; the trailer format and any enclosing signature are **not verified**.
Checksums establish reproducibility, not signing authority or update safety.

The component table contains five images:

| Decoded image | Bytes | Independently checked |
| --- | ---: | --- |
| MainMcu | 158720 | CRC-16/MODBUS; Cortex vector table; `1.5.9` string |
| dspACDC | 76800 | 74 nested block CRC-16/MODBUS values |
| dspDCDC | 88064 | 84 nested block CRC-16/MODBUS values |
| MainBMS | 51200 | CRC-16/XMODEM |
| SubBMS | 41984 | CRC-16/XMODEM |

The encoded payload's byte sum also matches. Whole-component DSP checks
remain unresolved. DSP build strings explicitly identify **230 V A1761**, TI
**TMS320F2800137**, and **V5110**. This package contains no separately listed
ESP32/radio image. Its name alone should not classify other OTA packages as
regional voltage variants.

The deterministic [manifest](manifest.json) records component hashes, offsets,
version bytes and checks. No complete private PEM key was found in these
components. No user account IDs, session keys, credentials or phone captures
were added to this directory.

## Reproduction

From the repository root:

```sh
python3 tools/firmware_analysis/extract_c1000_original.py --output /tmp/a1761-images
python3 -m unittest discover -s tools/firmware_analysis -p test_c1000_original_package.py -v
```

The extractor is offline and uses Python's standard library. See the
[network/controller investigation](../../../docs/c1000-legacy-network-investigation.md)
for the separate Unicorn replay and its substitutions. There is no flashing
tool or tested replacement firmware here.

## Vendor notice

Copyright remains with the respective firmware authors. Public availability
does not establish a redistribution license; the repository software license
does not grant rights to vendor firmware. Embedded notices and original
application bytes are retained. Analysis scripts and synthetic results are
repository software.
