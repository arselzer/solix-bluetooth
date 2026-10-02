#!/usr/bin/env python3
"""Inspect the exact retained APK's protected loaders offline.

Replay the three UPX stubs, stopping before recovered library code; separately
replay the protector's pure byte-remapping function. All guest system calls
operate on emulated memory; no Android/JNI/host calls occur.
Only selected metadata is written by default. Optional recovered virtual
images must stay private: they are neither reconstructed ELF files nor SDKs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import platform
import struct
import zipfile
import zlib

import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import unicorn
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_BLOCK, UC_HOOK_INTR
from unicorn.arm64_const import (
    UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1,
    UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
    UC_ARM64_REG_X8, UC_ARM64_REG_X30,
)

APK_SHA256 = "27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110"
ASSETS = {
    "assets/ijiami.dat": "56cb2bd0d8c6eea2aeced1b274c006be6b852309b5e267daa53a50641e43eb1c",
    "assets/ijiami.ajm": "ea68ea4fa5981f12e89651f50d637f5d4c80e24fe1700b41c11c2a02cc055960",
    "assets/IJMDal.Data": "358ac3092eaa68058739c497ff6cbb27d2cc54e3e92bddb1599fa57e96f5fa31",
}
LIBRARIES = {
    "assets/ijm_lib/arm64-v8a/libexec.so": "4fd13d12d64e6d451f5b567c173917225c1b1e2deb97abdeca10110b8dc4f379",
    "assets/ijm_lib/arm64-v8a/libexecmain.so": "82d35cb6e2e5f1918e5ef53aae83590f7257cbec29f60357155060eb883eafd9",
    "assets/libijmDataEncryption_arm64.so": "3dd7349af2d0ca697e7ab30a673231fa9ec4fd0b1cf13c3ef577cd7355c05032",
}
# Exact copied decompressor/helper regions for the pinned packed images. The
# intervening embedded UPX notices and allocation padding are not executable.
HEAP_CODE = {
    LIBRARIES["assets/ijm_lib/arm64-v8a/libexec.so"]:
        ((0x43390, 0x434b8), (0x43598, 0x438c4)),
    LIBRARIES["assets/ijm_lib/arm64-v8a/libexecmain.so"]:
        ((0x37c8, 0x38b8), (0x3998, 0x3cc4)),
    LIBRARIES["assets/libijmDataEncryption_arm64.so"]:
        ((0x4032c, 0x40454), (0x40534, 0x40860)),
}
STUB_CODE = {
    LIBRARIES["assets/ijm_lib/arm64-v8a/libexec.so"]:
        ((0x985a0, 0x986f8), (0x987b4, 0x98c84)),
    LIBRARIES["assets/ijm_lib/arm64-v8a/libexecmain.so"]:
        ((0x3f80, 0x40a0), (0x415c, 0x462c)),
    LIBRARIES["assets/libijmDataEncryption_arm64.so"]:
        ((0x62b68, 0x62cc0), (0x62d7c, 0x6324c)),
}
HANDOFF = {
    LIBRARIES["assets/ijm_lib/arm64-v8a/libexec.so"]: 0x72d80,
    LIBRARIES["assets/ijm_lib/arm64-v8a/libexecmain.so"]: 0xef38,
    LIBRARIES["assets/libijmDataEncryption_arm64.so"]: 0x1de74,
}
TARGETS = ("akiot.cloud_api", "akiot.device.invoke_action", "action_set_ac_params",
           "acInputDisableSwitch", "supportAcInputDisable")
BASE, HEAP, STACK, END = 0x10000000, 0x20000000, 0x30000000, 0x40000000
LIB_SIZE, HEAP_SIZE, STACK_SIZE = 0x1000000, 0x2000000, 0x100000
REGISTERS = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
             UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LoadElf:
    """Read program/dynamic tables without consulting protected section tables."""

    def __init__(self, data: bytes):
        self.data = data
        if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
            raise ValueError("Expected little-endian ARM64 ELF")
        offset = struct.unpack_from("<Q", data, 32)[0]
        stride, count = struct.unpack_from("<HH", data, 54)
        if stride != 56 or not 0 < count <= 64 or offset + stride * count > len(data):
            raise ValueError("Invalid program headers")
        self.segments = [struct.unpack_from("<IIQQQQQQ", data, offset + i * stride)
                         for i in range(count)]
        self.loads = [s for s in self.segments if s[0] == 1]
        for s in self.loads:
            if s[5] > s[6] or s[2] + s[5] > len(data) or s[3] + s[6] > LIB_SIZE:
                raise ValueError("Invalid or oversized load segment")
        dynamic = next(s for s in self.segments if s[0] == 2)
        self.tags = {}
        for position in range(dynamic[2], dynamic[2] + dynamic[5], 16):
            tag, value = struct.unpack_from("<qQ", data, position)
            if tag == 0:
                break
            self.tags[tag] = value

    def read(self, address: int, size: int) -> bytes:
        for s in self.loads:
            if s[3] <= address and address + size <= s[3] + s[5]:
                position = s[2] + address - s[3]
                return self.data[position:position + size]
        raise ValueError("Address outside file-backed LOAD segments")

    def symbols(self) -> list[dict]:
        # All three exact images have the traditional dynamic hash table.
        _, count = struct.unpack("<II", self.read(self.tags[4], 8))
        if self.tags[11] != 24 or not 0 < count < 100000:
            raise ValueError("Unexpected dynamic symbol table")
        strings = self.read(self.tags[5], self.tags[10])
        symbols = []
        for index in range(count):
            name, info, other, section, address, size = struct.unpack(
                "<IBBHQQ", self.read(self.tags[6] + index * 24, 24))
            end = strings.index(0, name)
            symbols.append({"name": strings[name:end].decode("utf-8"),
                            "address": address, "size": size, "type": info & 15,
                            "defined": section != 0})
        return symbols


def replay_unpacker(elf: LoadElf) -> tuple[dict, bytes]:
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for address, size in ((BASE, LIB_SIZE), (HEAP, HEAP_SIZE),
                          (STACK, STACK_SIZE), (END, 4096)):
        uc.mem_map(address, size)
    for s in elf.loads:
        uc.mem_write(BASE + s[3], elf.data[s[2]:s[2] + s[5]])
    uc.reg_write(UC_ARM64_REG_SP, STACK + STACK_SIZE - 4096)
    uc.reg_write(UC_ARM64_REG_X30, END)
    start = elf.tags[12]
    stop = next(s[3] + s[5] for s in elf.loads if s[3] <= start < s[3] + s[5])
    heap_next = HEAP
    heap_executable = False
    input_digest = sha256(elf.data)
    heap_code = HEAP_CODE[input_digest]
    stub_code = STUB_CODE[input_digest]
    tails = []
    report = {"entry": f"{start:08x}", "stub_end": f"{stop:08x}",
              "blocks": 0, "guest_syscalls": [], "stop": None}

    def inside(address: int, length: int, lower: int, upper: int) -> bool:
        return length > 0 and lower <= address and address + length <= upper

    def syscall(u, interrupt, context):
        nonlocal heap_next, heap_executable
        number = u.reg_read(UC_ARM64_REG_X8)
        address, length, protection, flags, fd, offset = [u.reg_read(r) for r in REGISTERS]
        event = {"number": number, "length": length}
        if number == 222:  # Guest mmap, never an operating-system mmap.
            if not 0 < length <= LIB_SIZE or fd != 0xffffffffffffffff or offset != 0:
                raise ValueError("Unexpected guest mmap")
            if flags == 0x32 and protection == 3:
                if not inside(address, length + 16, BASE, BASE + LIB_SIZE):
                    raise ValueError("Fixed guest mmap outside library memory")
                result = address
                # The stub restores its 12-byte final trampoline at this end.
                # At entry, its instructions are validated before permitting it.
                tails.append((address + length, address + length + 16))
                event["library_offset"] = f"{address - BASE:08x}"
            elif flags == 0x22 and protection == 3 and address == 0:
                result = heap_next
                heap_next += (length + 4095) & ~4095
                if heap_next > HEAP + HEAP_SIZE:
                    raise ValueError("Guest heap exhausted")
                event["heap_offset"] = f"{result - HEAP:08x}"
            else:
                raise ValueError("Unexpected guest mmap flags")
            u.mem_write(result, b"\0" * length)
            u.reg_write(UC_ARM64_REG_X0, result)
        elif number == 226:  # Guest mprotect: memory remains inside Unicorn.
            if protection != 5 or not (inside(address, length, BASE, BASE + LIB_SIZE)
                                       or inside(address, length, HEAP, HEAP + HEAP_SIZE)):
                raise ValueError("Unexpected guest protection range")
            if address == HEAP:
                if any(upper > length for lower, upper in heap_code):
                    raise ValueError("Copied stub code exceeds protected allocation")
                heap_executable = True
            u.reg_write(UC_ARM64_REG_X0, 0)
        elif number == 215:  # Guest munmap: no host memory is unmapped.
            if not inside(address, length, HEAP, heap_next):
                raise ValueError("Guest unmap outside allocated heap")
            heap_executable = False
            u.reg_write(UC_ARM64_REG_X0, 0)
        else:
            raise ValueError(f"Unexpected guest syscall {number}; refused")
        report["guest_syscalls"].append(event)

    def block(u, address, size, context):
        report["blocks"] += 1
        tail = next((lower for lower, upper in tails
                     if lower <= address and address + size <= upper - 4), None)
        if tail is not None and bytes(u.mem_read(tail, 12)) != bytes.fromhex(
                "010000d4e007cca860001fd6"):
            raise ValueError("Unexpected recovered unpacker trampoline")
        in_heap_code = heap_executable and any(
            HEAP + lower <= address and address + size <= HEAP + upper
            for lower, upper in heap_code)
        in_stub_code = any(BASE + lower <= address and address + size <= BASE + upper
                           for lower, upper in stub_code)
        if not (in_stub_code or in_heap_code or tail is not None):
            if address != BASE + HANDOFF[input_digest]:
                raise ValueError("Unexpected transfer outside unpacker code")
            report["stop"] = "before_recovered_library_code"
            report["handoff"] = f"{address - BASE:08x}"
            u.emu_stop()

    uc.hook_add(UC_HOOK_INTR, syscall)
    uc.hook_add(UC_HOOK_BLOCK, block)
    try:
        uc.emu_start(BASE + start, END, timeout=20000000, count=20000000)
    except UcError as error:
        raise ValueError(f"UPX replay failed at {uc.reg_read(UC_ARM64_REG_PC):x}") from error
    if report["stop"] != "before_recovered_library_code":
        raise ValueError("Replay reached budget/return without the expected handoff")
    image = bytes(uc.mem_read(BASE, max(s[3] + s[6] for s in elf.loads)))
    report.update(memory_image_size=len(image), memory_image_sha256=sha256(image),
                  host_syscalls=0, android_or_jni_code_executed=False,
                  recovered_library_code_executed_by_unpacker=False)
    return report, image


def native_metadata(elf: LoadElf, image: bytes) -> dict:
    symbols = elf.symbols()
    selected = [s for s in symbols if s["defined"] and s["type"] == 2 and s["name"] in
                ("JNI_OnLoad", "JNI_OnUnload", "getOpCode")]
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    exports = []
    for symbol in selected:
        address, size = symbol["address"], symbol["size"]
        instructions = list(cs.disasm(image[address:address + size], address))
        if sum(i.size for i in instructions) != size:
            raise ValueError("Selected recovered function does not decode fully")
        exports.append({"name": symbol["name"], "address": f"{address:08x}", "size": size,
                        "decoded_bytes": sum(i.size for i in instructions),
                        "direct_call_targets": [i.op_str[1:] for i in instructions
                                                if i.mnemonic == "bl"]})
    # Selected fixed strings only, never an arbitrary string dump.
    return {"selected_exports": exports,
            "selected_data_symbols": [{"name": s["name"], "address": f"{s['address']:08x}",
                                       "size": s["size"]} for s in symbols
                                      if s["defined"] and s["name"] == "ijm_vmp"],
            "action_literal_presence": {term: term.encode() in image for term in TARGETS}}


def opcode_permutation(image: bytes) -> dict:
    """Replay getOpCode's pure byte mapping; these are protector VM opcodes."""
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, (len(image) + 4095) & ~4095)
    uc.mem_write(BASE, image)
    uc.mem_map(END, 4096)
    values = []

    def deny_interrupt(u, interrupt, context):
        raise ValueError("Pure opcode mapping attempted a guest system call")

    def check_code(u, address, size, context):
        if not (BASE + 0x1f5c <= address and address + size <= BASE + 0x2fec):
            raise ValueError("Pure opcode mapping left the pinned function")

    uc.hook_add(UC_HOOK_INTR, deny_interrupt)
    uc.hook_add(UC_HOOK_BLOCK, check_code)
    for value in range(256):
        uc.reg_write(UC_ARM64_REG_X0, value)
        uc.reg_write(UC_ARM64_REG_X30, END)
        uc.emu_start(BASE + 0x1f5c, END, timeout=100000, count=1000)
        if uc.reg_read(UC_ARM64_REG_PC) != END:
            raise ValueError("Pure opcode mapping did not return")
        values.append(uc.reg_read(UC_ARM64_REG_X0) & 255)
    return {"cases": 256, "output_distinct_bytes": len(set(values)),
            "output_sha256": sha256(bytes(values)), "host_or_guest_syscalls": 0,
            "scope": "Protector VM byte remapping; not Anker station command opcodes"}


def carrier_metadata(data: bytes) -> dict:
    count = Counter(data)
    return {"size": len(data), "sha256": sha256(data),
            "entropy_bits_per_byte": round(-sum(n / len(data) * math.log2(n / len(data))
                                                 for n in count.values()), 4),
            "action_literal_presence": {term: term.encode() in data for term in TARGETS},
            "dex_magic_count": sum(data.count(f"dex\n0{i:02d}\0".encode()) for i in range(100)),
            "zip_local_header_count": data.count(b"PK\x03\x04")}


def embedded_empty_dex(data: bytes) -> dict:
    offset = 723854
    size, header, endian = struct.unpack_from("<III", data, offset + 32)
    dex = data[offset:offset + size]
    if dex[:8] != b"dex\n035\0" or size != 156 or header != 112 or endian != 0x12345678:
        raise ValueError("Unexpected embedded DEX")
    sha1_valid = hashlib.sha1(dex[32:]).digest() == dex[12:32]
    adler_valid = zlib.adler32(dex[12:]) & 0xffffffff == struct.unpack_from("<I", dex, 8)[0]
    table_counts = [struct.unpack_from("<I", dex, offset)[0] for offset in
                    (56, 64, 72, 80, 88, 96)]
    if not sha1_valid or not adler_valid or any(table_counts):
        raise ValueError("Embedded DEX is not the audited valid empty DEX")
    return {"file_offset": offset, "size": size, "sha256": sha256(dex),
            "sha1_valid": sha1_valid, "adler32_valid": adler_valid,
            "strings_types_prototypes_fields_methods_classes_counts": table_counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-apk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--private-images-dir", type=Path,
                        help="Optional restricted local directory; never publish its images")
    args = parser.parse_args()
    os.umask(0o077)
    data = args.base_apk.read_bytes()
    if sha256(data) != APK_SHA256:
        raise ValueError("Wrong APK SHA256; no guest instructions replayed")
    result = {"apk_sha256": APK_SHA256, "carriers": {}, "libraries": {},
              "unpacker_cases": 3, "hardware_or_network_access": False,
              "sdk_action_frame_recovered": False}
    if args.private_images_dir:
        args.private_images_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if args.private_images_dir.stat().st_mode & 0o077:
            raise ValueError("Private images directory must have mode 700")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for entry, expected in {**ASSETS, **LIBRARIES}.items():
            raw = archive.read(entry)
            if sha256(raw) != expected:
                raise ValueError("Unexpected selected asset SHA256")
            if entry in ASSETS:
                result["carriers"][entry] = carrier_metadata(raw)
                continue
            elf = LoadElf(raw)
            report, image = replay_unpacker(elf)
            report.update(input_sha256=expected, input_size=len(raw),
                          native_metadata=native_metadata(elf, image))
            if entry.endswith("libexec.so"):
                report["embedded_empty_dex"] = embedded_empty_dex(raw)
            if entry.endswith("libexecmain.so"):
                report["protector_opcode_mapping"] = opcode_permutation(image)
            result["libraries"][entry] = report
            if args.private_images_dir:
                path = args.private_images_dir / (Path(entry).name + ".virtual-memory.bin")
                path.write_bytes(image)
                path.chmod(0o600)
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    manifest = {"source_sha256": sha256(Path(__file__).read_bytes()),
                "results_sha256": sha256(encoded), "input_sha256": APK_SHA256,
                "runtime": {"python": platform.python_version(),
                            "unicorn": unicorn.__version__, "capstone": capstone.__version__},
                "scope": "3 UPX-only replays and 256 pure protector byte-mapping cases"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (("android-loader-carriers-results.json", encoded),
                          ("android-loader-carriers-manifest.json",
                           (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())):
        (args.output_dir / name).write_bytes(content)
    print(json.dumps({"results_sha256": sha256(encoded), "unpacker_cases": 3,
                      "protector_mapping_cases": 256, "host_syscalls": 0,
                      "hardware_or_network_access": False}))


if __name__ == "__main__":
    main()
