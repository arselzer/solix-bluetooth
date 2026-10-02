#!/usr/bin/env python3
"""Inspect a retained APK's SDK boundary without loading code or credentials.

Only the exact audited APK pair is accepted. Results contain binary metadata,
selected exports/call targets and fixed-string presence, never asset contents,
DEX strings, phone data or crypto material. This is static inspection, not an
instruction replay or a cloud/device protocol implementation.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import zipfile

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile


APK_SHA256 = {
    "base": "27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110",
    "arm64": "373aefb48f1384b6065000cfab7ab4b25c9969b441d2547788f8e1807bbed601",
}
LIB_SHA256 = {
    "libcrypto-security.so": "ad6e86ebe54341673a6180f9d2d124b47a401262e66d3148f059596854ec0ba3",
    "libecc-encryption.so": "20fba95954b6a6c24142a182b00b7d127c6b1677ee16c71dd8b5c4322ca74aea",
}
ACTION_NAMES = ("akiot.cloud_api", "akiot.device.invoke_action",
                "action_set_ac_params", "acInputDisableSwitch")
LOADER_ANCHORS = ("com.anker.charging.AnkerApplication", "com.ijm.dataencryption.DETool",
                  "instantiateClassLoader", "supportInstantiateClassLoader", "loadLibrary")
PROTECTED_ASSETS = ("assets/IJMDal.Data", "assets/InteGration_4.6.7.ttf",
                    "assets/ijm_lib/arm64-v8a/libexec.so",
                    "assets/ijm_lib/arm64-v8a/libexecmain.so",
                    "assets/libijmDataEncryption_arm64.so")
PRIMITIVES = ("aes_decrypt", "crypto_hkdf", "compute_ecdh", "ecdh_gen", "gen_pic_code_v1")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verified_apk(path: Path, kind: str) -> zipfile.ZipFile:
    data = path.read_bytes()
    if sha256(data) != APK_SHA256[kind]:
        raise ValueError(f"Wrong {kind} APK SHA256; no code inspected")
    return zipfile.ZipFile(BytesIO(data))


def dex_inventory(data: bytes) -> dict:
    if len(data) != 13892 or data[:8] != b"dex\n038\0":
        raise ValueError("Unexpected loader DEX")
    count, offset = struct.unpack_from("<II", data, 0x38)
    strings = []
    for index in range(count):
        position = struct.unpack_from("<I", data, offset + index * 4)[0]
        # The leading ULEB128 is UTF-16 length. We only compare fixed ASCII
        # anchors; this is not a general MUTF-8 decoder or DEX decompiler.
        while data[position] & 128:
            position += 1
        position += 1
        end = data.index(0, position)
        strings.append(data[position:end])
    return {
        "size": len(data), "sha256": sha256(data), "string_count": count,
        "loader_anchor_presence": {name: name.encode() in strings for name in LOADER_ANCHORS},
        "action_literal_presence": {name: name.encode() in strings for name in ACTION_NAMES},
        "scope": "Literal presence in retained clear loader DEX only",
    }


def native_inventory(name: str, data: bytes) -> dict:
    if sha256(data) != LIB_SHA256[name]:
        raise ValueError("Unexpected native library SHA256")
    elf = ELFFile(BytesIO(data))
    symbols = list(elf.get_section_by_name(".dynsym").iter_symbols())
    by_name = {symbol.name: symbol for symbol in symbols}
    names = {symbol["st_value"]: symbol.name for symbol in symbols if symbol["st_value"]}
    segments = [(segment["p_vaddr"], segment.data()) for segment in elf.iter_segments()
                if segment["p_type"] == "PT_LOAD"]

    def read(address: int, size: int) -> bytes:
        for start, content in segments:
            if start <= address and address + size <= start + len(content):
                return content[address - start:address - start + size]
        raise ValueError("Native address outside loaded segments")

    plt = elf.get_section_by_name(".plt")
    relocations = elf.get_section_by_name(".rela.plt")
    dynsym = elf.get_section(relocations["sh_link"])
    for index, relocation in enumerate(relocations.iter_relocations()):
        names[plt["sh_addr"] + 32 + 16 * index] = dynsym.get_symbol(relocation["r_info_sym"]).name

    def inspect_symbol(symbol) -> dict:
        calls = []
        for instruction in Cs(CS_ARCH_ARM64, CS_MODE_ARM).disasm(
                read(symbol["st_value"], symbol["st_size"]), symbol["st_value"]):
            if instruction.mnemonic == "bl" and instruction.op_str.startswith("#0x"):
                target = int(instruction.op_str[1:], 16)
                calls.append({"at": f"{instruction.address:08x}", "target": f"{target:08x}",
                              "symbol": names.get(target, "local")})
        return {"name": symbol.name, "address": f"{symbol['st_value']:08x}",
                "size": symbol["st_size"], "direct_calls": calls}

    report = {
        "sha256": sha256(data), "size": len(data),
        "jni_exports": [inspect_symbol(symbol) for symbol in symbols
                        if symbol.name.startswith("Java_com_anker_esiotkit_support_crypto_")],
    }
    if name == "libcrypto-security.so":
        report["selected_primitives"] = [inspect_symbol(by_name[name]) for name in PRIMITIVES]
        sizes = struct.unpack("<III", read(0xBA04C, 12))
        rel = elf.get_section_by_name(".rela.dyn")
        dynsym = elf.get_section(rel["sh_link"])
        table = {item["r_offset"]: item for item in rel.iter_relocations()}
        choices = []
        for index, size in enumerate(sizes):
            relocation = table[0x2763D8 + 8 * index]
            if relocation["r_info_type"] != 257:
                raise ValueError("Unexpected digest table relocation")
            choices.append({"selector": index, "digest_bytes": size,
                            "md_info_symbol": dynsym.get_symbol(relocation["r_info_sym"]).name})
        report["hkdf_digest_choices"] = choices
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-apk", type=Path, required=True)
    parser.add_argument("--arm64-apk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    with verified_apk(args.base_apk, "base") as base, verified_apk(args.arm64_apk, "arm64") as arm64:
        result = {
            "apk_sha256": APK_SHA256,
            "clear_dex_entries": [name for name in base.namelist() if name.endswith(".dex")],
            "loader_dex": dex_inventory(base.read("classes.dex")),
            "protected_asset_sizes": {name: base.getinfo(name).file_size for name in PROTECTED_ASSETS},
            "native_libraries": {name: native_inventory(name, arm64.read("lib/arm64-v8a/" + name))
                                 for name in LIB_SHA256},
            "code_executed": False, "hardware_or_network_access": False,
            "cloud_envelope_or_action_frame_recovered": False,
        }
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    manifest = {"source_sha256": sha256(Path(__file__).read_bytes()),
                "results_sha256": sha256(encoded), "inputs_sha256": APK_SHA256,
                "scope": "Static metadata and selected instruction-call inventory; no emulation cases"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (("android-sdk-boundary-results.json", encoded),
                          ("android-sdk-boundary-manifest.json",
                           (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())):
        (args.output_dir / name).write_bytes(content)
    print(json.dumps({"results_sha256": sha256(encoded), "code_executed": False,
                      "hardware_or_network_access": False}))


if __name__ == "__main__":
    main()
