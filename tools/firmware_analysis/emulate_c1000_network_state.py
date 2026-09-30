#!/usr/bin/env python3
"""Offline original C1000 MCU network ACK/state replay; no device transport."""

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4

from emulate_c1000_original_commands import CTX, IMAGE, IMAGE_SHA256, Machine, PAYLOAD, RAM

NETWORK = 0x20000054
MODULE = 0x20000c6c
TIMERS = 0x20004e28


class NetworkMachine(Machine):
    def __init__(self, image):
        self.transports = []
        self.persistent_writes = []
        super().__init__(image)

    def reset(self):
        super().reset()
        self.transports, self.persistent_writes = [], []

    def step(self, uc, address, size, data):
        if address == 0x08012b38:
            self.transports.append({"function": uc.reg_read(UC_ARM_REG_R0),
                                    "command": uc.reg_read(UC_ARM_REG_R1),
                                    "payload": bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R2),
                                                                 uc.reg_read(UC_ARM_REG_R3))).hex()})
            self.back(0)
        elif address == 0x0800d6b0:
            self.persistent_writes.append({"domain": uc.reg_read(UC_ARM_REG_R0),
                                           "key": uc.reg_read(UC_ARM_REG_R1),
                                           "value": bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R3),
                                                                        uc.reg_read(UC_ARM_REG_R2))).hex()})
            self.back(0)
        else:
            super().step(uc, address, size, data)

    def put(self, address, value, fmt="I"):
        self.uc.mem_write(address, struct.pack("<"+fmt, value))

    def get(self, address, fmt="I"):
        return struct.unpack("<"+fmt, self.uc.mem_read(address, struct.calcsize(fmt)))[0]

    def assert_changes(self, before, values):
        expected = bytearray(before)
        for address, value in values.items():
            expected[address-RAM:address-RAM+len(value)] = value
        assert self.low_ram() == expected


def run_suite(image):
    m = NetworkMachine(image)
    rows, counts = [], Counter()

    def record(group, values):
        rows.append([group, values])
        counts[group] += 1

    table = {r['opcode']: r['handler'] for r in m.table(0x20000154, 32)}
    assert table['0825'] == '0800c2bc' and table['0824'] == '0800a9b0'
    assert table['0027'] == '0800c2c8'
    for payload, seed in itertools.product(range(256), (0, 1, 255)):
        m.reset()
        m.put(PAYLOAD, payload, "B")
        m.put(NETWORK+0x12, seed, "B")
        before = m.low_ram()
        m.run(0x0800c2bc, CTX)
        m.assert_changes(before, {NETWORK+0x12: b'\x01'})
        assert not m.calls and not m.transports and not m.persistent_writes
        record('0825_payload_ignored', [payload, seed, 1])
    m.reset()
    before = m.low_ram()
    m.run(0x0800c2bc, 0)  # The handler does not dereference its context argument.
    m.assert_changes(before, {NETWORK+0x12: b'\x01'})
    record('0825_null_context_not_read', True)

    for payload in range(256):
        m.reset()
        m.put(PAYLOAD, payload, "B")
        before = m.low_ram()
        m.run(0x0800a9b0, CTX)
        m.assert_changes(before, {})
        expected = {'domain': 0, 'key': 0xd6, 'value': '5500' if payload == 0 else '0000'}
        assert m.persistent_writes == [expected]
        record('0824_zero_vs_nonzero', [payload, expected])

    builder_frames = []
    for enable, mode in itertools.product((0, 1), (0, 1, 2)):
        m.reset()
        m.put(MODULE+0x9e, mode, 'B')
        before = m.low_ram()
        m.run(0x080111fc, enable)
        m.assert_changes(before, {})
        expected = {'function': 0x10, 'command': 0x25,
                    'payload': bytes((0xa1, 1, enable, 0xa2, 1, mode)).hex()}
        assert m.transports == [expected]
        builder_frames.append(expected)
        record('internal_0025_builder', [enable, mode, expected])

    for state, permit in itertools.product((0, 1, 2, 3, 0x26, 255), (0, 1)):
        m.reset()
        m.put(PAYLOAD+2, state, 'B')
        m.put(NETWORK+0xf, permit, 'B')
        m.put(NETWORK+0xc, 0, 'B')
        m.put(NETWORK+0x10, 0, 'B')
        m.put(MODULE+0xc4, 0xff, 'B')
        m.put(MODULE+0x9e, 2, 'B')
        before = m.low_ram()
        m.run(0x0800c2c8, CTX)
        updates = {NETWORK+0xa: bytes((state,))}
        if state == 0:
            updates |= {NETWORK+0xc: b'\x01', NETWORK+0x10: b'\x01', MODULE+0xc4: b'\xfc'}
            if permit:
                updates[MODULE+0x9e] = b'\x01'
        m.assert_changes(before, updates)
        assert m.replies == [b'\0']
        assert m.transports == ([{'function': 16, 'command': 37, 'payload': 'a10101a20101'}] if state == 0 and permit else [])
        record('module_0027_state', [state, permit, m.transports])

    # Full startup handshake poller, but only state6 waiting/ACK branches.
    for prior_stage, now, acknowledged, retries in itertools.product(
            range(1, 6), (9998, 9999, 10000, 10001), (0, 1), (0, 1, 3)):
        m.reset()
        m.put(NETWORK+0x10, 6, 'B')
        m.put(NETWORK+0x11, prior_stage, 'B')
        m.put(NETWORK+0x12, acknowledged, 'B')
        m.put(NETWORK+0x5c, 9999)
        m.put(NETWORK+0x60, retries)
        m.put(0x20000704, now)
        before = m.low_ram()
        m.run(0x08028704)
        state, remaining = 6, retries
        if now > 9999:
            state, remaining = (prior_stage, retries-1) if retries else (0, 3)
        if acknowledged:
            state = prior_stage+1 if prior_stage < 4 else (5 if prior_stage == 4 and now < 10000 else 0)
            remaining = 3
        m.assert_changes(before, {NETWORK+0x10: bytes((state,)), NETWORK+0x12: b'\0',
                                   NETWORK+0x60: struct.pack('<I', remaining)})
        assert not m.transports and not m.persistent_writes
        record('startup_wait_ack_and_retry', [prior_stage, now, acknowledged, retries, state, remaining])

    m.reset()
    m.uc.mem_write(TIMERS, bytes(70*20))
    m.put(TIMERS, 1, 'B')  # Startup reserves timer0; simulate that reservation.
    m.put(MODULE+0xb7, 0, 'B')
    m.put(MODULE+0xb8, 0, 'B')
    m.run(0x08028578, stop=0x080285a2, registers={UC_ARM_REG_R4: MODULE})
    timers = []
    for offset, expected_id, callback in ((0xb7, 1, 0x08011275), (0xb8, 2, 0x08007b41)):
        identifier = m.get(MODULE+offset, 'B')
        record_address = TIMERS+20*identifier
        assert identifier == expected_id
        assert m.get(record_address, 'B') == 1
        assert m.get(record_address+1, 'B') == 0
        assert m.get(record_address+4) == 300000
        assert m.get(record_address+0xc) == callback
        timers.append({'context_offset': f'{offset:02x}', 'period_ticks': 300000,
                       'repeating': False, 'callback': f'{callback & ~1:08x}'})
        record('network_window_timer_registration', timers[-1])

    return {'model': 'Original C1000 A1761', 'main_version': '1.5.9',
            'input_sha256': IMAGE_SHA256,
            'environment': {'python': platform.python_version(), 'unicorn': unicorn.__version__},
            'cases': dict(counts) | {'total': len(rows)},
            'cases_sha256': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest(),
            '0825_observation': 'All payload-byte values set generic startup ACK flag; the payload/context is not read.',
            '0824_observation': 'Zero maps to5500 persistence marker; every nonzero byte maps to0000.',
            'internal_0025_frames': builder_frames, 'network_timers': timers,
            'substitutions': {'08012b38': 'capture MCU-to-module transport; no send',
                              '0800d6b0': 'capture configuration persistence marker; no storage write',
                              '080225c4': 'capture module response; existing helper'},
            'limits': ['Installed main1.5.1 differs from analyzed1.5.9.',
                       'Original radio firmware0.1.3.0 is unavailable; no HTTP/MQTT parser or error producer runs.',
                       'Function10/0025 is internal radio control, not app function0f/0025 activation.',
                       'Startup waiting state is replayed with synthetic elapsed ticks and flags.',
                       'Button handlers and timer callbacks were inspected statically, not executed.',
                       'No safe remote reset-free bootstrap recovery command is established.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=IMAGE)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(run_suite(args.firmware.read_bytes()), indent=2, sort_keys=True)+'\n'
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end='')


if __name__ == '__main__':
    main()
