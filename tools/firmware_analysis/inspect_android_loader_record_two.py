#!/usr/bin/env python3
"""Statically decode one exact protected JNI descriptor record.

Parse token widths, rolling keys and both control-flow edges of the pinned
182-byte record. Runtime JNI memory and native call results stay symbolic;
no VM/JNI/Android/native method, initializer or device operation executes.
Public output contains selected metadata, addresses and hashes only.
"""
from __future__ import annotations

import argparse
from collections import deque
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
import inspect_android_loader_vm_boundary as vm

START, SIZE, TABLE = 0x1136CC, 182, 0x113790
DECODED_SHA = "14c73475f02e708a07d982b65d5fcea65792c8d477ba293ca95187f461e6e06f"
MAX_STATES = 64
# operand bytes, handler address, exclusive native handler span end, meaning
HANDLERS = {
    0x01: (0, 0xBD910, 0xBD92C, "return_word"),
    0x0C: (0, 0xC1320, 0xC13E8, "compare_words_equal"),
    0x2C: (1, 0xBD760, 0xBD8E0, "reserve_zero_words"),
    0x32: (4, 0xC2528, 0xC260C, "push_immediate_word"),
    0x33: (8, 0xC3908, 0xC39C8, "push_immediate_pointer"),
    0x36: (2, 0xC075C, 0xC0838, "push_local_byte"),
    0x38: (2, 0xC3CE4, 0xC3DBC, "push_local_word"),
    0x39: (2, 0xC39C8, 0xC3AB0, "push_local_pointer"),
    0x3B: (1, 0xC35F8, 0xC36A4, "reset_rolling_key"),
    0x3C: (2, 0xC3820, 0xC3908, "store_local_byte"),
    0x3E: (2, 0xC2E0C, 0xC2EE4, "store_local_word"),
    0x3F: (2, 0xC010C, 0xC01F0, "store_local_pointer"),
    0x41: (2, 0xC0A8C, 0xC0B64, "push_local_address"),
    0x45: (0, 0xC0B64, 0xC0C24, "load_indirect_pointer"),
    0x4A: (0, 0xBF780, 0xBF848, "store_indirect_pointer"),
    0x4C: (2, 0xBEFC0, 0xBF0A4, "push_call_table_pointer"),
    0x4D: (1, 0xC3768, 0xC3820, "call_native_thunk"),
    0x68: (0, 0xC4600, 0xC46C8, "add_pointers"),
    0x85: (3, 0xC2798, 0xC2878, "jump_relative"),
    0x86: (3, 0xC02A4, 0xC03A8, "jump_relative_if_byte_nonzero"),
}
METHODS = (
    (0x271ED, 0x1035F0, 0x740E0, "l", "(Landroid/app/Application;Ljava/lang/String;)Z"),
    (0x26D33, 0x1035F0, 0x768F0, "r", "(Landroid/app/Application;Ljava/lang/String;)Z"),
    (0x103658, 0x1035F0, 0x76F78, "ra", "(Landroid/app/Application;Ljava/lang/String;)Z"),
    (0x10365C, 0x103660, 0x77124, "b2b", "([BI)[B"),
    (0x26CDA, 0x103670, 0x7712C, "m", "(Ljava/lang/String;I)V"),
    (0x103688, 0x103690, 0x77130, "sa", "(Ljava/lang/String;Ljava/lang/String;)V"),
    (0x1036B8, 0x1036C0, 0x77134, "al",
     "(Ljava/lang/ClassLoader;Landroid/content/pm/ApplicationInfo;Ljava/lang/String;Ljava/lang/String;)Ljava/lang/ClassLoader;"),
)
FUNCTIONS = (0xE8040, 0xE80C8, 0xE8128, 0x73864, 0x77124, 0x7712C,
             0x77130, 0x77134)
INSTRUCTION_CHECKS = {
    'handler_01_return_word': [
        (0xBD910, 'ldr', 'x8, [sp, #8]'),
        (0xBD914, 'ldr', 'x8, [x8]'),
        (0xBD918, 'ldur', 'w8, [x8, #-4]'),
        (0xBD91C, 'ldr', 'x9, [sp, #0x10]'),
        (0xBD920, 'str', 'w8, [x9]'),
        (0xBD924, 'ldr', 'x8, [sp, #0x28]'),
        (0xBD928, 'b', '#0xc46e8'),
    ],
    'handler_0c_compare_words_equal': [
        (0xC1320, 'ldr', 'x8, [sp, #8]'),
        (0xC1324, 'ldr', 'x8, [x8]'),
        (0xC1328, 'ldr', 'w9, [x8, #-4]!'),
        (0xC132C, 'ldur', 'w10, [x8, #-4]'),
        (0xC1330, 'ldr', 'x11, [sp, #8]'),
        (0xC1334, 'cmp', 'w10, w9'),
        (0xC1338, 'cset', 'w9, eq'),
        (0xC133C, 'str', 'x8, [x11]'),
        (0xC1340, 'stur', 'w9, [x8, #-4]'),
        (0xC1344, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC1348, 'ldr', 'x9, [sp, #0x820]'),
        (0xC134C, 'adrp', 'x10, #0x2b000'),
        (0xC1350, 'add', 'w8, w8, #0x7b'),
        (0xC1354, 'ldrb', 'w9, [x9]'),
        (0xC1358, 'and', 'w8, w8, #0xff'),
        (0xC13D0, 'strb', 'w8, [sp, #0x334]'),
        (0xC13D4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC13D8, 'ldrb', 'w8, [sp, #0x334]'),
        (0xC13DC, 'ldrb', 'w9, [sp, #0x338]'),
        (0xC13E0, 'add', 'x10, x10, #1'),
        (0xC13E4, 'b', '#0xbd8ac'),
        (0xC13B8, 'eor', 'w8, w13, w8'),
        (0xC13BC, 'eor', 'w8, w8, w11'),
    ],
    'handler_2c_reserve_zero_words': [
        (0xBD760, 'ldr', 'x8, [sp, #0x38]'),
        (0xBD764, 'mov', 'x9, #-1'),
        (0xBD768, 'ldr', 'x8, [x8]'),
        (0xBD76C, 'stp', 'x8, x9, [sp, #0x50]'),
        (0xBD770, 'ldr', 'x8, [sp, #0x50]'),
        (0xBD774, 'ldr', 'x8, [sp, #0x820]'),
        (0xBD778, 'adrp', 'x9, #0x2b000'),
        (0xBD77C, 'ldrb', 'w8, [x8]'),
        (0xBD780, 'lsl', 'x8, x8, #2'),
        (0xBD784, 'str', 'x8, [sp, #0x60]'),
        (0xBD788, 'ldr', 'x8, [sp, #0x58]'),
        (0xBD78C, 'ldr', 'w9, [x9, #0x26c]'),
        (0xBD790, 'cmn', 'x8, #1'),
        (0xBD794, 'cinc', 'w8, w9, eq'),
        (0xBD798, 'ldr', 'x8, [x24, w8, sxtw #3]'),
        (0xBD8C8, 'strb', 'w9, [sp, #0x81c]'),
        (0xBD8CC, 'str', 'x10, [sp, #0x820]'),
        (0xBD8D0, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xBD8D4, 'ldr', 'x9, [x19, x8, lsl #3]'),
        (0xBD8D8, 'ldr', 'x8, [sp, #0x10]'),
        (0xBD8DC, 'br', 'x9'),
        (0xBD880, 'eor', 'w8, w13, w8'),
        (0xBD884, 'eor', 'w8, w8, w11'),
    ],
    'handler_32_push_immediate_word': [
        (0xC2528, 'ldr', 'x8, [sp, #0x820]'),
        (0xC252C, 'ldrb', 'w8, [x8]'),
        (0xC2530, 'ldr', 'x9, [sp, #0x820]'),
        (0xC2534, 'ldrb', 'w9, [x9, #1]'),
        (0xC2538, 'ldr', 'x10, [sp, #0x820]'),
        (0xC253C, 'bfi', 'w8, w9, #8, #8'),
        (0xC2540, 'ldrb', 'w9, [x10, #2]'),
        (0xC2544, 'ldr', 'x10, [sp, #0x820]'),
        (0xC2548, 'bfi', 'w8, w9, #0x10, #8'),
        (0xC254C, 'ldrb', 'w9, [x10, #3]'),
        (0xC2550, 'ldr', 'x10, [sp, #8]'),
        (0xC2554, 'bfi', 'w8, w9, #0x18, #8'),
        (0xC2558, 'ldr', 'x9, [x10]'),
        (0xC255C, 'adrp', 'x10, #0x2b000'),
        (0xC2560, 'str', 'w8, [x9], #4'),
        (0xC2564, 'ldr', 'x8, [sp, #8]'),
        (0xC2568, 'str', 'x9, [x8]'),
        (0xC256C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC2570, 'ldrb', 'w9, [sp, #0x82c]'),
        (0xC25F4, 'strb', 'w8, [sp, #0x10c]'),
        (0xC25F8, 'ldr', 'x10, [sp, #0x820]'),
        (0xC25FC, 'ldrb', 'w8, [sp, #0x10c]'),
        (0xC2600, 'ldrb', 'w9, [sp, #0x110]'),
        (0xC2604, 'add', 'x10, x10, #5'),
        (0xC2608, 'b', '#0xbd8ac'),
        (0xC25DC, 'eor', 'w8, w13, w8'),
        (0xC25E0, 'eor', 'w8, w8, w11'),
    ],
    'handler_33_push_immediate_pointer': [
        (0xC3908, 'ldr', 'x8, [sp, #0x820]'),
        (0xC390C, 'adrp', 'x10, #0x2b000'),
        (0xC3910, 'ldr', 'x8, [x8]'),
        (0xC3914, 'ldr', 'x9, [sp, #8]'),
        (0xC3918, 'ldr', 'x9, [x9]'),
        (0xC391C, 'str', 'x8, [x9], #8'),
        (0xC3920, 'ldr', 'x8, [sp, #8]'),
        (0xC3924, 'str', 'x9, [x8]'),
        (0xC3928, 'ldr', 'x8, [sp, #0x820]'),
        (0xC392C, 'ldrb', 'w9, [sp, #0x82c]'),
        (0xC3930, 'ldrb', 'w8, [x8, #8]'),
        (0xC3934, 'add', 'w9, w9, #0x7b'),
        (0xC3938, 'and', 'w9, w9, #0xff'),
        (0xC393C, 'cmp', 'w9, #5'),
        (0xC3940, 'strb', 'w8, [sp, #0x114]'),
        (0xC39B0, 'strb', 'w8, [sp, #0x118]'),
        (0xC39B4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC39B8, 'ldrb', 'w8, [sp, #0x118]'),
        (0xC39BC, 'ldrb', 'w9, [sp, #0x11c]'),
        (0xC39C0, 'add', 'x10, x10, #9'),
        (0xC39C4, 'b', '#0xbd8ac'),
        (0xC3998, 'eor', 'w8, w13, w8'),
        (0xC399C, 'eor', 'w8, w8, w11'),
    ],
    'handler_36_push_local_byte': [
        (0xC075C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC0760, 'ldrb', 'w8, [x8]'),
        (0xC0764, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0768, 'ldrb', 'w9, [x9, #1]'),
        (0xC076C, 'ldr', 'x10, [sp, #0x30]'),
        (0xC0770, 'lsl', 'w9, w9, #8'),
        (0xC0774, 'sxth', 'x9, w9'),
        (0xC0778, 'orr', 'x8, x9, x8'),
        (0xC077C, 'ldrb', 'w8, [x10, x8]'),
        (0xC0780, 'ldr', 'x9, [sp, #8]'),
        (0xC0784, 'adrp', 'x10, #0x2b000'),
        (0xC0788, 'ldr', 'x9, [x9]'),
        (0xC078C, 'str', 'w8, [x9], #4'),
        (0xC0790, 'ldr', 'x8, [sp, #8]'),
        (0xC0794, 'str', 'x9, [x8]'),
        (0xC0820, 'strb', 'w8, [sp, #0x1a0]'),
        (0xC0824, 'ldr', 'x10, [sp, #0x820]'),
        (0xC0828, 'ldrb', 'w8, [sp, #0x1a0]'),
        (0xC082C, 'ldrb', 'w9, [sp, #0x1a4]'),
        (0xC0830, 'add', 'x10, x10, #3'),
        (0xC0834, 'b', '#0xbd8ac'),
        (0xC0808, 'eor', 'w8, w13, w8'),
        (0xC080C, 'eor', 'w8, w8, w11'),
    ],
    'handler_38_push_local_word': [
        (0xC3CE4, 'ldr', 'x8, [sp, #0x820]'),
        (0xC3CE8, 'ldrb', 'w8, [x8]'),
        (0xC3CEC, 'ldr', 'x9, [sp, #0x820]'),
        (0xC3CF0, 'ldrb', 'w9, [x9, #1]'),
        (0xC3CF4, 'ldr', 'x10, [sp, #0x10]'),
        (0xC3CF8, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC3CFC, 'sxth', 'x8, w8'),
        (0xC3D00, 'ldr', 'w8, [x10, x8, lsl #2]'),
        (0xC3D04, 'ldr', 'x9, [sp, #8]'),
        (0xC3D08, 'adrp', 'x10, #0x2b000'),
        (0xC3D0C, 'ldr', 'x9, [x9]'),
        (0xC3D10, 'str', 'w8, [x9], #4'),
        (0xC3D14, 'ldr', 'x8, [sp, #8]'),
        (0xC3D18, 'str', 'x9, [x8]'),
        (0xC3D1C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC3DA4, 'strb', 'w8, [sp, #0x1b8]'),
        (0xC3DA8, 'ldr', 'x10, [sp, #0x820]'),
        (0xC3DAC, 'ldrb', 'w8, [sp, #0x1b8]'),
        (0xC3DB0, 'ldrb', 'w9, [sp, #0x1bc]'),
        (0xC3DB4, 'add', 'x10, x10, #3'),
        (0xC3DB8, 'b', '#0xbd8ac'),
        (0xC3D8C, 'eor', 'w8, w13, w8'),
        (0xC3D90, 'eor', 'w8, w8, w11'),
    ],
    'handler_39_push_local_pointer': [
        (0xC39C8, 'ldr', 'x8, [sp, #0x820]'),
        (0xC39CC, 'ldrb', 'w8, [x8]'),
        (0xC39D0, 'ldr', 'x9, [sp, #0x820]'),
        (0xC39D4, 'ldrb', 'w9, [x9, #1]'),
        (0xC39D8, 'ldr', 'x10, [sp, #0x10]'),
        (0xC39DC, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC39E0, 'sbfiz', 'x8, x8, #2, #0x10'),
        (0xC39E4, 'ldr', 'w9, [x10, x8]'),
        (0xC39E8, 'ldr', 'x10, [sp, #8]'),
        (0xC39EC, 'ldr', 'x10, [x10]'),
        (0xC39F0, 'str', 'w9, [x10]'),
        (0xC39F4, 'ldr', 'x9, [sp, #0x28]'),
        (0xC39F8, 'ldr', 'w8, [x9, x8]'),
        (0xC39FC, 'add', 'x9, x10, #8'),
        (0xC3A00, 'str', 'w8, [x10, #4]'),
        (0xC3A98, 'strb', 'w8, [sp, #0x1c4]'),
        (0xC3A9C, 'ldr', 'x10, [sp, #0x820]'),
        (0xC3AA0, 'ldrb', 'w8, [sp, #0x1c4]'),
        (0xC3AA4, 'ldrb', 'w9, [sp, #0x1c8]'),
        (0xC3AA8, 'add', 'x10, x10, #3'),
        (0xC3AAC, 'b', '#0xbd8ac'),
        (0xC3A80, 'eor', 'w8, w13, w8'),
        (0xC3A84, 'eor', 'w8, w8, w11'),
    ],
    'handler_3b_reset_rolling_key': [
        (0xC35F8, 'ldr', 'x8, [sp, #0x820]'),
        (0xC35FC, 'adrp', 'x10, #0x2b000'),
        (0xC3600, 'ldrb', 'w8, [x8]'),
        (0xC3604, 'strb', 'w8, [sp, #0x524]'),
        (0xC3608, 'ldr', 'x8, [sp, #0x820]'),
        (0xC360C, 'ldrb', 'w9, [sp, #0x82c]'),
        (0xC3610, 'ldrb', 'w8, [x8, #1]'),
        (0xC3614, 'add', 'w9, w9, #0x7b'),
        (0xC3618, 'and', 'w9, w9, #0xff'),
        (0xC361C, 'cmp', 'w9, #5'),
        (0xC3620, 'strb', 'w8, [sp, #0x528]'),
        (0xC3624, 'ldrb', 'w8, [sp, #0x528]'),
        (0xC3628, 'ldr', 'w10, [x10, #0x668]'),
        (0xC362C, 'cset', 'w9, lo'),
        (0xC3630, 'cmp', 'w8, #0x3b'),
        (0xC368C, 'strb', 'w9, [sp, #0x530]'),
        (0xC3690, 'strb', 'w8, [sp, #0x52c]'),
        (0xC3694, 'ldr', 'x10, [sp, #0x820]'),
        (0xC3698, 'ldrb', 'w8, [sp, #0x52c]'),
        (0xC369C, 'ldrb', 'w9, [sp, #0x530]'),
        (0xC36A0, 'b', '#0xbd8a8'),
        (0xC3678, 'eor', 'w8, w13, w8'),
        (0xC367C, 'eor', 'w8, w8, w11'),
    ],
    'handler_3c_store_local_byte': [
        (0xC3820, 'ldr', 'x8, [sp, #0x820]'),
        (0xC3824, 'ldrb', 'w8, [x8]'),
        (0xC3828, 'ldr', 'x9, [sp, #0x820]'),
        (0xC382C, 'ldrb', 'w9, [x9, #1]'),
        (0xC3830, 'ldr', 'x10, [sp, #8]'),
        (0xC3834, 'lsl', 'w9, w9, #8'),
        (0xC3838, 'ldr', 'x10, [x10]'),
        (0xC383C, 'sxth', 'x9, w9'),
        (0xC3840, 'orr', 'x8, x9, x8'),
        (0xC3844, 'ldurb', 'w10, [x10, #-4]'),
        (0xC3848, 'ldr', 'x11, [sp, #0x30]'),
        (0xC384C, 'strb', 'w10, [x11, x8]'),
        (0xC3850, 'ldr', 'x8, [sp, #0x820]'),
        (0xC3854, 'ldr', 'x9, [sp, #8]'),
        (0xC3858, 'ldr', 'x9, [x9]'),
        (0xC38F0, 'strb', 'w8, [sp, #0x13c]'),
        (0xC38F4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC38F8, 'ldrb', 'w8, [sp, #0x13c]'),
        (0xC38FC, 'ldrb', 'w9, [sp, #0x140]'),
        (0xC3900, 'add', 'x10, x10, #3'),
        (0xC3904, 'b', '#0xbd8ac'),
        (0xC38D8, 'eor', 'w8, w13, w8'),
        (0xC38DC, 'eor', 'w8, w8, w11'),
    ],
    'handler_3e_store_local_word': [
        (0xC2E0C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC2E10, 'ldrb', 'w8, [x8]'),
        (0xC2E14, 'ldr', 'x9, [sp, #0x820]'),
        (0xC2E18, 'ldrb', 'w9, [x9, #1]'),
        (0xC2E1C, 'ldr', 'x10, [sp, #8]'),
        (0xC2E20, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC2E24, 'ldr', 'x9, [x10]'),
        (0xC2E28, 'sxth', 'x8, w8'),
        (0xC2E2C, 'ldr', 'w10, [x9, #-4]!'),
        (0xC2E30, 'ldr', 'x11, [sp, #0x10]'),
        (0xC2E34, 'str', 'w10, [x11, x8, lsl #2]'),
        (0xC2E38, 'ldr', 'x8, [sp, #0x820]'),
        (0xC2E3C, 'ldr', 'x10, [sp, #8]'),
        (0xC2E40, 'str', 'x9, [x10]'),
        (0xC2E44, 'ldrb', 'w9, [sp, #0x82c]'),
        (0xC2ECC, 'strb', 'w8, [sp, #0x154]'),
        (0xC2ED0, 'ldr', 'x10, [sp, #0x820]'),
        (0xC2ED4, 'ldrb', 'w8, [sp, #0x154]'),
        (0xC2ED8, 'ldrb', 'w9, [sp, #0x158]'),
        (0xC2EDC, 'add', 'x10, x10, #3'),
        (0xC2EE0, 'b', '#0xbd8ac'),
        (0xC2EB4, 'eor', 'w8, w13, w8'),
        (0xC2EB8, 'eor', 'w8, w8, w11'),
    ],
    'handler_3f_store_local_pointer': [
        (0xC010C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC0110, 'ldrb', 'w8, [x8]'),
        (0xC0114, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0118, 'ldrb', 'w9, [x9, #1]'),
        (0xC011C, 'ldr', 'x10, [sp, #8]'),
        (0xC0120, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC0124, 'ldr', 'x9, [x10]'),
        (0xC0128, 'sbfiz', 'x8, x8, #2, #0x10'),
        (0xC012C, 'ldr', 'w10, [x9, #-8]!'),
        (0xC0130, 'ldr', 'x11, [sp, #0x10]'),
        (0xC0134, 'str', 'w10, [x11, x8]'),
        (0xC0138, 'ldr', 'w10, [x9, #4]'),
        (0xC013C, 'ldr', 'x11, [sp, #0x28]'),
        (0xC0140, 'str', 'w10, [x11, x8]'),
        (0xC0144, 'ldr', 'x8, [sp, #0x820]'),
        (0xC01D8, 'strb', 'w8, [sp, #0x160]'),
        (0xC01DC, 'ldr', 'x10, [sp, #0x820]'),
        (0xC01E0, 'ldrb', 'w8, [sp, #0x160]'),
        (0xC01E4, 'ldrb', 'w9, [sp, #0x164]'),
        (0xC01E8, 'add', 'x10, x10, #3'),
        (0xC01EC, 'b', '#0xbd8ac'),
        (0xC01C0, 'eor', 'w8, w13, w8'),
        (0xC01C4, 'eor', 'w8, w8, w11'),
    ],
    'handler_41_push_local_address': [
        (0xC0A8C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC0A90, 'ldrb', 'w8, [x8]'),
        (0xC0A94, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0A98, 'ldrb', 'w9, [x9, #1]'),
        (0xC0A9C, 'ldr', 'x10, [sp, #0x10]'),
        (0xC0AA0, 'ldr', 'x11, [sp, #8]'),
        (0xC0AA4, 'bfi', 'w8, w9, #8, #0x18'),
        (0xC0AA8, 'add', 'x8, x10, w8, sxth #2'),
        (0xC0AAC, 'ldr', 'x9, [x11]'),
        (0xC0AB0, 'lsr', 'x10, x8, #0x20'),
        (0xC0AB4, 'stp', 'w8, w10, [x9], #8'),
        (0xC0AB8, 'ldr', 'x8, [sp, #8]'),
        (0xC0ABC, 'adrp', 'x10, #0x2b000'),
        (0xC0AC0, 'str', 'x9, [x8]'),
        (0xC0AC4, 'ldr', 'x8, [sp, #0x820]'),
        (0xC0B4C, 'strb', 'w8, [sp, #0x2c4]'),
        (0xC0B50, 'ldr', 'x10, [sp, #0x820]'),
        (0xC0B54, 'ldrb', 'w8, [sp, #0x2c4]'),
        (0xC0B58, 'ldrb', 'w9, [sp, #0x2c8]'),
        (0xC0B5C, 'add', 'x10, x10, #3'),
        (0xC0B60, 'b', '#0xbd8ac'),
        (0xC0B34, 'eor', 'w8, w13, w8'),
        (0xC0B38, 'eor', 'w8, w8, w11'),
    ],
    'handler_45_load_indirect_pointer': [
        (0xC0B64, 'ldr', 'x8, [sp, #8]'),
        (0xC0B68, 'ldr', 'x8, [x8]'),
        (0xC0B6C, 'ldur', 'x9, [x8, #-8]'),
        (0xC0B70, 'ldr', 'w10, [x9]'),
        (0xC0B74, 'stur', 'w10, [x8, #-8]'),
        (0xC0B78, 'ldr', 'w9, [x9, #4]'),
        (0xC0B7C, 'adrp', 'x10, #0x2b000'),
        (0xC0B80, 'stur', 'w9, [x8, #-4]'),
        (0xC0B84, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC0B88, 'ldr', 'x9, [sp, #0x820]'),
        (0xC0B8C, 'add', 'w8, w8, #0x7b'),
        (0xC0B90, 'ldrb', 'w9, [x9]'),
        (0xC0B94, 'and', 'w8, w8, #0xff'),
        (0xC0B98, 'cmp', 'w8, #5'),
        (0xC0B9C, 'strb', 'w9, [sp, #0x220]'),
        (0xC0C0C, 'strb', 'w8, [sp, #0x224]'),
        (0xC0C10, 'ldr', 'x10, [sp, #0x820]'),
        (0xC0C14, 'ldrb', 'w8, [sp, #0x224]'),
        (0xC0C18, 'ldrb', 'w9, [sp, #0x228]'),
        (0xC0C1C, 'add', 'x10, x10, #1'),
        (0xC0C20, 'b', '#0xbd8ac'),
        (0xC0BF4, 'eor', 'w8, w13, w8'),
        (0xC0BF8, 'eor', 'w8, w8, w11'),
    ],
    'handler_4a_store_indirect_pointer': [
        (0xBF780, 'ldr', 'x8, [sp, #8]'),
        (0xBF784, 'ldr', 'x8, [x8]'),
        (0xBF788, 'ldr', 'x9, [x8, #-0x10]!'),
        (0xBF78C, 'ldr', 'w10, [x8, #8]'),
        (0xBF790, 'str', 'w10, [x9]'),
        (0xBF794, 'ldr', 'w10, [x8, #0xc]'),
        (0xBF798, 'str', 'w10, [x9, #4]'),
        (0xBF79C, 'ldr', 'x9, [sp, #8]'),
        (0xBF7A0, 'adrp', 'x10, #0x2b000'),
        (0xBF7A4, 'str', 'x8, [x9]'),
        (0xBF7A8, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xBF7AC, 'ldr', 'x9, [sp, #0x820]'),
        (0xBF7B0, 'add', 'w8, w8, #0x7b'),
        (0xBF7B4, 'ldrb', 'w9, [x9]'),
        (0xBF7B8, 'and', 'w8, w8, #0xff'),
        (0xBF830, 'strb', 'w8, [sp, #0x284]'),
        (0xBF834, 'ldr', 'x10, [sp, #0x820]'),
        (0xBF838, 'ldrb', 'w8, [sp, #0x284]'),
        (0xBF83C, 'ldrb', 'w9, [sp, #0x288]'),
        (0xBF840, 'add', 'x10, x10, #1'),
        (0xBF844, 'b', '#0xbd8ac'),
        (0xBF818, 'eor', 'w8, w13, w8'),
        (0xBF81C, 'eor', 'w8, w8, w11'),
    ],
    'handler_4c_push_call_table_pointer': [
        (0xBEFC0, 'ldr', 'x8, [sp, #0x820]'),
        (0xBEFC4, 'ldrb', 'w8, [x8]'),
        (0xBEFC8, 'ldr', 'x9, [sp, #0x820]'),
        (0xBEFCC, 'ldrb', 'w9, [x9, #1]'),
        (0xBEFD0, 'ldr', 'x10, [sp, #0x20]'),
        (0xBEFD4, 'ldr', 'x11, [sp, #8]'),
        (0xBEFD8, 'bfi', 'w8, w9, #8, #0x18'),
        (0xBEFDC, 'add', 'x8, x10, w8, sxth #3'),
        (0xBEFE0, 'ldr', 'x9, [x11]'),
        (0xBEFE4, 'ldr', 'w10, [x8]'),
        (0xBEFE8, 'str', 'w10, [x9]'),
        (0xBEFEC, 'ldr', 'w8, [x8, #4]'),
        (0xBEFF0, 'adrp', 'x10, #0x2b000'),
        (0xBEFF4, 'str', 'w8, [x9, #4]'),
        (0xBEFF8, 'ldr', 'x8, [sp, #8]'),
        (0xBF08C, 'strb', 'w8, [sp, #0x2fc]'),
        (0xBF090, 'ldr', 'x10, [sp, #0x820]'),
        (0xBF094, 'ldrb', 'w8, [sp, #0x2fc]'),
        (0xBF098, 'ldrb', 'w9, [sp, #0x300]'),
        (0xBF09C, 'add', 'x10, x10, #3'),
        (0xBF0A0, 'b', '#0xbd8ac'),
        (0xBF074, 'eor', 'w8, w13, w8'),
        (0xBF078, 'eor', 'w8, w8, w11'),
    ],
    'handler_4d_call_native_thunk': [
        (0xC3768, 'ldr', 'x20, [sp, #0x820]'),
        (0xC376C, 'ldr', 'x8, [sp, #0x820]'),
        (0xC3770, 'ldrb', 'w8, [x8]'),
        (0xC3774, 'ldr', 'x9, [sp, #0x18]'),
        (0xC3778, 'ldr', 'x8, [x9, x8, lsl #3]'),
        (0xC377C, 'ldr', 'x0, [sp]'),
        (0xC3780, 'blr', 'x8'),
        (0xC3784, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC3788, 'ldrb', 'w9, [x20, #1]'),
        (0xC378C, 'adrp', 'x10, #0x2b000'),
        (0xC3790, 'add', 'w8, w8, #0x7b'),
        (0xC3794, 'strb', 'w9, [sp, #0x304]'),
        (0xC3798, 'ldrb', 'w9, [sp, #0x304]'),
        (0xC379C, 'and', 'w8, w8, #0xff'),
        (0xC37A0, 'cmp', 'w8, #5'),
        (0xC3808, 'strb', 'w9, [sp, #0x30c]'),
        (0xC380C, 'strb', 'w8, [sp, #0x308]'),
        (0xC3810, 'ldr', 'x10, [sp, #0x820]'),
        (0xC3814, 'ldrb', 'w8, [sp, #0x308]'),
        (0xC3818, 'ldrb', 'w9, [sp, #0x30c]'),
        (0xC381C, 'b', '#0xbd8a8'),
        (0xC37F4, 'eor', 'w8, w13, w8'),
        (0xC37F8, 'eor', 'w8, w8, w11'),
    ],
    'handler_68_add_pointers': [
        (0xC4600, 'ldr', 'x8, [sp, #8]'),
        (0xC4604, 'ldr', 'x8, [x8]'),
        (0xC4608, 'ldr', 'x9, [x8, #-8]!'),
        (0xC460C, 'ldur', 'x10, [x8, #-8]'),
        (0xC4610, 'ldr', 'x11, [sp, #8]'),
        (0xC4614, 'add', 'x9, x10, x9'),
        (0xC4618, 'lsr', 'x10, x9, #0x20'),
        (0xC461C, 'str', 'x8, [x11]'),
        (0xC4620, 'stp', 'w9, w10, [x8, #-8]'),
        (0xC4624, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC4628, 'ldr', 'x9, [sp, #0x820]'),
        (0xC462C, 'adrp', 'x10, #0x2b000'),
        (0xC4630, 'add', 'w8, w8, #0x7b'),
        (0xC4634, 'ldrb', 'w9, [x9]'),
        (0xC4638, 'and', 'w8, w8, #0xff'),
        (0xC46B0, 'strb', 'w8, [sp, #0x5d4]'),
        (0xC46B4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC46B8, 'ldrb', 'w8, [sp, #0x5d4]'),
        (0xC46BC, 'ldrb', 'w9, [sp, #0x5d8]'),
        (0xC46C0, 'add', 'x10, x10, #1'),
        (0xC46C4, 'b', '#0xbd8ac'),
        (0xC4698, 'eor', 'w8, w13, w8'),
        (0xC469C, 'eor', 'w8, w8, w11'),
    ],
    'handler_85_jump_relative': [
        (0xC2798, 'ldr', 'x8, [sp, #0x820]'),
        (0xC279C, 'ldrb', 'w8, [x8]'),
        (0xC27A0, 'ldr', 'x9, [sp, #0x820]'),
        (0xC27A4, 'ldrb', 'w9, [x9, #1]'),
        (0xC27A8, 'ldr', 'x10, [sp, #0x820]'),
        (0xC27AC, 'bfi', 'w8, w9, #8, #8'),
        (0xC27B0, 'ldrb', 'w9, [x10, #2]'),
        (0xC27B4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC27B8, 'sxtb', 'w11, w9'),
        (0xC27BC, 'bfi', 'w8, w9, #0x10, #8'),
        (0xC27C0, 'lsr', 'w9, w11, #7'),
        (0xC27C4, 'bfi', 'w8, w9, #0x18, #8'),
        (0xC27C8, 'sub', 'w8, w8, #1'),
        (0xC27CC, 'add', 'x8, x10, w8, sxtw'),
        (0xC27D0, 'str', 'x8, [sp, #0x4b0]'),
        (0xC27D4, 'ldrb', 'w8, [sp, #0x82c]'),
        (0xC27D8, 'ldr', 'x9, [sp, #0x4b0]'),
        (0xC27DC, 'adrp', 'x10, #0x2b000'),
        (0xC27E0, 'add', 'w8, w8, #0x7b'),
        (0xC27E4, 'ldrb', 'w9, [x9]'),
        (0xC27E8, 'and', 'w8, w8, #0xff'),
        (0xC27EC, 'cmp', 'w8, #5'),
        (0xC27F0, 'strb', 'w9, [sp, #0x4bc]'),
        (0xC27F4, 'ldrb', 'w8, [sp, #0x4bc]'),
        (0xC2860, 'strb', 'w8, [sp, #0x4c0]'),
        (0xC2864, 'ldr', 'x10, [sp, #0x4b0]'),
        (0xC2868, 'ldrb', 'w8, [sp, #0x4c0]'),
        (0xC286C, 'ldrb', 'w9, [sp, #0x4c4]'),
        (0xC2870, 'add', 'x10, x10, #1'),
        (0xC2874, 'b', '#0xbd8ac'),
        (0xC2848, 'eor', 'w8, w13, w8'),
        (0xC284C, 'eor', 'w8, w8, w11'),
    ],
    'handler_86_jump_relative_if_byte_nonzero': [
        (0xC02A4, 'ldr', 'x8, [sp, #0x820]'),
        (0xC02A8, 'ldrb', 'w8, [x8]'),
        (0xC02AC, 'ldr', 'x9, [sp, #0x820]'),
        (0xC02B0, 'ldrb', 'w9, [x9, #1]'),
        (0xC02B4, 'ldr', 'x10, [sp, #0x820]'),
        (0xC02B8, 'bfi', 'w8, w9, #8, #8'),
        (0xC02BC, 'ldrb', 'w9, [x10, #2]'),
        (0xC02C0, 'ldr', 'x10, [sp, #8]'),
        (0xC02C4, 'sxtb', 'w11, w9'),
        (0xC02C8, 'ldr', 'x10, [x10]'),
        (0xC02CC, 'bfi', 'w8, w9, #0x10, #8'),
        (0xC02D0, 'lsr', 'w11, w11, #7'),
        (0xC02D4, 'bfi', 'w8, w11, #0x18, #8'),
        (0xC02D8, 'ldrb', 'w9, [x10, #-4]!'),
        (0xC02DC, 'ldr', 'x12, [sp, #0x820]'),
        (0xC02E0, 'ldr', 'x13, [sp, #0x820]'),
        (0xC02E4, 'cmp', 'w9, #0'),
        (0xC02E8, 'add', 'x8, x12, w8, sxtw'),
        (0xC02EC, 'add', 'x11, x13, #3'),
        (0xC02F0, 'sub', 'x8, x8, #1'),
        (0xC02F4, 'csel', 'x8, x11, x8, eq'),
        (0xC02F8, 'str', 'x8, [sp, #0x4c8]'),
        (0xC02FC, 'ldr', 'x8, [sp, #8]'),
        (0xC0300, 'str', 'x10, [x8]'),
        (0xC0390, 'strb', 'w8, [sp, #0x4d8]'),
        (0xC0394, 'ldr', 'x10, [sp, #0x4c8]'),
        (0xC0398, 'ldrb', 'w8, [sp, #0x4d8]'),
        (0xC039C, 'ldrb', 'w9, [sp, #0x4dc]'),
        (0xC03A0, 'add', 'x10, x10, #1'),
        (0xC03A4, 'b', '#0xbd8ac'),
        (0xC0378, 'eor', 'w8, w13, w8'),
        (0xC037C, 'eor', 'w8, w8, w11'),
    ],
    'three_argument_word_return_thunk': [
        (0xE8040, 'stp', 'x30, x21, [sp, #-0x20]!'),
        (0xE8044, 'stp', 'x20, x19, [sp, #0x10]'),
        (0xE8048, 'ldr', 'x20, [x0]'),
        (0xE804C, 'mov', 'x19, x0'),
        (0xE8050, 'sub', 'x8, x20, #4'),
        (0xE8054, 'sub', 'x9, x20, #8'),
        (0xE8058, 'str', 'x8, [x0]'),
        (0xE805C, 'sub', 'x10, x20, #0xc'),
        (0xE8060, 'ldur', 'w2, [x20, #-4]'),
        (0xE8064, 'str', 'x9, [x0]'),
        (0xE8068, 'sub', 'x11, x20, #0x10'),
        (0xE806C, 'ldur', 'w8, [x20, #-8]'),
        (0xE8070, 'str', 'x10, [x0]'),
        (0xE8074, 'sub', 'x12, x20, #0x14'),
        (0xE8078, 'ldur', 'w1, [x20, #-0xc]'),
        (0xE807C, 'str', 'x11, [x0]'),
        (0xE8080, 'sub', 'x21, x20, #0x18'),
        (0xE8084, 'ldur', 'w9, [x20, #-0x10]'),
        (0xE8088, 'str', 'x12, [x0]'),
        (0xE808C, 'sub', 'x13, x20, #0x1c'),
        (0xE8090, 'ldur', 'w0, [x20, #-0x14]'),
        (0xE8094, 'str', 'x21, [x19]'),
        (0xE8098, 'ldur', 'w10, [x20, #-0x18]'),
        (0xE809C, 'str', 'x13, [x19]'),
        (0xE80A0, 'ldur', 'w11, [x20, #-0x1c]'),
        (0xE80A4, 'bfi', 'x1, x8, #0x20, #0x20'),
        (0xE80A8, 'bfi', 'x0, x9, #0x20, #0x20'),
        (0xE80AC, 'bfi', 'x11, x10, #0x20, #0x20'),
        (0xE80B0, 'blr', 'x11'),
        (0xE80B4, 'stur', 'w0, [x20, #-0x1c]'),
        (0xE80B8, 'str', 'x21, [x19]'),
        (0xE80BC, 'ldp', 'x20, x19, [sp, #0x10]'),
        (0xE80C0, 'ldp', 'x30, x21, [sp], #0x20'),
        (0xE80C4, 'ret', ''),
    ],
    'one_argument_word_return_thunk': [
        (0xE80C8, 'stp', 'x30, x21, [sp, #-0x20]!'),
        (0xE80CC, 'stp', 'x20, x19, [sp, #0x10]'),
        (0xE80D0, 'ldr', 'x20, [x0]'),
        (0xE80D4, 'mov', 'x19, x0'),
        (0xE80D8, 'sub', 'x8, x20, #4'),
        (0xE80DC, 'sub', 'x9, x20, #8'),
        (0xE80E0, 'str', 'x8, [x0]'),
        (0xE80E4, 'sub', 'x21, x20, #0xc'),
        (0xE80E8, 'ldur', 'w8, [x20, #-4]'),
        (0xE80EC, 'str', 'x9, [x0]'),
        (0xE80F0, 'sub', 'x10, x20, #0x10'),
        (0xE80F4, 'ldur', 'w0, [x20, #-8]'),
        (0xE80F8, 'str', 'x21, [x19]'),
        (0xE80FC, 'ldur', 'w9, [x20, #-0xc]'),
        (0xE8100, 'str', 'x10, [x19]'),
        (0xE8104, 'ldur', 'w10, [x20, #-0x10]'),
        (0xE8108, 'bfi', 'x0, x8, #0x20, #0x20'),
        (0xE810C, 'bfi', 'x10, x9, #0x20, #0x20'),
        (0xE8110, 'blr', 'x10'),
        (0xE8114, 'stur', 'w0, [x20, #-0x10]'),
        (0xE8118, 'str', 'x21, [x19]'),
        (0xE811C, 'ldp', 'x20, x19, [sp, #0x10]'),
        (0xE8120, 'ldp', 'x30, x21, [sp], #0x20'),
        (0xE8124, 'ret', ''),
    ],
    'one_argument_byte_return_thunk': [
        (0xE8128, 'stp', 'x30, x21, [sp, #-0x20]!'),
        (0xE812C, 'stp', 'x20, x19, [sp, #0x10]'),
        (0xE8130, 'ldr', 'x20, [x0]'),
        (0xE8134, 'mov', 'x19, x0'),
        (0xE8138, 'sub', 'x8, x20, #4'),
        (0xE813C, 'sub', 'x9, x20, #8'),
        (0xE8140, 'str', 'x8, [x0]'),
        (0xE8144, 'sub', 'x21, x20, #0xc'),
        (0xE8148, 'ldur', 'w8, [x20, #-4]'),
        (0xE814C, 'str', 'x9, [x0]'),
        (0xE8150, 'sub', 'x10, x20, #0x10'),
        (0xE8154, 'ldur', 'w0, [x20, #-8]'),
        (0xE8158, 'str', 'x21, [x19]'),
        (0xE815C, 'ldur', 'w9, [x20, #-0xc]'),
        (0xE8160, 'str', 'x10, [x19]'),
        (0xE8164, 'ldur', 'w10, [x20, #-0x10]'),
        (0xE8168, 'bfi', 'x0, x8, #0x20, #0x20'),
        (0xE816C, 'bfi', 'x10, x9, #0x20, #0x20'),
        (0xE8170, 'blr', 'x10'),
        (0xE8174, 'and', 'w8, w0, #0xff'),
        (0xE8178, 'stur', 'w8, [x20, #-0x10]'),
        (0xE817C, 'str', 'x21, [x19]'),
        (0xE8180, 'ldp', 'x20, x19, [sp, #0x10]'),
        (0xE8184, 'ldp', 'x30, x21, [sp], #0x20'),
        (0xE8188, 'ret', ''),
    ],
    'seven_jni_registration_rows': [
        (0x73924, 'adrp', 'x8, #0x27000'),
        (0x73928, 'adrp', 'x9, #0x103000'),
        (0x7392C, 'adrp', 'x10, #0x74000'),
        (0x73930, 'adrp', 'x11, #0x26000'),
        (0x73934, 'adrp', 'x12, #0x76000'),
        (0x73938, 'adrp', 'x13, #0x103000'),
        (0x7393C, 'add', 'x8, x8, #0x1ed'),
        (0x73940, 'add', 'x9, x9, #0x5f0'),
        (0x73944, 'add', 'x10, x10, #0xe0'),
        (0x73948, 'add', 'x11, x11, #0xd33'),
        (0x7394C, 'add', 'x12, x12, #0x8f0'),
        (0x73950, 'add', 'x13, x13, #0x658'),
        (0x73954, 'adrp', 'x0, #0x26000'),
        (0x73958, 'stp', 'x10, x11, [sp, #0x30]'),
        (0x7395C, 'adrp', 'x10, #0x103000'),
        (0x73960, 'stp', 'x9, x12, [sp, #0x40]'),
        (0x73964, 'adrp', 'x11, #0x77000'),
        (0x73968, 'stp', 'x8, x9, [sp, #0x20]'),
        (0x7396C, 'stp', 'x13, x9, [sp, #0x50]'),
        (0x73970, 'adrp', 'x8, #0x103000'),
        (0x73974, 'adrp', 'x9, #0x103000'),
        (0x73978, 'adrp', 'x12, #0x77000'),
        (0x7397C, 'add', 'x0, x0, #0xcda'),
        (0x73980, 'add', 'x10, x10, #0x670'),
        (0x73984, 'add', 'x11, x11, #0x12c'),
        (0x73988, 'add', 'x8, x8, #0x688'),
        (0x7398C, 'add', 'x9, x9, #0x690'),
        (0x73990, 'add', 'x12, x12, #0x130'),
        (0x73994, 'adrp', 'x14, #0x76000'),
        (0x73998, 'adrp', 'x15, #0x103000'),
        (0x7399C, 'adrp', 'x16, #0x103000'),
        (0x739A0, 'adrp', 'x17, #0x77000'),
        (0x739A4, 'stp', 'x0, x10, [sp, #0x80]'),
        (0x739A8, 'adrp', 'x10, #0x103000'),
        (0x739AC, 'stp', 'x11, x8, [sp, #0x90]'),
        (0x739B0, 'adrp', 'x8, #0x103000'),
        (0x739B4, 'stp', 'x9, x12, [sp, #0xa0]'),
        (0x739B8, 'adrp', 'x9, #0x77000'),
        (0x739BC, 'add', 'x14, x14, #0xf78'),
        (0x739C0, 'add', 'x15, x15, #0x65c'),
        (0x739C4, 'add', 'x16, x16, #0x660'),
        (0x739C8, 'add', 'x17, x17, #0x124'),
        (0x739CC, 'add', 'x10, x10, #0x6b8'),
        (0x739D0, 'add', 'x8, x8, #0x6c0'),
        (0x739D4, 'add', 'x9, x9, #0x134'),
        (0x739D8, 'stp', 'x14, x15, [sp, #0x60]'),
        (0x739DC, 'stp', 'x16, x17, [sp, #0x70]'),
        (0x739E0, 'stp', 'x10, x8, [sp, #0xb0]'),
        (0x739E4, 'str', 'x9, [sp, #0xc0]'),
        (0x739E8, 'ldr', 'x8, [x19]'),
        (0x739EC, 'add', 'x2, sp, #0x20'),
        (0x739F0, 'mov', 'w3, #7'),
        (0x739F4, 'mov', 'x0, x19'),
        (0x739F8, 'ldr', 'x8, [x8, #0x6b8]'),
        (0x739FC, 'mov', 'x1, x20'),
        (0x73A00, 'blr', 'x8'),
    ],
    'registration_class_descriptor': [
        (0x738CC, 'ldr', 'x9, [x19]'),
        (0x738D0, 'mov', 'x0, x19'),
        (0x738D4, 'ldr', 'x8, [x23]'),
        (0x738D8, 'ldr', 'x9, [x9, #0x30]'),
        (0x738DC, 'ldr', 'x8, [x8, #0x2d0]'),
        (0x738E0, 'ldr', 'x1, [x8, #8]'),
        (0x738E4, 'blr', 'x9'),
    ],
    'b2b_null_return': [
        (0x77124, 'mov', 'x0, xzr'),
        (0x77128, 'ret', ''),
    ],
    'm_ret_stub': [
        (0x7712C, 'ret', ''),
    ],
    'sa_ret_stub': [
        (0x77130, 'ret', ''),
    ],
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_record(record: bytes, offset: int, size: int) -> bytes:
    if not 0 <= offset <= len(record) or not 0 <= size <= len(record) - offset:
        raise ValueError("Protected token read leaves the exact record")
    return record[offset:offset + size]


def decode_cfg(record: bytes, image: bytes) -> dict:
    vm.expect(len(record), SIZE, "record-two length")
    vm.expect(record[0], 0x3B, "record-two header marker")
    # key and last opcode differ after a special reset marker.
    pending = deque([(4, record[1], None)])
    seen = set()
    nodes = []
    occupied = {}
    token_identities = {}
    terminal = []
    while pending:
        state = pending.popleft()
        if state in seen:
            continue
        if len(seen) >= MAX_STATES:
            raise ValueError("Protected static CFG exceeds its state bound")
        seen.add(state)
        position, key, last_opcode = state
        raw = read_record(record, position, 1)[0]
        special = (last_opcode is not None
                   and ((last_opcode + 0x7B) & 0xFF) < 5 and raw == 0x3B)
        opcode = 0x3B if special else vm.byte_transform(raw) ^ key ^ 0x5F
        if opcode not in HANDLERS:
            raise ValueError("Unsupported protected handler; static analysis stops")
        width, handler, _, meaning = HANDLERS[opcode]
        vm.expect(vm.pointer(image, 0xF3AF0 + opcode * 8), handler, "record dispatch target")
        operand_data = read_record(record, position + 1, width)
        operand = int.from_bytes(operand_data, "little") if width else None
        end = position + width + 1
        identity = (opcode, width, operand)
        if token_identities.setdefault(position, identity) != identity:
            raise ValueError("One token offset has incompatible rolling-key interpretations")
        for offset in range(position, end):
            owner = occupied.setdefault(offset, position)
            if owner != position:
                raise ValueError("Static CFG enters another token's operand bytes")
        successors = []
        next_key = operand if opcode == 0x3B else opcode
        if opcode == 0x01:
            terminal.append(position)
        elif opcode in (0x85, 0x86):
            delta = int.from_bytes(operand_data, "little", signed=True)
            destination = position + delta
            read_record(record, destination, 1)
            successors.append({"offset": destination,
                               "condition": "unconditional" if opcode == 0x85 else "byte nonzero"})
            if opcode == 0x86:
                read_record(record, end, 1)
                successors.append({"offset": end, "condition": "byte zero"})
        else:
            read_record(record, end, 1)
            successors.append({"offset": end, "condition": "next"})
        node = {"offset": position, "handler_index": hex(opcode),
                "handler": hex(handler), "meaning": meaning,
                "operand_bytes": width, "operand": operand,
                "special_marker": special, "successors": successors}
        nodes.append(node)
        for edge in successors:
            pending.append((edge["offset"], next_key, opcode))
    if sorted(occupied) != list(range(4, len(record))):
        raise ValueError("Static CFG does not cover every non-header record byte")
    vm.expect(sorted(set(terminal)), [181], "record-two terminal")
    return {"nodes": sorted(nodes, key=lambda node: node["offset"]),
            "unique_token_offsets": len({node["offset"] for node in nodes}),
            "states": len(seen), "covered_non_header_bytes": len(occupied),
            "terminal_offsets": sorted(set(terminal)),
            "runtime_branch_results_evaluated": 0}


def static_inspection(image: bytes, decoded: bytes, elf) -> dict:
    # Existing wrapper proof remains a separate static foundation, not new replay cases.
    foundation = vm.inspect(image, elf)
    rw = [(s[3], s[3] + s[6]) for s in elf.loads if s[1] & 2]
    relocated, _, _, _ = strings.relocate(elf, image, [(0, vm.RX_END), *rw], rw)
    vm.expect(struct.unpack("<ii", vm.read(relocated, TABLE + 16, 8)),
              (220, SIZE), "record-two descriptor offset and size")
    record = vm.read(relocated, START, SIZE)
    graph = decode_cfg(record, relocated)
    by_offset = {node["offset"]: node for node in graph["nodes"]}
    call_tokens = [(node["offset"], node["operand"]) for node in graph["nodes"]
                   if node["handler_index"] == "0x4d"]
    vm.expect(call_tokens, [(81, 1), (147, 2), (165, 3)], "record native call slots")
    for position, opcode, operand in ((6, "0x41", 30), (41, "0x33", 48),
            (76, "0x32", 0x10004), (101, "0x86", 8), (105, "0x85", 69),
            (111, "0x4c", 1), (121, "0x33", 120), (159, "0x4c", 0),
            (170, "0x85", 4), (176, "0x32", 0x10004)):
        vm.expect((by_offset[position]["handler_index"], by_offset[position]["operand"]),
                  (opcode, operand), "selected call/branch preparation token")
    engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    bounds = strings.function_bounds(elf, relocated, vm.RX_END)
    for label, checks in INSTRUCTION_CHECKS.items():
        for address, mnemonic, operands in checks:
            instruction = list(engine.disasm(vm.read(relocated, address, 4), address))
            vm.expect(len(instruction), 1, f"{label} instruction count")
            vm.expect((instruction[0].mnemonic, instruction[0].op_str),
                      (mnemonic, operands), label)
    handlers = []
    for opcode in sorted({int(node["handler_index"], 16) for node in graph["nodes"]}):
        width, start, end, meaning = HANDLERS[opcode]
        if not 0xBD624 <= start < end <= 0xC472C:
            raise ValueError("Selected handler leaves the audited interpreter function")
        data = vm.read(relocated, start, end - start)
        instructions = list(engine.disasm(data, start))
        vm.expect(sum(x.size for x in instructions), len(data), "complete native handler decode")
        handlers.append({"handler_index": hex(opcode), "operand_bytes": width,
                         "start": hex(start), "end_exclusive": hex(end),
                         "meaning": meaning, "sha256": digest(data)})
    functions = []
    for address in FUNCTIONS:
        if address not in bounds:
            raise ValueError("Selected native function lacks an exact supported FDE")
        start, end = bounds[address]
        data = vm.read(relocated, start, end - start)
        instructions = list(engine.disasm(data, start))
        vm.expect(sum(x.size for x in instructions), len(data), "complete native function decode")
        functions.append({"start": hex(start), "end_exclusive": hex(end),
            "bytes": len(data), "sha256": digest(data),
            "direct_calls": sum(x.mnemonic == "bl" for x in instructions),
            "indirect_calls": sum(x.mnemonic == "blr" for x in instructions),
            "system_call_instructions": sum(x.mnemonic == "svc" for x in instructions)})
    methods = []
    for name_offset, signature_offset, function, expected_name, expected_signature in METHODS:
        def fixed_string(offset: int, expected: str) -> None:
            data = expected.encode() + b"\0"
            vm.expect(vm.read(decoded, offset, len(data)), data, "selected JNI literal")
        fixed_string(name_offset, expected_name)
        fixed_string(signature_offset, expected_signature)
        methods.append({"name_offset": hex(name_offset), "signature_offset": hex(signature_offset),
            "name": expected_name, "signature": expected_signature, "function": hex(function)})
    vm.expect(vm.pointer(relocated, 0x1135D0 + 8), 0xE8040, "three-argument thunk")
    vm.expect(vm.pointer(relocated, 0x1135D0 + 16), 0xE80C8, "word-return one-argument thunk")
    vm.expect(vm.pointer(relocated, 0x1135D0 + 24), 0xE8128, "byte-return one-argument thunk")
    vm.expect(vm.pointer(relocated, 0x1135B0), 0x73864, "JNI registration target")
    vm.expect(vm.pointer(relocated, 0x1135B0 + 8), 0xFD220, "runtime-context pointer slot")
    return {"scope": "Static CFG and call preparation only; no VM/JNI/native execution",
        "guest_instructions_executed": 0,
        "foundation_instruction_checks": sum(x["count"] for x in foundation["instruction_checks"].values()),
        "new_instruction_checks": {label: {"count": len(checks),
            "addresses": [hex(x[0]) for x in checks]} for label, checks in INSTRUCTION_CHECKS.items()},
        "record": {"start": hex(START), "end_exclusive": hex(START + SIZE),
                   "bytes": SIZE, "sha256": digest(record), "cfg": graph},
        "handler_widths": handlers, "selected_native_functions": functions,
        "native_call_preparations": [
            {"record_offset": 81, "thunk": "0xe8040", "thunk_call": "0xe80b0",
             "target": "symbolic JavaVM.functions[0x30] = GetEnv",
             "arguments": ["original JavaVM", "address of local JNIEnv* cell", "JNI_VERSION_1_4 (0x10004)"],
             "return": "unknown jint"},
            {"record_offset": 147, "thunk": "0xe80c8", "thunk_call": "0xe8110",
             "target": "symbolic (*runtime_context_pointer).callback[0x78]",
             "runtime_context_pointer_slot": "0xfd220", "arguments": ["original JavaVM"],
             "return": "unknown word; stored without controlling the next call"},
            {"record_offset": 165, "thunk": "0xe8128", "thunk_call": "0xe8170",
             "target": "0x73864", "arguments": ["JNIEnv* from GetEnv local cell"],
             "return": "unknown low byte; stored without controlling the return literal"}],
        "jni_registration_candidate": {"function": "0x73864", "register_call": "0x73a00",
            "entries": methods, "count": 7,
            "class": "runtime context descriptor field +0x08; unresolved",
            "b2b_result": "exact null-return stub", "m_and_sa_result": "exact RET stubs"},
        "return_literal": "0x10004 on both statically decoded GetEnv branches",
        "limits": ["No runtime branch or indirect context callback resolved",
            "No Java class or JNI/native method executed",
            "Class-loader and Application registration paths need further static tracing",
            "No plaintext SDK DEX, action serializer or station command recovered"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-apk", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--initialized-image", type=Path, required=True,
                        help="Exact private libexec snapshot after the earlier 49 pure string initializers")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.base_apk.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("APK exceeds static input bound")
    apk = args.base_apk.read_bytes()
    vm.expect(digest(apk), carrier.APK_SHA256, "base APK SHA-256")
    image_path = args.images_dir / vm.IMAGE_NAME
    vm.expect(image_path.stat().st_size, vm.IMAGE_SIZE, "unpacked image size")
    vm.expect(args.initialized_image.stat().st_size, vm.IMAGE_SIZE, "initialized image size")
    image, decoded = image_path.read_bytes(), args.initialized_image.read_bytes()
    vm.expect(digest(image), vm.IMAGE_SHA, "unpacked image SHA-256")
    vm.expect(digest(decoded), DECODED_SHA, "initialized image SHA-256")
    with zipfile.ZipFile(BytesIO(apk)) as archive:
        info = archive.getinfo(vm.LIBRARY)
        if info.file_size > 2 * 1024 * 1024:
            raise ValueError("Packed library exceeds static input bound")
        packed = archive.read(vm.LIBRARY)
    vm.expect(digest(packed), carrier.LIBRARIES[vm.LIBRARY], "packed library SHA-256")
    result = static_inspection(image, decoded, carrier.LoadElf(packed))
    sources = (Path(__file__), Path(vm.__file__), Path(strings.__file__), Path(carrier.__file__))
    manifest = {"tool": Path(__file__).name, "apk_sha256": digest(apk),
        "packed_library_sha256": digest(packed), "image_sha256": digest(image),
        "initialized_image_sha256": digest(decoded),
        "source_sha256": {p.name: digest(p.read_bytes()) for p in sources},
        "runtime": {"python": platform.python_version(), "capstone": capstone.__version__},
        "guest_instructions_executed": 0}
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix, value in (("results", result), ("manifest", manifest)):
        path = args.output_dir / f"android-loader-record-two-{suffix}.json"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"cfg_states": result["record"]["cfg"]["states"],
                     "covered_record_bytes": result["record"]["cfg"]["covered_non_header_bytes"],
                     "new_instruction_checks": sum(len(x) for x in INSTRUCTION_CHECKS.values()),
                     "guest_instructions_executed": 0}))


if __name__ == "__main__":
    main()
