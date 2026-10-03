#!/usr/bin/env python3
"""Prove a narrow protected JNI wrapper boundary by static parsing only.

Use the exact retained APK and libexec virtual image from the carrier tool.
Apply only ELF RELATIVE relocations; do not execute an initializer, JNI entry,
protector interpreter, native call thunk, Android method or device operation.
Output contains fixed addresses, selected token metadata and hashes, never
raw APK/code/bytecode records, arbitrary strings or private identifiers.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys
import zipfile

sys.dont_write_bytecode = True
import capstone
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import inspect_android_loader_carriers as carrier
import inspect_android_loader_strings as strings

BASE = 0x10000000
IMAGE_NAME = "libexec.so.virtual-memory.bin"
IMAGE_SHA = "b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57"
IMAGE_SIZE = 1160588
LIBRARY = "assets/ijm_lib/arm64-v8a/libexec.so"
RX_END = 0xF0000
FUNCTIONS = (0x72E74, 0xE2E00, 0xE2E10, 0xBD624, 0xE7FC4, 0x848CC,
             0x72DFC, 0x55218, 0x55314, 0x553DC, 0x54A60)
TOKEN_SPECS = (
    (4, 0x2C, 1, 0xBD760, "reserve_zero_words"),
    (6, 0x4C, 2, 0xBEFC0, "push_call_table_pointer"),
    (9, 0x39, 2, 0xC39C8, "push_local_pointer"),
    (12, 0x39, 2, 0xC39C8, "push_local_pointer"),
    (15, 0x4D, 1, 0xC3768, "call_native_thunk"),
)
# Selected exact Capstone checks; whole inputs are SHA-pinned.
INSTRUCTION_CHECKS = {
    'jni_entry_selector_zero': [
        (0x72E84, 'mov', 'x19, x1'),
        (0x72E88, 'mov', 'x20, x0'),
        (0x72E94, 'mov', 'x21, x0'),
        (0x72E98, 'bl', '#0xe2e10'),
        (0x72E9C, 'mov', 'x0, x21'),
        (0x72EA0, 'mov', 'x1, x19'),
        (0x72EA4, 'bl', '#0xe2e10'),
        (0x72EA8, 'adrp', 'x1, #0x113000'),
        (0x72EAC, 'adrp', 'x2, #0x113000'),
        (0x72EB0, 'adrp', 'x3, #0x113000'),
        (0x72EB4, 'adrp', 'x4, #0x113000'),
        (0x72EB8, 'add', 'x1, x1, #0x5b0'),
        (0x72EBC, 'add', 'x2, x2, #0x5d0'),
        (0x72EC0, 'add', 'x3, x3, #0x5f0'),
        (0x72EC4, 'add', 'x4, x4, #0x790'),
        (0x72EC8, 'mov', 'x0, x21'),
        (0x72ECC, 'mov', 'w5, wzr'),
        (0x72ED0, 'bl', '#0xe2e00'),
    ],
    'descriptor_selector': [
        (0xE2E00, 'lsl', 'w8, w5, #1'),
        (0xE2E04, 'ldrsw', 'x8, [x4, w8, sxtw #2]'),
        (0xE2E08, 'add', 'x3, x3, x8'),
        (0xE2E0C, 'b', '#0xbd624'),
    ],
    'push_original_pointer': [
        (0xE2E10, 'ldr', 'x8, [x0, #8]'),
        (0xE2E14, 'str', 'x1, [x8], #8'),
        (0xE2E18, 'str', 'x8, [x0, #8]'),
        (0xE2E1C, 'ret', ''),
    ],
    'header_transform': [
        (0xBD67C, 'ldrb', 'w8, [x20, #1]'),
        (0xBD698, 'ldrb', 'w9, [x20, #2]'),
        (0xBD69C, 'ldrb', 'w11, [x20, #3]'),
        (0xBD6A0, 'eor', 'w8, w8, w12'),
        (0xBD6A4, 'sub', 'w14, w9, #0x69'),
        (0xBD6A8, 'sub', 'w15, w11, #0x69'),
        (0xBD6AC, 'and', 'w14, w14, w13'),
        (0xBD6B0, 'and', 'w13, w15, w13'),
        (0xBD6B8, 'sub', 'w9, w10, w9'),
        (0xBD6BC, 'sub', 'w11, w10, w11'),
        (0xBD6C0, 'and', 'w9, w9, w12'),
        (0xBD6C4, 'and', 'w11, w11, w12'),
        (0xBD6C8, 'orr', 'w9, w9, w14'),
        (0xBD6D0, 'orr', 'w11, w11, w13'),
        (0xBD6D4, 'eor', 'w9, w9, w8'),
        (0xBD6D8, 'eor', 'w11, w11, w8'),
        (0xBD6DC, 'and', 'x11, x11, #0xff'),
        (0xBD6E0, 'and', 'x9, x9, #0xff'),
        (0xBD6E4, 'bfi', 'x9, x11, #8, #8'),
        (0xBD6E8, 'sub', 'x9, x14, x9, lsl #2'),
        (0xBD6F0, 'ldrb', 'w9, [x20, #4]'),
        (0xBD704, 'add', 'x11, x11, #4'),
        (0xBD70C, 'ldr', 'x11, [sp, #0x10]'),
        (0xBD728, 'ldr', 'x10, [sp]'),
        (0xBD72C, 'eor', 'w8, w9, w8'),
        (0xBD738, 'strb', 'w8, [sp, #0x82c]'),
        (0xBD73C, 'str', 'x13, [sp, #0x820]'),
        (0xBD744, 'strb', 'w8, [sp, #0x81c]'),
        (0xBD750, 'add', 'x19, x19, #0xaf0'),
        (0xBD754, 'ldr', 'x9, [x19, x8, lsl #3]'),
        (0xBD75C, 'br', 'x9'),
    ],
    'first_token_width_and_rolling_key': [
        (0xBD77C, 'ldrb', 'w8, [x8]'),
        (0xBD780, 'lsl', 'x8, x8, #2'),
        (0xBD7C4, 'ldr', 'x0, [sp, #0x50]'),
        (0xBD7CC, 'mov', 'w1, wzr'),
        (0xBD7D0, 'bl', '#0xeea50'),
        (0xBD7EC, 'ldr', 'x8, [sp, #0x820]'),
        (0xBD7F0, 'ldrb', 'w8, [x8]'),
        (0xBD7FC, 'ldr', 'x10, [sp, #8]'),
        (0xBD800, 'add', 'x8, x9, x8, lsl #2'),
        (0xBD804, 'str', 'x8, [x10]'),
        (0xBD808, 'ldr', 'x8, [sp, #0x820]'),
        (0xBD80C, 'ldrb', 'w9, [sp, #0x82c]'),
        (0xBD818, 'ldrb', 'w8, [x8, #1]'),
        (0xBD834, 'cmp', 'w8, #0x3b'),
        (0xBD854, 'ldrb', 'w8, [sp, #0x6c]'),
        (0xBD858, 'ldrb', 'w9, [sp, #0x6c]'),
        (0xBD85C, 'mov', 'w10, #0x68'),
        (0xBD860, 'ldrb', 'w13, [sp, #0x81c]'),
        (0xBD864, 'mov', 'w11, #0x5f'),
        (0xBD868, 'mov', 'w12, #-0x60'),
        (0xBD86C, 'sub', 'w8, w8, #0x69'),
        (0xBD870, 'sub', 'w9, w10, w9'),
        (0xBD874, 'and', 'w9, w9, w11'),
        (0xBD878, 'and', 'w8, w8, w12'),
        (0xBD87C, 'orr', 'w8, w9, w8'),
        (0xBD880, 'eor', 'w8, w13, w8'),
        (0xBD884, 'eor', 'w8, w8, w11'),
        (0xBD888, 'mov', 'w9, w8'),
        (0xBD898, 'strb', 'w8, [sp, #0x70]'),
        (0xBD89C, 'ldr', 'x10, [sp, #0x820]'),
        (0xBD8A8, 'add', 'x10, x10, #2'),
        (0xBD8AC, 'str', 'x10, [sp, #0x80]'),
        (0xBD8C4, 'strb', 'w8, [sp, #0x82c]'),
        (0xBD8C8, 'strb', 'w9, [sp, #0x81c]'),
        (0xBD8CC, 'str', 'x10, [sp, #0x820]'),
        (0xBD8D4, 'ldr', 'x9, [x19, x8, lsl #3]'),
    ],
    'call_table_pointer_token': [
        (0xBEFC0, 'ldr', 'x8, [sp, #0x820]'),
        (0xBEFC4, 'ldrb', 'w8, [x8]'),
        (0xBEFCC, 'ldrb', 'w9, [x9, #1]'),
        (0xBEFD0, 'ldr', 'x10, [sp, #0x20]'),
        (0xBEFD8, 'bfi', 'w8, w9, #8, #0x18'),
        (0xBEFDC, 'add', 'x8, x10, w8, sxth #3'),
        (0xBEFE4, 'ldr', 'w10, [x8]'),
        (0xBEFE8, 'str', 'w10, [x9]'),
        (0xBEFEC, 'ldr', 'w8, [x8, #4]'),
        (0xBEFF4, 'str', 'w8, [x9, #4]'),
        (0xBEFFC, 'add', 'x9, x9, #8'),
        (0xBF000, 'str', 'x9, [x8]'),
        (0xBF00C, 'ldrb', 'w8, [x8, #2]'),
        (0xBF060, 'sub', 'w8, w8, #0x69'),
        (0xBF064, 'sub', 'w9, w10, w9'),
        (0xBF068, 'and', 'w9, w9, w11'),
        (0xBF06C, 'and', 'w8, w8, w12'),
        (0xBF070, 'orr', 'w8, w9, w8'),
        (0xBF074, 'eor', 'w8, w13, w8'),
        (0xBF078, 'eor', 'w8, w8, w11'),
        (0xBF09C, 'add', 'x10, x10, #3'),
    ],
    'local_pointer_token': [
        (0xC39CC, 'ldrb', 'w8, [x8]'),
        (0xC39D4, 'ldrb', 'w9, [x9, #1]'),
        (0xC39DC, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC39E0, 'sbfiz', 'x8, x8, #2, #0x10'),
        (0xC39E4, 'ldr', 'w9, [x10, x8]'),
        (0xC39F0, 'str', 'w9, [x10]'),
        (0xC39F4, 'ldr', 'x9, [sp, #0x28]'),
        (0xC39F8, 'ldr', 'w8, [x9, x8]'),
        (0xC39FC, 'add', 'x9, x10, #8'),
        (0xC3A00, 'str', 'w8, [x10, #4]'),
        (0xC3A18, 'ldrb', 'w8, [x8, #2]'),
        (0xC3AA8, 'add', 'x10, x10, #3'),
    ],
    'native_thunk_token': [
        (0xC3768, 'ldr', 'x20, [sp, #0x820]'),
        (0xC3770, 'ldrb', 'w8, [x8]'),
        (0xC3774, 'ldr', 'x9, [sp, #0x18]'),
        (0xC3778, 'ldr', 'x8, [x9, x8, lsl #3]'),
        (0xC377C, 'ldr', 'x0, [sp]'),
        (0xC3780, 'blr', 'x8'),
        (0xC3788, 'ldrb', 'w9, [x20, #1]'),
        (0xC37E0, 'sub', 'w8, w8, #0x69'),
        (0xC37E4, 'sub', 'w9, w10, w9'),
        (0xC37E8, 'and', 'w9, w9, w11'),
        (0xC37EC, 'and', 'w8, w8, w12'),
        (0xC37F0, 'orr', 'w8, w9, w8'),
        (0xC37F4, 'eor', 'w8, w13, w8'),
        (0xC37F8, 'eor', 'w8, w8, w11'),
        (0xC381C, 'b', '#0xbd8a8'),
    ],
    'two_argument_native_thunk': [
        (0xE7FCC, 'ldr', 'x20, [x0]'),
        (0xE7FD0, 'mov', 'x19, x0'),
        (0xE7FD4, 'sub', 'x8, x20, #4'),
        (0xE7FD8, 'sub', 'x9, x20, #8'),
        (0xE7FDC, 'str', 'x8, [x0]'),
        (0xE7FE0, 'sub', 'x10, x20, #0xc'),
        (0xE7FE4, 'ldur', 'w8, [x20, #-4]'),
        (0xE7FE8, 'str', 'x9, [x0]'),
        (0xE7FEC, 'sub', 'x11, x20, #0x10'),
        (0xE7FF0, 'ldur', 'w1, [x20, #-8]'),
        (0xE7FF4, 'str', 'x10, [x0]'),
        (0xE7FF8, 'sub', 'x21, x20, #0x14'),
        (0xE7FFC, 'ldur', 'w9, [x20, #-0xc]'),
        (0xE8000, 'str', 'x11, [x0]'),
        (0xE8004, 'sub', 'x12, x20, #0x18'),
        (0xE8008, 'ldur', 'w0, [x20, #-0x10]'),
        (0xE800C, 'str', 'x21, [x19]'),
        (0xE8010, 'ldur', 'w10, [x20, #-0x14]'),
        (0xE8014, 'str', 'x12, [x19]'),
        (0xE8018, 'ldur', 'w11, [x20, #-0x18]'),
        (0xE801C, 'bfi', 'x1, x8, #0x20, #0x20'),
        (0xE8020, 'bfi', 'x0, x9, #0x20, #0x20'),
        (0xE8024, 'bfi', 'x11, x10, #0x20, #0x20'),
        (0xE8028, 'blr', 'x11'),
        (0xE802C, 'stur', 'w0, [x20, #-0x18]'),
        (0xE8030, 'str', 'x21, [x19]'),
    ],
    'non_null_target_and_record_two': [
        (0x848D8, 'mov', 'x19, x1'),
        (0x848DC, 'cbz', 'x0, #0x848f0'),
        (0x848E0, 'mov', 'x1, x19'),
        (0x848EC, 'b', '#0x72dfc'),
        (0x72E0C, 'mov', 'x19, x1'),
        (0x72E10, 'mov', 'x20, x0'),
        (0x72E18, 'mov', 'x1, x20'),
        (0x72E20, 'bl', '#0xe2e10'),
        (0x72E24, 'mov', 'x0, x21'),
        (0x72E28, 'mov', 'x1, x19'),
        (0x72E2C, 'bl', '#0xe2e10'),
        (0x72E30, 'adrp', 'x1, #0x113000'),
        (0x72E34, 'adrp', 'x2, #0x113000'),
        (0x72E38, 'adrp', 'x3, #0x113000'),
        (0x72E3C, 'adrp', 'x4, #0x113000'),
        (0x72E40, 'add', 'x1, x1, #0x5b0'),
        (0x72E44, 'add', 'x2, x2, #0x5d0'),
        (0x72E48, 'add', 'x3, x3, #0x5f0'),
        (0x72E4C, 'add', 'x4, x4, #0x790'),
        (0x72E50, 'mov', 'w5, #2'),
        (0x72E54, 'mov', 'x0, x21'),
        (0x72E58, 'bl', '#0xe2e00'),
    ],
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def expect(value, wanted, label: str) -> None:
    if value != wanted:
        raise ValueError(f"Static boundary mismatch: {label}")


def read(image: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(image):
        raise ValueError("Static metadata read exceeds the pinned image")
    return image[offset:offset + size]


def pointer(image: bytes, offset: int) -> int:
    value = struct.unpack("<Q", read(image, offset, 8))[0] - BASE
    if not 0 <= value < len(image):
        raise ValueError("Selected static pointer is outside the image")
    return value


def byte_transform(value: int) -> int:
    # Literal arithmetic from BD624's header and rolling-opcode paths.
    return (((value - 0x69) & 0xA0) | ((0x68 - value) & 0x5F)) & 0xFF


def inspect(image: bytes, elf) -> dict:
    writable = [(segment[3], segment[3] + segment[6])
                for segment in elf.loads if segment[1] & 2]
    relocated, _, relocation_types, _ = strings.relocate(
        elf, image, [(0, RX_END), *writable], writable)
    bounds = strings.function_bounds(elf, relocated, RX_END)
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    selected = []
    for start in FUNCTIONS:
        if start not in bounds:
            raise ValueError("Selected function lacks a supported exact FDE")
        lower, upper = bounds[start]
        data = read(relocated, lower, upper - lower)
        decoded = list(engine.disasm(data, lower))
        expect(sum(item.size for item in decoded), len(data), "complete function decode")
        selected.append({"start": hex(lower), "end_exclusive": hex(upper),
                         "bytes": len(data), "sha256": digest(data)})
    for label, checks in INSTRUCTION_CHECKS.items():
        for address, mnemonic, operands in checks:
            decoded = list(engine.disasm(read(relocated, address, 4), address))
            expect(len(decoded), 1, f"{label} instruction count")
            expect((decoded[0].mnemonic, decoded[0].op_str), (mnemonic, operands), label)

    records = []
    for index, expected in enumerate(((0, 24), (24, 196), (220, 182))):
        offset, size = struct.unpack("<ii", read(relocated, 0x113790 + index * 8, 8))
        expect((offset, size), expected, "selected descriptor bounds")
        start = 0x1135F0 + offset
        record = read(relocated, start, size)
        expect(record[0], 0x3B, "selected record marker")
        key = record[1] ^ 0x5F
        words = byte_transform(record[2]) ^ key
        words |= (byte_transform(record[3]) ^ key) << 8
        first = byte_transform(record[4]) ^ key
        expect(words, 4, "selected record frame words")
        expect(first, 0x2C, "selected first handler")
        expect(pointer(relocated, 0xF3AF0 + first * 8), 0xBD760, "first dispatch target")
        records.append({"index": index, "start": hex(start),
                        "end_exclusive": hex(start + size), "stored_bytes": size,
                        "sha256": digest(record), "frame_words": words,
                        "first_handler_index": hex(first),
                        "first_handler_target": "0xbd760"})

    # Decode only this proven prefix. Do not treat operand bytes as opcodes or
    # apply these widths to record 1, record 2, or another protected program.
    record = read(relocated, 0x1135F0, 24)
    previous = None
    tokens = []
    for position, wanted, width, target, meaning in TOKEN_SPECS:
        if previous is not None and ((previous + 0x7B) & 0xFF) < 5 and record[position] == 0x3B:
            raise ValueError("Special marker branch is outside this prefix proof")
        key = record[1] if previous is None else previous
        opcode = byte_transform(record[position]) ^ key ^ 0x5F
        expect(opcode, wanted, "wrapper prefix rolling opcode")
        expect(pointer(relocated, 0xF3AF0 + opcode * 8), target, "prefix dispatch target")
        operand = int.from_bytes(read(record, position + 1, width), "little")
        tokens.append({"record_offset": position, "handler_index": hex(opcode),
                       "handler_target": hex(target), "operand_bytes": width,
                       "operand": operand, "static_meaning": meaning})
        previous = opcode
    expect([token["operand"] for token in tokens], [2, 2, 0, 2, 0], "wrapper operands")
    expect(pointer(relocated, 0x1135B0 + 2 * 8), 0x848CC, "native target table entry")
    expect(pointer(relocated, 0x1135D0), 0xE7FC4, "native call thunk entry")
    return {
        "scope": "Static protected JNI wrapper prefix; no guest or VM execution",
        "guest_instructions_executed": 0,
        "elf_relative_relocations_applied": relocation_types.get(1027, 0),
        "supported_fde_functions": len(bounds),
        "selected_functions": selected,
        "instruction_checks": {label: {"count": len(checks),
            "addresses": [hex(check[0]) for check in checks]}
            for label, checks in INSTRUCTION_CHECKS.items()},
        "descriptor_records": records,
        "record_zero_prefix": {"tokens": tokens, "decoded_through_offset_exclusive": 17,
            "remaining_bytes_unparsed": 7, "native_target": "0x848cc",
            "native_thunk": "0xe7fc4", "native_thunk_indirect_call": "0xe8028",
            "argument_order": ["original JNI x0", "original JNI x1"],
            "non_null_x0_target": "0x72dfc", "next_descriptor_index": 2,
            "next_record_start": "0x1136cc", "next_record_bytes": 182},
        "limits": ["No complete descriptor VM decoder",
            "Record 1 and record 2 bodies remain undecoded",
            "No plaintext DEX, SDK action serializer or device packet recovered",
            "DETool.dowork belongs to a separate data-protection library"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-apk", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.base_apk.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("APK exceeds the static input bound")
    apk = args.base_apk.read_bytes()
    expect(digest(apk), carrier.APK_SHA256, "base APK SHA-256")
    image_path = args.images_dir / IMAGE_NAME
    expect(image_path.stat().st_size, IMAGE_SIZE, "virtual image byte length")
    image = image_path.read_bytes()
    expect(digest(image), IMAGE_SHA, "virtual image SHA-256")
    with zipfile.ZipFile(BytesIO(apk)) as archive:
        info = archive.getinfo(LIBRARY)
        if info.file_size > 2 * 1024 * 1024:
            raise ValueError("Packed library exceeds the static input bound")
        packed = archive.read(LIBRARY)
    expect(digest(packed), carrier.LIBRARIES[LIBRARY], "packed library SHA-256")
    result = inspect(image, carrier.LoadElf(packed))
    files = (Path(__file__), Path(carrier.__file__), Path(strings.__file__))
    manifest = {"tool": Path(__file__).name, "apk_sha256": digest(apk),
                "packed_library_sha256": digest(packed), "image_sha256": digest(image),
                "source_sha256": {path.name: digest(path.read_bytes()) for path in files},
                "runtime": {"python": platform.python_version(),
                            "capstone": capstone.__version__},
                "guest_instructions_executed": 0}
    # Publish only after every input, bound, token and instruction check passes.
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-loader-vm-boundary-{suffix}.json"
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        path.chmod(0o600)
    print(json.dumps({"records_bounded": len(result["descriptor_records"]),
                     "prefix_tokens": len(result["record_zero_prefix"]["tokens"]),
                     "instruction_checks": sum(len(x) for x in INSTRUCTION_CHECKS.values()),
                     "guest_instructions_executed": 0}))


if __name__ == "__main__":
    main()
