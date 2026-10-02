#!/usr/bin/env python3
"""Replay selected pure native string initializers from exact private images.

The images come from inspect_android_loader_carriers.py. Regenerate selection
from init-array relocations, EH-frame bounds and actual instructions. No Java,
JNI, Android, imported-function, guest-system-call or device callback executes.
Only whitelisted static names, addresses, counts and hashes are exported.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import stat
import struct
import sys
import zipfile

import capstone
import unicorn
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from unicorn import (
    Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_BLOCK, UC_HOOK_INTR,
    UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
)
from unicorn import arm64_const as registers

sys.dont_write_bytecode = True
BASE, STACK, END = 0x10000000, 0x30000000, 0x40000000
STACK_SIZE = 0x100000
DEPENDENCY = Path(__file__).with_name("inspect_android_loader_carriers.py")
EXPECTED_IMAGES = {
    "libexec.so": {
        "sha256": "b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57",
        "size": 1160588, "rx_end": 0xF0000, "selected": 49,
        "unique_init_functions": 63,
    },
    "libijmDataEncryption_arm64.so": {
        "sha256": "72ab38e97a6f5eaaada32eb82b82ec5224a5da90532f9d5f4c67988c46d00c3e",
        "size": 1171816, "rx_end": 0xAC000, "selected": 19,
        "unique_init_functions": 23,
    },
}
WHITELIST = (
    "ijiami.dat", "ijiami.ajm", "IJMDal.Data", "classes.dex",
    "dalvik/system/DexClassLoader", "dalvik/system/InMemoryDexClassLoader",
    "java/lang/ClassLoader", "java/nio/ByteBuffer",
    "com/ijm/dataencryption/DETool", "dowork",
    "(Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V",
    "([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V",
    "(Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;Z)Z",
)
PINNED_CIE = bytes.fromhex("1400000000000000017a5200017c1e011b0c1f0000000000")
UINT64 = 0xFFFFFFFFFFFFFFFF


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inside(address: int, size: int, ranges: list[tuple[int, int]]) -> bool:
    return size > 0 and any(lower <= address and address + size <= upper
                            for lower, upper in ranges)


def overlap(address: int, size: int, ranges: list[tuple[int, int]]) -> bool:
    return any(address < upper and lower < address + size for lower, upper in ranges)


def compact_writes(events: list[tuple[int, int]]) -> list[list[int]]:
    """Union writes as [image offset, byte length]; each range ends exclusively."""
    ranges = []
    for address, size in sorted(set(events)):
        end = address + size
        if ranges and address <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([address, end])
    return [[lower, upper - lower] for lower, upper in ranges]


def read_image(image: bytes, address: int, size: int,
               ranges: list[tuple[int, int]]) -> bytes:
    if not inside(address, size, ranges) or address + size > len(image):
        raise ValueError("Metadata read outside audited image ranges")
    return image[address:address + size]


def relocate(elf, image: bytes, ranges: list[tuple[int, int]],
             writable: list[tuple[int, int]]) -> tuple[bytearray, dict, dict, list]:
    table, size, stride = (elf.tags[key] for key in (7, 8, 9))
    if stride != 24 or not 0 < size <= 0x100000 or size % stride:
        raise ValueError("Unexpected RELA table shape")
    relocated = bytearray(image)
    relative = {}
    imported = []
    types = Counter()
    for offset in range(0, size, stride):
        target, info, addend = struct.unpack("<QQq", elf.read(table + offset, stride))
        kind, symbol = info & 0xFFFFFFFF, info >> 32
        if target % 8 or not inside(target, 8, writable):
            raise ValueError("Relocation target outside original RW segment")
        types[kind] += 1
        if kind == 1027:
            if symbol or target in relative:
                raise ValueError("Invalid or duplicate RELATIVE relocation")
            relative[target] = addend
            struct.pack_into("<Q", relocated, target, (BASE + addend) & UINT64)
        else:
            # Do not synthesize imports; accessing their unresolved slots fails.
            imported.append((target, target + 8))
    if overlap(table, size, imported):
        raise ValueError("Relocations overlap imported slots")
    read_image(image, table, size, ranges)
    return relocated, relative, dict(sorted(types.items())), sorted(imported)


def function_bounds(elf, image: bytes, rx_end: int) -> dict[int, tuple[int, int]]:
    headers = [segment for segment in elf.segments if segment[0] == 0x6474E550]
    if len(headers) != 1:
        raise ValueError("Expected one GNU EH-frame header")
    header = headers[0]
    address, length = header[3], header[6]
    raw = read_image(image, address, length, [(0, rx_end)])
    if raw[:4] != b"\x01\x1b\x03\x3b":
        raise ValueError("Unexpected EH-frame header encoding")
    count = struct.unpack_from("<I", raw, 8)[0]
    if not 0 < count <= 16384 or length != 12 + 8 * count:
        raise ValueError("Unexpected EH-frame search-table size")
    bounds = {}
    previous = -1
    for index in range(count):
        pc_delta, fde_delta = struct.unpack_from("<ii", raw, 12 + index * 8)
        start, fde = address + pc_delta, address + fde_delta
        if not 0 <= start < rx_end or start <= previous:
            raise ValueError("Invalid EH-frame search-table ordering")
        previous = start
        length, cie_delta = struct.unpack("<II", read_image(image, fde, 8, [(0, rx_end)]))
        if not 12 <= length <= 4096:
            raise ValueError("Invalid FDE length")
        read_image(image, fde, length + 4, [(0, rx_end)])
        cie = fde + 4 - cie_delta
        if read_image(image, cie, len(PINNED_CIE), [(0, rx_end)]) != PINNED_CIE:
            # Other functions may use an augmented personality CIE; the selected
            # initializer entries must use the exact supported zR CIE below.
            continue
        location, span = struct.unpack("<ii", read_image(image, fde + 8, 8, [(0, rx_end)]))
        actual_start = fde + 8 + location
        if actual_start != start or not 0 < span or start + span > rx_end:
            raise ValueError("Unexpected FDE initial-location/range")
        bounds[start] = (start, start + span)
    return bounds


def select_initializers(elf, image: bytes, relative: dict[int, int],
                        writable: list[tuple[int, int]], rx_end: int) -> tuple[list, dict]:
    start, size = elf.tags[25], elf.tags[27]
    if size % 8 or not 0 < size <= 8192 or not inside(start, size, writable):
        raise ValueError("Unexpected init-array bounds")
    bounds = function_bounds(elf, image, rx_end)
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    seen = set()
    selected, omitted = [], []
    nulls = 0
    for index, offset in enumerate(range(start, start + size, 8)):
        if offset not in relative:
            value = struct.unpack("<Q", read_image(image, offset, 8, writable))[0]
            if value == 0:
                nulls += 1
                continue
            raise ValueError("Init entry lacks an audited RELATIVE relocation")
        entry = relative[offset]
        if entry in seen:
            continue
        seen.add(entry)
        if entry not in bounds:
            raise ValueError("Initializer has no supported exact FDE bound")
        lower, upper = bounds[entry]
        raw = read_image(image, lower, upper - lower, [(0, rx_end)])
        instructions = list(engine.disasm(raw, lower))
        if not instructions or sum(instruction.size for instruction in instructions) != len(raw):
            raise ValueError("Initializer does not decode completely")
        reasons = set()
        for instruction in instructions:
            mnemonic = instruction.mnemonic
            if mnemonic == "bl" or mnemonic.startswith("blr"):
                reasons.add("contains_call")
            if mnemonic in ("svc", "hvc", "smc", "brk", "hlt"):
                reasons.add("contains_trap")
            if mnemonic == "b":
                target = int(instruction.op_str.removeprefix("#"), 0)
                if not lower <= target < upper:
                    reasons.add("external_direct_branch")
        row = {"init_array_index": index, "start": f"{lower:08x}",
               "end": f"{upper:08x}", "bytes": upper - lower}
        if reasons:
            row["reasons"] = sorted(reasons)
            omitted.append(row)
        else:
            selected.append(row)
    return selected, {"init_array_entries": size // 8, "null_entries": nulls,
                      "unique_functions": len(seen), "excluded_functions": omitted,
                      "fde_supported_function_count": len(bounds)}


def replay(image: bytes, selected: list, ranges: list[tuple[int, int]],
           writable: list[tuple[int, int]], imported: list[tuple[int, int]]) -> tuple[bytes, list]:
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, (len(image) + 4095) & ~4095)
    uc.mem_write(BASE, image)
    uc.mem_map(STACK, STACK_SIZE)
    uc.mem_map(END, 4096)
    image_ranges = [(BASE + lower, BASE + upper) for lower, upper in ranges]
    read_ranges = image_ranges + [(STACK, STACK + STACK_SIZE)]
    write_ranges = [(BASE + lower, BASE + upper) for lower, upper in writable]
    write_ranges.append((STACK, STACK + STACK_SIZE))
    imported_ranges = [(BASE + lower, BASE + upper) for lower, upper in imported]
    active = None
    blocks, reads, writes = 0, 0, []

    def deny_interrupt(*args):
        raise ValueError("Guest interrupt/system call refused")

    def check_block(u, address, size, context):
        nonlocal blocks
        blocks += 1
        if active is None or not BASE + active[0] <= address or address + size > BASE + active[1]:
            raise ValueError("Guest code left selected initializer")

    def check_read(u, access, address, size, value, context):
        nonlocal reads
        reads += 1
        if not inside(address, size, read_ranges) or overlap(address, size, imported_ranges):
            raise ValueError("Guest read outside valid image/stack or from unresolved import")

    def check_write(u, access, address, size, value, context):
        if not inside(address, size, write_ranges) or overlap(address, size, imported_ranges):
            raise ValueError("Guest write outside original RW/stack or into unresolved import")
        if BASE <= address < BASE + len(image):
            writes.append((address - BASE, size))

    uc.hook_add(UC_HOOK_INTR, deny_interrupt)
    uc.hook_add(UC_HOOK_BLOCK, check_block)
    uc.hook_add(UC_HOOK_MEM_READ, check_read)
    uc.hook_add(UC_HOOK_MEM_WRITE, check_write)
    results = []
    for row in selected:
        active = (int(row["start"], 16), int(row["end"], 16))
        blocks, reads, writes = 0, 0, []
        uc.mem_write(STACK, b"\0" * STACK_SIZE)
        for index in range(31):
            uc.reg_write(getattr(registers, f"UC_ARM64_REG_X{index}"), 0)
        for index in range(32):
            uc.reg_write(getattr(registers, f"UC_ARM64_REG_Q{index}"), 0)
        for name in ("NZCV", "FPCR", "FPSR"):
            uc.reg_write(getattr(registers, "UC_ARM64_REG_" + name), 0)
        uc.reg_write(registers.UC_ARM64_REG_CPACR_EL1, 3 << 20)
        uc.reg_write(registers.UC_ARM64_REG_SP, STACK + STACK_SIZE - 4096)
        uc.reg_write(registers.UC_ARM64_REG_X30, END)
        try:
            uc.emu_start(BASE + active[0], END, timeout=1000000, count=1000000)
        except (UcError, ValueError) as error:
            raise ValueError(f"Initializer {row['start']} failed; no result emitted") from error
        if uc.reg_read(registers.UC_ARM64_REG_PC) != END:
            raise ValueError("Initializer failed to return within its budget")
        results.append({**row, "passed": True, "blocks": blocks, "reads": reads,
                        "image_write_event_count": len(writes),
                        "distinct_image_write_sites": len(set(writes)),
                        "image_write_events_sha256": digest(b"".join(
                            struct.pack("<QI", address, size) for address, size in writes)),
                        "write_event_encoding": "ordered little-endian uint64 offset + uint32 size",
                        "write_ranges": compact_writes(writes)})
    return bytes(uc.mem_read(BASE, len(image))), results


def whitelisted_literals(image: bytes) -> dict:
    result = {}
    for value in WHITELIST:
        needle = value.encode() + b"\0"
        offsets, start = [], 0
        while True:
            found = image.find(needle, start)
            if found < 0:
                break
            # A whitelisted terminated literal occurrence, not an arbitrary dump.
            offsets.append(f"{found:08x}")
            start = found + len(needle)
        result[value] = offsets
    return result


def registration_metadata(image: bytes, ranges: list[tuple[int, int]]) -> dict:
    name, signature, function = struct.unpack("<QQQ", read_image(image, 0xB4008, 24, ranges))
    expected_name = "dowork"
    expected_signature = WHITELIST[-1]
    for pointer, expected in ((name, expected_name), (signature, expected_signature)):
        raw = expected.encode() + b"\0"
        if pointer < BASE or read_image(image, pointer - BASE, len(raw), ranges) != raw:
            raise ValueError("Unexpected static JNI registration name/signature")
    if function != BASE + 0x234B8:
        raise ValueError("Unexpected static JNI registration function pointer")
    return {"table_address": "000b4008", "name": expected_name,
            "signature": expected_signature, "function_address": "000234b8",
            "registration_or_function_executed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-apk", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    source_sha = digest(Path(__file__).read_bytes())
    dependency_sha = digest(DEPENDENCY.read_bytes())
    spec = importlib.util.spec_from_file_location("loader_carriers", DEPENDENCY)
    carrier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(carrier)
    apk = args.base_apk.read_bytes()
    if digest(apk) != carrier.APK_SHA256:
        raise ValueError("Wrong APK SHA256; no guest initializer executed")
    if stat.S_IMODE(args.images_dir.stat().st_mode) & 0o077:
        raise ValueError("Recovered image directory must have restricted mode700")
    result = {"schema_version": 1, "apk_sha256": carrier.APK_SHA256, "libraries": {},
              "selected_initializer_cases": 68, "pure_native_string_initializers_executed": True,
              "java_jni_android_callbacks_executed": False,
              "hardware_network_or_guest_syscalls": 0,
              "sdk_action_frame_recovered": False}
    prepared = []
    with zipfile.ZipFile(BytesIO(apk)) as archive:
        for name, expected in EXPECTED_IMAGES.items():
            entry = next(key for key in carrier.LIBRARIES if key.endswith("/" + name))
            raw = archive.read(entry)
            if digest(raw) != carrier.LIBRARIES[entry]:
                raise ValueError("Wrong packed library SHA256")
            path = args.images_dir / (name + ".virtual-memory.bin")
            if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError("Recovered image file must have restricted permissions")
            image = path.read_bytes()
            if len(image) != expected["size"] or digest(image) != expected["sha256"]:
                raise ValueError("Wrong recovered image SHA256/size; no guest initializer executed")
            elf = carrier.LoadElf(raw)
            writable = [(segment[3], segment[3] + segment[6]) for segment in elf.loads
                        if segment[1] & 2]
            ranges = [(0, expected["rx_end"])] + writable
            relocated, relative, relocation_types, imported = relocate(elf, image, ranges, writable)
            selected, selection = select_initializers(elf, image, relative, writable, expected["rx_end"])
            if len(selected) != expected["selected"] or selection["unique_functions"] != expected["unique_init_functions"]:
                raise ValueError("Initializer selection count changed; refused")
            prepared.append((name, raw, image, relocated, relative, relocation_types,
                             imported, selected, selection, ranges, writable))
    # Validate both complete input sets and selections before executing any case.
    for (name, raw, image, relocated, relative, relocation_types, imported,
         selected, selection, ranges, writable) in prepared:
        decoded, cases = replay(bytes(relocated), selected, ranges, writable, imported)
        library = {"packed_library_sha256": digest(raw), "recovered_image_sha256": digest(image),
                   "decoded_image_sha256": digest(decoded), "image_size": len(image),
                   "relative_relocations": len(relative),
                   "relocation_type_counts": relocation_types,
                   "unresolved_import_slots": len(imported), "read_ranges": ranges,
                   "write_ranges": writable, "selection": selection,
                   "selected_cases": len(cases), "cases": cases,
                   "whitelisted_terminated_literal_offsets": whitelisted_literals(decoded),
                   "action_literal_counts": {term: decoded.count(term.encode())
                                             for term in carrier.TARGETS}}
        if name == "libijmDataEncryption_arm64.so":
            library["static_jni_table"] = registration_metadata(decoded, ranges)
        result["libraries"][name] = library
    if digest(DEPENDENCY.read_bytes()) != dependency_sha:
        raise ValueError("Carrier dependency changed during replay")
    if digest(Path(__file__).read_bytes()) != source_sha:
        raise ValueError("Initializer source changed during replay")
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    manifest = {"schema_version": 1, "source_sha256": source_sha,
                "dependency_sha256": {DEPENDENCY.name: dependency_sha},
                "results_sha256": digest(encoded), "apk_sha256": carrier.APK_SHA256,
                "recovered_image_sha256": {name: expected["sha256"] for name, expected in EXPECTED_IMAGES.items()},
                "runtime": {"python": platform.python_version(),
                            "unicorn": unicorn.__version__, "capstone": capstone.__version__},
                "scope": "68 selected pure native string initializers; no Java/JNI/Android callbacks"}
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, content in (("android-loader-strings-results.json", encoded),
                          ("android-loader-strings-manifest.json",
                           (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())):
        path = args.output_dir / name
        path.write_bytes(content)
        path.chmod(0o600)
    print(json.dumps({"results_sha256": digest(encoded), "selected_initializer_cases": 68,
                      "java_jni_android_callbacks_executed": False,
                      "hardware_network_or_guest_syscalls": 0}))


if __name__ == "__main__":
    main()
