"""Offline A1763 internal UART queue, worker and receive-completion replay.

Executes actual ARM ring queues, CRC, serializers, worker, parser and selected
callbacks. UART send, allocation/free, timer installation, logging, memory
helpers and alarm/display delivery are recorded substitutes. Synthetic internal
frames only; no public command, radio, real UART or device is accessed.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR)

from emulate_clock_semantics import ClockMachine, STACK, STOP
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT

os.umask(0o077)
ACTIVE = 0x200034e8
STATE = 0x200003a4
MODULE = 0x20003f00
COMM = 0x200003e8
SETTINGS = 0x20001d48
SCRATCH = 0x21001000
RX = SCRATCH+0x400
ALLOC = SCRATCH+0x800
CALLBACK = 0x08005010


def crc16(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value


def response(register=0x100, payload=b'\x30\0', *, selector=1, operation=0x1001):
    raw = b'main' + struct.pack('<6H', 0x10, selector, len(payload)+6,
                               operation, register, len(payload)) + payload
    return raw + struct.pack('<H', crc16(raw))


class WorkerMachine(ClockMachine):
    def __init__(self, *, tx_return=1):
        super().__init__()
        self.uc.mem_map(SCRATCH, 0x1000)
        self.tx_return = tx_return
        self.transmitted = []
        self.freed = []
        self.completions = []
        self.errors = []
        self.refresh = []
        self.timer_registrations = []
        self.timer_starts = []
        self.worker_calls = 0
        self.run(0x0800e334)
        assert self.timer_registrations == [{'period_argument': 10, 'callback': '0801b401'}]
        self.uc.mem_write(0x20000164, struct.pack('<I', 0x31))
        self.settings = bytes(self.uc.mem_read(SETTINGS, 0x190))
        self.output_flags = bytes(self.uc.mem_read(0x20000164, 4))

    def step(self, uc, address, size, data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in
            (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        if address == 0x080107f4:
            self.timer_registrations.append({'period_argument': r0, 'callback': f'{r1:08x}'})
            uc.mem_write(r3, b'\x2a'); self.back(0)
        elif address == 0x0801095c:
            self.timer_starts.append(r0); self.back(0)
        elif address == 0x08008f2c:
            assert r0 == 2
            raw = bytes(uc.mem_read(r1, r2))
            assert raw[:4] == b'MAIN' and crc16(raw) == 0
            self.transmitted.append(raw)
            self.back(self.tx_return)
        elif address == 0x08020a4c:
            assert 0 < r0 <= 64
            self.back(ALLOC)
        elif address == 0x08017a8c:
            self.freed.append(r0); self.back()
        elif address in (CALLBACK, 0x0801b2d4, 0x0801649c):
            self.completions.append({'callback': f'{address:08x}', 'accepted': r0,
                'payload_hex': bytes(uc.mem_read(r1, r2)).hex() if r1 else None,
                'length': r2})
            if address == CALLBACK:
                self.back()
        elif address == 0x08007030:
            self.errors.append(r0); self.back(1)
        elif address == 0x0800fbe4:
            self.refresh.append(r0); self.back()
        elif address == 0x080052a8:
            assert r2 <= 512
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2))); self.back(r0)
        elif address == 0x080052da:
            assert r1 <= 512
            uc.mem_write(r0, bytes(r1)); self.back(r0)
        elif address == 0x08005400:
            self.back(0 if bytes(uc.mem_read(r0, r2)) == bytes(uc.mem_read(r1, r2)) else 1)
        elif any(lo <= address < hi for lo, hi in (
                (0x0800e334, 0x0800e374), (0x0800f454, 0x0800f55a),
                (0x08027e40, 0x08027ebc), (0x0801b400, 0x0801b50e),
                (0x0801b518, 0x0801b548), (0x0801b54c, 0x0801b6b2),
                (0x0801b720, 0x0801b7de), (0x0800d300, 0x0800d330),
                (0x0802a210, 0x0802a228), (0x0802a4fc, 0x0802a512),
                (0x0801b2d4, 0x0801b330), (0x0801649c, 0x080164b2),
                (0x0802a590, 0x0802a752), (0x08016600, 0x08016638))):
            return
        else:
            super().step(uc, address, size, data)

    def run(self, address, r0=0, r1=0, r2=0):
        self.stopped = False
        self.uc.reg_write(UC_ARM_REG_R0, r0)
        self.uc.reg_write(UC_ARM_REG_R1, r1)
        self.uc.reg_write(UC_ARM_REG_R2, r2)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=60000)
        assert self.stopped
        return self.uc.reg_read(UC_ARM_REG_R0)

    def queue(self, register=0x100, *, length=146, write=False, priority=1,
              insertion=0, callback=CALLBACK, selector=1):
        payload = bytes((i & 255 for i in range(length))) if write else b''
        self.uc.mem_write(ALLOC, payload)
        descriptor = struct.pack('<IIHHH2xIB3x', callback | 1, 0, selector,
                                 register, length, ALLOC if write else 0, int(write))
        assert len(descriptor) == 24
        self.uc.mem_write(SCRATCH, descriptor)
        return self.run(0x08027e40, priority, SCRATCH, insertion)

    def worker(self, count=1):
        for _ in range(count):
            self.run(0x0801b400)
            self.worker_calls += 1

    def receive(self, raw):
        assert len(raw) <= 500
        self.uc.mem_write(RX, bytes(500))
        self.uc.mem_write(RX, raw)
        self.uc.mem_write(STATE+0x14, struct.pack('<IHH', RX, len(raw), 0))
        self.run(0x0801b54c)

    def state(self):
        assert bytes(self.uc.mem_read(SETTINGS, 0x190)) == self.settings
        assert bytes(self.uc.mem_read(0x20000164, 4)) == self.output_flags
        raw = bytes(self.uc.mem_read(ACTIVE, 24))
        return {'busy': self.uc.mem_read(STATE, 1)[0],
            'remaining_worker_invocations': int.from_bytes(raw[4:8], 'little'),
            'descriptor_register': int.from_bytes(raw[10:12], 'little'),
            'descriptor_cleared': raw == bytes(24),
            'rx_stored_bytes': int.from_bytes(self.uc.mem_read(STATE+0x18, 2), 'little'),
            'transmissions': len(self.transmitted), 'completions': list(self.completions),
            'settings_and_output_flags_preserved': True}


def main():
    results = {'send_timeout': [], 'response_correlation': [], 'queue_order': [],
               'real_callbacks': []}
    for write, lengths in ((False, (0, 2, 12, 50, 146)), (True, (0, 2, 12, 50, 51))):
        for length in lengths:
            for tx_return in (0, 1):
                m = WorkerMachine(tx_return=tx_return)
                assert m.queue(0x16 if write else 0x100, length=length, write=write) == 1
                deadline = (length*6)//100+13
                m.worker()
                first = m.state()
                assert first['busy'] == 1 and first['remaining_worker_invocations'] == deadline-1
                expected_sends = int(not write or length <= 50)
                assert len(m.transmitted) == expected_sends
                assert len(m.freed) == int(write)
                m.worker(deadline-2)
                assert m.state()['remaining_worker_invocations'] == 1 and not m.completions
                m.worker()
                assert m.completions == [{'callback': f'{CALLBACK:08x}',
                    'accepted': 0, 'payload_hex': None, 'length': 0}]
                assert m.state()['descriptor_cleared'] and m.state()['busy'] == 0
                m.worker(3)
                assert len(m.transmitted) == expected_sends and len(m.completions) == 1
                if m.transmitted:
                    header = struct.unpack_from('<6H', m.transmitted[0], 4)
                    assert header == (0x10, 1, length+6 if write else 6,
                                      0x1002 if write else 0x1001,
                                      0x16 if write else 0x100, length)
                results['send_timeout'].append({'write': write, 'requested_bytes': length,
                    'substituted_send_return': tx_return, 'timeout_invocation': deadline,
                    'first': first, 'final': m.state(), 'payload_freed_count': len(m.freed)})

    scenarios = [
        ('read_match', False, response(), 1, True),
        ('read_other_selector', False, response(selector=4), 1, True),
        ('read_other_register', False, response(register=0x126), None, True),
        ('write_success', True, response(register=0x16, operation=0x1002, payload=b'\xaa\xee'), 1, True),
        ('write_rejected', True, response(register=0x16, operation=0x1002, payload=b'\xaa\x00'), 0, True),
        ('write_other_selector', True, response(register=0x16, selector=4, operation=0x1002, payload=b'\xaa\xee'), 1, True),
        ('write_other_register', True, response(register=0x15, operation=0x1002, payload=b'\xaa\xee'), None, True),
        ('read_response_to_write', True, response(register=0x16), 1, True),
        ('write_response_to_read', False, response(operation=0x1002, payload=b'\xaa\xee'), 1, True),
        ('unknown_operation', False, response(operation=0x1003), None, True),
        ('bad_crc', False, response()[:-1]+bytes((response()[-1]^1,)), None, True),
        ('partial_header', False, response()[:17], None, False),
        ('partial_body', False, response(payload=bytes(20))[:20], None, False),
        ('no_magic', False, bytes(20), None, False),
        ('read_short_payload', False, response(payload=b'\x30'), 1, True),
    ]
    for name, write, raw, accepted, releases in scenarios:
        m = WorkerMachine()
        assert m.queue(0x16 if write else 0x100, length=12 if write else 146, write=write) == 1
        m.worker()
        m.receive(raw)
        after_rx = m.state()
        assert after_rx['busy'] == int(not releases)
        assert len(m.completions) == int(accepted is not None)
        if accepted is not None:
            assert m.completions[0]['accepted'] == accepted
        copied = bytes(m.uc.mem_read(MODULE, 146))
        if name in ('read_match', 'read_short_payload'):
            assert copied[:1] == b'\x30'
        elif name == 'read_other_register':
            assert copied[0x4c:0x4e] == b'\x30\0'
        elif name == 'read_other_selector':
            assert copied == bytes(146)
        m.worker(25)
        assert m.state()['busy'] == 0 and m.state()['descriptor_cleared']
        if releases:
            assert len(m.completions) == int(accepted is not None)
        else:
            assert m.completions[-1]['accepted'] == 0
        assert len(m.transmitted) == 1
        results['response_correlation'].append({'scenario': name,
            'synthetic_frame_hex': raw.hex(), 'after_receive': after_rx,
            'after_25_worker_calls': m.state(),
            'cache_changed': copied != bytes(146)})

    # Actual queues hold20 descriptor slots with one sentinel slot unused.
    # Exercise FIFO/prepend/priority and full-queue replacement without any TX.
    for priority in (0, 1):
        for insertion in (0, 1, 2):
            m = WorkerMachine()
            for register in range(19):
                assert m.queue(register, priority=priority) == 1
            accepted = m.queue(0x7f, priority=priority, insertion=insertion)
            assert accepted == int(insertion == 2)
            m.run(0x0801b518, SCRATCH, 1)
            first = int.from_bytes(m.uc.mem_read(SCRATCH+10, 2), 'little')
            assert first == int(insertion == 2)
            assert not m.completions and not m.freed
            results['queue_order'].append({'priority': priority, 'insertion': insertion,
                'usable_slots': 19, 'full_queue_accepts': accepted,
                'first_remaining_register': first, 'eviction_invokes_callback_or_free': False})
    m = WorkerMachine()
    assert m.queue(0x100, priority=0) and m.queue(0x101, priority=1)
    assert m.queue(0x102, priority=1, insertion=1)
    order = []
    for _ in range(3):
        assert m.run(0x0801b518, SCRATCH, 1) == 1
        order.append(int.from_bytes(m.uc.mem_read(SCRATCH+10, 2), 'little'))
    assert order == [0x102, 0x101, 0x100]
    results['queue_order'].append({'mixed_priority_prepend_pop_order': order})

    # Drive the real status callback through the parser and the actual worker.
    m = WorkerMachine()
    m.run(0x08016600)
    m.worker()
    m.receive(response(payload=bytes([0xa5])*146))
    assert m.uc.mem_read(COMM+2, 1)[0] == 1
    for _ in range(6):
        m.run(0x08016600); m.worker()
        m.receive(response(register=0x126, payload=b'\x30\0'))
        m.worker()
    after_mismatches = m.state()
    assert len(m.completions) == 1 and m.uc.mem_read(COMM+2, 1)[0] == 1
    assert bytes(m.uc.mem_read(MODULE, 146)) != bytes(146)
    for _ in range(6):
        m.run(0x08016600); m.worker(21)
    assert [c['accepted'] for c in m.completions] == [1]+[0]*6
    assert bytes(m.uc.mem_read(MODULE, 146)) == bytes(146)
    assert m.refresh == [10] and m.errors == [25]
    results['real_callbacks'].append({'name': 'status_mismatch_vs_timeout',
        'after_six_mismatches': after_mismatches, 'after_six_timeouts': m.state(),
        'cache_cleared_after_timeouts': True, 'error_requests': m.errors,
        'refresh_requests': m.refresh})

    for accepted in (False, True):
        m = WorkerMachine()
        m.uc.mem_write(SCRATCH, struct.pack('<6H', 100, 400, 1400, 580, 2800, 4000))
        assert m.run(0x0802a664, 2, SCRATCH, 0x0801649d) == 1
        m.worker()
        m.receive(response(register=0x16, operation=0x1002,
                           payload=b'\xaa\xee' if accepted else b'\0\0'))
        assert m.completions[-1]['accepted'] == accepted
        m.worker()
        assert len(m.transmitted) == 1+accepted
        if accepted:
            raw = m.transmitted[-1]
            assert int.from_bytes(raw[12:14], 'little') == 0x15 and raw[16:18] == b'\x57\0'
        results['real_callbacks'].append({'name': 'configuration_completion',
            'write_ack_accepted': accepted, 'start_queued_after_ack': accepted,
            'state': m.state()})

    counts = {key: len(value) for key, value in results.items()}
    assert counts == {'send_timeout': 20, 'response_correlation': 15,
                      'queue_order': 7, 'real_callbacks': 3}
    out = ROOT/'uart-request-worker-results.json'
    out.write_text(json.dumps(results, indent=2)+'\n'); out.chmod(0o600)
    names = (Path(__file__).name, 'emulate_clock_semantics.py', 'replay_io.py')
    manifest = {'hardware_access': False, 'case_counts': counts,
        'firmware': {'filename': FIRMWARE_NAME, 'sha256': FIRMWARE_SHA256},
        'runtime': {'python': platform.python_version(), 'unicorn': unicorn.__version__},
        'source_sha256': {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                          for name in names},
        'result_sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
        'substitutions': ['UART port2 send and return value', 'allocation/free',
            'timer registration/start', 'logging', 'memcpy/memset/memcmp',
            'alarm/display delivery', 'synthetic RX buffer and worker invocation schedule'],
        'executed': ['queue init/push/prepend/pop/overflow replacement',
            'request worker and countdown', 'read/write serializers and CRC16',
            'receive parser and correlation', 'bulk status and configuration callbacks',
            'configuration/start builders', 'communication counters'],
        'not_executed': ['UART ISR/DMA/framing arrival', 'physical peripheral or DSP',
            'timer ISR/scheduler', 'wall-clock timing', 'higher policy retry task',
            'public app/radio transport', 'concurrent interrupt interleavings']}
    path = ROOT/'uart-request-worker-manifest.json'
    path.write_text(json.dumps(manifest, indent=2)+'\n'); path.chmod(0o600)
    print(json.dumps({'cases': sum(counts.values()), 'groups': counts, 'hardware_access': False}))


if __name__ == '__main__':
    main()
