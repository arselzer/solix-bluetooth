#!/usr/bin/env python3
"""Verify/extract the public A1761 v1.5.9 package offline; never flash a device."""

import argparse
import binascii
import hashlib
import json
from pathlib import Path
import re
import struct

from extract_dsp import parse as parse_dsp


FILENAME = "A1761_AllPackets_V1.5.9_20250617_HighVoltage.bin"
SIZE = 417860
MD5 = "ce53b545926bf5ce88e886e5da1b12dc"
SHA256 = "a395b1763e82cbfa93c8125b35f460a5c86cfcc0e9754680953d4901c645c3f5"
SOURCE_URL = "https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2025/07/22/iot-admin/NMvH3hGsgGHRWgg5/" + FILENAME
COMPONENTS = ("MainMcu", "dspACDC", "dspDCDC", "MainBMS", "SubBMS")
DEFAULT_INPUT = Path(__file__).resolve().parents[2] / "firmware/c1000_original/1.5.9" / FILENAME


def crc8(value: int) -> int:
    for _ in range(8):
        value = ((value << 1) ^ (0x31 if value & 0x80 else 0)) & 255
    return value


def crc16_modbus(data: bytes) -> int:
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value


def inspect(package: bytes) -> tuple[dict, dict[str, bytes]]:
    """Check this container's structure/checksums, independently of its file hash."""
    if len(package) != SIZE:
        raise ValueError("Incorrect package length")
    extent, check = struct.unpack_from("<II", package)
    if extent != 0x66000 or package[8:12] != bytes((1, 5, 9, 255)):
        raise ValueError("Unexpected container extent/version")
    if package[0x3f0:0x3f6] != b"A1761\0":
        raise ValueError("Incorrect product marker")
    if sum(package[0x400:extent]) & 0xffffffff != check:
        raise ValueError("Encoded-payload byte sum mismatch")
    mask = bytes(map(crc8, range(256)))
    images = {}
    records = []
    expected_offset = 0x400
    for index, expected_name in enumerate(COMPONENTS):
        offset, size, checksum, version, raw_name = struct.unpack_from("<III4s16s", package, 12 + 32 * index)
        if raw_name != expected_name.encode().ljust(16, b"\0"):
            raise ValueError("Unexpected component name/order")
        if offset != expected_offset or size == 0 or offset + size > extent:
            raise ValueError("Invalid component extent")
        expected_offset = offset + size
        data = bytes(value ^ mask[i % 256] for i, value in enumerate(package[offset:expected_offset]))
        record = {
            "name": expected_name,
            "filename": expected_name + "-decoded.bin",
            "offset": offset,
            "bytes": size,
            "version_bytes": list(version),
            "sha256": hashlib.sha256(data).hexdigest(),
            "manifest_check": checksum,
        }
        if expected_name == "MainMcu":
            actual = crc16_modbus(data)
            record.update(check_algorithm="CRC-16/MODBUS", computed_check=actual)
        elif expected_name.endswith("BMS"):
            actual = binascii.crc_hqx(data, 0)
            record.update(check_algorithm="CRC-16/XMODEM", computed_check=actual)
        else:
            blocks = parse_dsp(data)
            actual = checksum  # Whole-component check remains unresolved.
            record.update(dsp_block_count=len(blocks), all_dsp_block_crc16_verified=True,
                          whole_component_check_verified=False,
                          dsp_payload_octets=sum(block["size_octets"] for block in blocks))
        if actual != checksum:
            raise ValueError(f"{expected_name}: component checksum mismatch")
        record["complete_private_pem_keys"] = len(re.findall(
            rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", data
        ))
        if expected_name == "MainMcu":
            record.update(vector_initial_sp=f"{struct.unpack_from('<I', data)[0]:08x}",
                          vector_reset=f"{struct.unpack_from('<I', data, 4)[0]:08x}",
                          inferred_load_address="08005000")
        records.append(record)
        images[expected_name] = data
    if expected_offset != extent or package[172:204] != b"\xff" * 32:
        raise ValueError("Unexpected component terminator/end")
    manifest = {
        "model": "A1761", "main_version": "1.5.9",
        "source_url": SOURCE_URL,
        "metadata_source": "https://github.com/thomluther/anker-solix-api/discussions/221",
        "source_kind": "Unauthenticated public vendor OTA download; not a device dump or phone capture",
        "package": {"filename": FILENAME, "bytes": len(package),
                    "md5": hashlib.md5(package).hexdigest(), "sha256": hashlib.sha256(package).hexdigest(),
                    "encoded_payload_sum32": check, "encoded_payload_sum_verified": True,
                    "declared_extent": extent, "uninterpreted_trailer_bytes": len(package) - extent,
                    "trailer_sha256": hashlib.sha256(package[extent:]).hexdigest(),
                    "signature_verified": False},
        "component_xor_mask": "CRC-8 polynomial 0x31, initial 0, input byte 0..255; repeat 256 bytes per component",
        "files": records,
    }
    return manifest, images


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = args.input.read_bytes()
    if hashlib.sha256(package).hexdigest() != SHA256 or hashlib.md5(package).hexdigest() != MD5:
        parser.error("Firmware hash differs from the documented vendor input")
    manifest, images = inspect(package)
    outputs = {name + "-decoded.bin": data for name, data in images.items()}
    outputs["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, data in outputs.items():
        destination = args.output / filename
        if destination.exists() and destination.read_bytes() != data:
            parser.error(f"Refusing to overwrite differing existing output: {filename}")
    for filename, data in outputs.items():
        (args.output / filename).write_bytes(data)
    print("Verified main + 2 BMS checksums, outer payload sum, and 158 DSP block checksums; extracted 5 components")


if __name__ == "__main__":
    main()
