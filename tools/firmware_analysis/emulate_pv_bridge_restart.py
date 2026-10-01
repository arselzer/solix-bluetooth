"""Offline A1763 MPPT restart timer and DSP-status failure callback replay.

Actual ARM task, timer service, getter, configuration builder and completion
callbacks execute against synthetic RAM. Queue/allocation, logging, diagnostic
collection, event delivery and memory helpers are substitutes. No physical
transport, public command, DSP actuator or wall-clock timing is exercised.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_pv_retry_actuation import ActuationMachine, dsp_words, DSP_NAME, DSP_HASH
from emulate_general_settings import SETTINGS, TIMERS
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)
TASK = 0x20000010
MODULE = 0x20003f00
COMM = 0x200003e8
TICK = 0x200008d0
FLAGS = 0x2000039a
TIMER_ID = 5


def direct_thumb_calls(image, target):
    """Aligned BL scan only; literal data and indirect calls are not a CFG."""
    calls = []
    for pos in range(0, len(image)-3, 2):
        first, second = struct.unpack_from('<HH', image, pos)
        if first & 0xf800 != 0xf000 or second & 0xd000 != 0xd000:
            continue
        sign = (first >> 10) & 1
        i1 = 1 ^ (((second >> 13) & 1) ^ sign)
        i2 = 1 ^ (((second >> 11) & 1) ^ sign)
        delta = ((sign << 24) | (i1 << 23) | (i2 << 22)
                 | ((first & 0x3ff) << 12) | ((second & 0x7ff) << 1))
        if sign:
            delta -= 1 << 25
        if 0x08005000 + pos + 4 + delta == target:
            calls.append(0x08005000 + pos)
    return calls


class RestartMachine(ActuationMachine):
    def __init__(self, *, active=True, running=False, configured=True):
        super().__init__(active=active)
        # Isolate the real single-shot MPPT sampling timer; no unrelated
        # scheduler callbacks. The timer period is the firmware's 1000 ticks.
        self.uc.mem_write(TIMERS, bytes(90*20))
        self.uc.mem_write(TIMERS + TIMER_ID*20,
                          struct.pack('<BB2xIIII', 2, 0, 1000, 0, 0, 0))
        self.uc.mem_write(TASK+1, bytes((configured, configured, 0, TIMER_ID)))
        self.uc.mem_write(FLAGS, b'\0')  # Exclude the separately traced weak-light lock.
        self.uc.mem_write(MODULE+0x4c, bytes((0x10 if running else 0,)))
        self.uc.mem_write(0x200018d0+14, b'\0\0')
        self.uc.mem_write(COMM+1, bytes(3))
        self.errors = []
        self.refresh_requests = []
        self.trace = set()
        self.protected = bytes(self.uc.mem_read(SETTINGS, 0x190))
        self.outputs = bytes(self.uc.mem_read(0x20000164, 4))

    def step(self, uc, address, size, data):
        self.trace.add(address) if hasattr(self, 'trace') else None
        r0, r1, r2 = (uc.reg_read(r) for r in
                       (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if address == 0x08027e40:
            assert r0 in (0, 1) and r2 == 0
            raw = bytes(uc.mem_read(r1, 24))
            length, = struct.unpack_from('<H', raw, 12)
            ptr, = struct.unpack_from('<I', raw, 16)
            self.queued.append({'priority': r0,
                'selector': int.from_bytes(raw[8:10], 'little'),
                'register': int.from_bytes(raw[10:12], 'little'),
                'payload_hex': bytes(uc.mem_read(ptr, length)).hex(),
                'callback': f"{int.from_bytes(raw[:4], 'little'):08x}",
                'accepted_by_substitute': self.queue_ok})
            self.back(int(self.queue_ok))
        elif address == 0x0802666c:
            self.boundaries.append('MPPT saved diagnostic record'); self.back()
        elif address == 0x08007030:
            self.errors.append(r0); self.back(1)
        elif address == 0x0800fbe4:
            self.refresh_requests.append(r0); self.back()
        elif any(lo <= address < hi for lo, hi in (
                (0x0801089c, 0x080108f0), (0x080108f4, 0x08010924),
                (0x0801095c, 0x08010994), (0x08010eb0, 0x08010eb6),
                (0x080292cc, 0x0802932a), (0x0801992c, 0x08019934),
                (0x0801b2d4, 0x0801b330), (0x08016570, 0x08016594))):
            return
        else:
            super().step(uc, address, size, data)

    def now(self, ticks):
        self.uc.mem_write(TICK, struct.pack('<I', ticks & 0xffffffff))

    def tick_task(self, ticks):
        self.now(ticks)
        self.run(0x0801089c, 0)
        self.mppt()

    def snapshot(self):
        assert bytes(self.uc.mem_read(SETTINGS, 0x190)) == self.protected
        assert bytes(self.uc.mem_read(0x20000164, 4)) == self.outputs
        task = bytes(self.uc.mem_read(TASK, 5))
        timer = bytes(self.uc.mem_read(TIMERS + TIMER_ID*20, 20))
        return {'configuration_queued_latch': task[1], 'stop_needed_latch': task[2],
                'sample_counter': task[3], 'timer_state': timer[0],
                'timer_started_tick': struct.unpack_from('<I', timer, 8)[0],
                'module_running_bit4': (self.uc.mem_read(MODULE+0x4c, 1)[0] >> 4) & 1,
                'queued': list(self.queued), 'saved_settings_and_output_flags_preserved': True}


def run_suite():
    results = {'timer_and_feedback': [], 'queue_and_completion': [],
               'status_failure_callback': [], 'parameter_callback': []}
    # At 10 completed sampling periods the configured latch is retained.
    # At 11, absent running feedback resets it. High feedback retains it.
    for running in (False, True):
        for ticks in (0, 999, 1000, 10000, 10999, 11000, 11999, 12000):
            m = RestartMachine(running=running)
            checkpoints = sorted(set([*range(1000, ticks+1, 1000), ticks]))
            for point in checkpoints:
                m.tick_task(point)
            s = m.snapshot()
            if ticks < 11000:
                assert s['configuration_queued_latch'] == 1
                assert s['sample_counter'] == ticks//1000
            elif ticks == 11000:
                assert s['configuration_queued_latch'] == int(running)
                assert s['sample_counter'] == (30 if running else 0)
            elif ticks == 11999:
                assert s['configuration_queued_latch'] == 1
                assert s['sample_counter'] == (30 if running else 0)
            else:
                assert s['configuration_queued_latch'] == 1
                assert s['sample_counter'] == (30 if running else 1)
            configs = [q for q in m.queued if q['callback'] == '0801649d']
            assert len(configs) == int(not running and ticks > 11000)
            # The task's timer branch sends register0016's first word even
            # when no configuration/restart is needed. It is not a poll read.
            assert sum(q['callback'] == '00000000' for q in m.queued) == ticks//1000
            results['timer_and_feedback'].append({'running_feedback_held_constant': running,
                'synthetic_ticks': ticks, 'actual_instructions': s})

    # Failed allocation/enqueue leaves config latch clear: next invocation
    # retries immediately, independent of the timer. The stop-needed latch is
    # nevertheless set even when allocation fails.
    for fail_kind in ('allocation', 'queue', 'completion', 'lost_completion', 'start_queue'):
        m = RestartMachine(configured=False)
        if fail_kind == 'allocation':
            m.allocation_ok = False
        elif fail_kind == 'queue':
            m.queue_ok = False
        m.mppt()
        first = m.snapshot()
        if fail_kind in ('allocation', 'queue'):
            assert first['configuration_queued_latch'] == 0
            assert first['stop_needed_latch'] == 1
            m.allocation_ok = m.queue_ok = True
            m.mppt()
            assert m.snapshot()['configuration_queued_latch'] == 1
            m.run(0x0801649c, 1)
            assert m.queued[-1]['payload_hex'] == '5700'
        else:
            assert first['configuration_queued_latch'] == 1
            if fail_kind == 'completion':
                m.run(0x0801649c, 0)
            elif fail_kind == 'start_queue':
                m.queue_ok = False
                m.run(0x0801649c, 1)
                assert m.queued[-1]['payload_hex'] == '5700'
                m.queue_ok = True
            # No completion in the lost case. Timer/feedback still retries.
            for point in range(1000, 11001, 1000):
                m.tick_task(point)
            assert m.snapshot()['configuration_queued_latch'] == 0
            m.mppt()
            assert len([q for q in m.queued if q['callback'] == '0801649d']) == 2
        results['queue_and_completion'].append({'failure': fail_kind,
            'first': first, 'after_recovery_path': m.snapshot(), 'freed_allocations': m.freed})

    # Bulk status completion success resets then increments the counter to1.
    # Six later failures reach7 and clear all146 cached bytes, without assuming
    # when transport reports completion or how many attempts it represents.
    for successful_initial_completion in (False, True):
        for failures in (0, 1, 5, 6, 7, 12):
            m = RestartMachine(running=True)
            initial = bytes([0xa5])*146
            m.uc.mem_write(MODULE, initial)
            if successful_initial_completion:
                m.run(0x0801b2d4, 1)
            for _ in range(failures):
                m.run(0x0801b2d4, 0)
            status = bytes(m.uc.mem_read(MODULE, 146))
            cleared = successful_initial_completion and failures >= 6
            expected = bytearray(initial)
            if not successful_initial_completion and failures:
                expected[0x76] &= ~0x80
            assert status == (bytes(146) if cleared else bytes(expected))
            assert m.refresh_requests == ([10] if cleared else [])
            comm = bytes(m.uc.mem_read(COMM+1, 3))
            assert comm[0] == int(cleared)
            results['status_failure_callback'].append({
                'successful_initial_completion': successful_initial_completion,
                'subsequent_failed_completions': failures,
                'entire_cache_cleared': cleared, 'comm_failure_flag': comm[0],
                'completion_counter': comm[1], 'has_received_status': comm[2],
                'error_codes_requested': m.errors, 'refresh_requests': m.refresh_requests,
                'saved_settings_and_output_flags_preserved': m.snapshot()['saved_settings_and_output_flags_preserved']})

    # A separate parameter-update callback retries via dirty byte+15 when the
    # failure counter increments below10, but resets that counter immediately.
    # Test the actual boundary, rather than claiming a ten-attempt backoff.
    for before in (0, 1, 8, 9, 10, 254, 255):
        for accepted in (0, 1):
            m = RestartMachine()
            m.uc.mem_write(TASK, bytes((before,)))
            m.uc.mem_write(0x200018d0+6, b'\x01')
            m.run(0x08016570, accepted)
            counter = m.uc.mem_read(TASK, 1)[0]
            dirty = m.uc.mem_read(0x200018d0+15, 1)[0]
            expected = 0 if accepted else ((before+1) & 255)
            retry = not accepted and expected < 10
            assert dirty == int(retry)
            assert counter == (0 if retry or accepted else expected)
            results['parameter_callback'].append({'initial_counter': before,
                'accepted': bool(accepted), 'counter_after': counter,
                'update_dirty_after': dirty,
                'saved_settings_and_output_flags_preserved': m.snapshot()['saved_settings_and_output_flags_preserved']})

    image = firmware_image()
    calls = direct_thumb_calls(image, 0x08027e40)
    expected_calls = [0x08016630, 0x0801666e, 0x0802838c, 0x080292b4,
        0x08029318, 0x0802943c, 0x0802947c, 0x080294c0, 0x0802952e,
        0x08029588, 0x080295f4, 0x0802a574, 0x0802a5f2, 0x0802a648,
        0x0802a6d6, 0x0802a73a, 0x0802a7e8, 0x0802a840, 0x0802a8d0,
        0x0802a928, 0x0802a994, 0x0802c820, 0x0802c87c]
    assert calls == expected_calls
    assert image.find(struct.pack('<I', 0x08027e41)) == -1
    # Manually reviewed descriptor-register assignments at all direct sites;
    # this records a bounded negative result, not an indirect-call proof.
    registers = ['0100', '0126', '0102', '0004', '0016', 'f000',
        '(argument << 12) & ffff', 'f00d', 'f012', 'f015', 'f000',
        '0000', '0000', '0015', '0004', '0016', '0000', '0022',
        '0001', '0023', '000d', 'f006', 'f007']
    words = dsp_words()
    writer_literals = [address for address, value in words.items() if value == 0x98dc]
    assert len(writer_literals) == 62
    assert all(words.get(address-1) == 0x7648 for address in writer_literals)
    audit = {'direct_queue_calls': [{'address': f'{call:08x}',
              'reviewed_descriptor_register': reg} for call, reg in zip(calls, registers)],
        'literal_thumb_pointer_occurrences': 0,
        'only_direct_0015_builder': '0802a590',
        'dsp_event_writer_low_word_occurrences': len(writer_literals),
        'all_low_word_occurrences_are_direct_long_call_operands': True,
        'dsp_indirect_limit': 'No other literal98dc materialization found. Computed '
            'addresses, nonliteral dispatch, aliases and direct event-bank stores '
            'are not excluded; bank0bit6 origin remains unproven.',
        'scope': 'Aligned direct BL and exact pointer-literal search; not a complete '
                 'call graph, alias analysis, diagnostic-table or radio forwarding proof.'}
    return results, audit


def main():
    results, audit = run_suite()
    counts = {key: len(value) for key, value in results.items()}
    assert counts == {'timer_and_feedback': 16, 'queue_and_completion': 5,
                      'status_failure_callback': 12, 'parameter_callback': 14}
    out = ROOT/'pv-bridge-restart-results.json'
    out.write_text(json.dumps({'cases': results, 'static_queue_audit': audit}, indent=2)+'\n')
    out.chmod(0o600)
    names = (Path(__file__).name, 'emulate_pv_retry_actuation.py',
             'emulate_pv_retry_origins.py', 'emulate_general_settings.py',
             'emulate_additional_features.py', 'emulate_clock_semantics.py',
             'extract_dsp.py', 'replay_io.py')
    manifest = {'hardware_access': False, 'case_counts': counts,
        'firmware': [{'filename': FIRMWARE_NAME, 'sha256': FIRMWARE_SHA256},
                     {'filename': DSP_NAME, 'sha256': DSP_HASH}],
        'runtime': {'python': platform.python_version(), 'unicorn': unicorn.__version__},
        'source_sha256': {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                          for name in names},
        'result_sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
        'substitutions': ['allocation/free', 'internal queue acceptance and delivery',
            'logging', 'MPPT diagnostic collection', 'error/display notification',
            'XT60i auxiliary configuration', 'memcpy/memset',
            'inherited unused LCD/persistence/backup-register boundaries'],
        'executed': ['MPPT task08025140', 'configuration/start builders',
            'software timer service0801089c', 'timer query/start/restart',
            'tick RAM getter08010eb0', 'status callback0801b2d4',
            'parameter completion08016570'],
        'not_executed': ['UART queue worker and receive parser', 'public diagnostic tunnel',
            'physical DSP/PV/BMS', 'hardware timer interrupt', 'wall-clock time',
            'main event-policy reaction to cleared cache']}
    path = ROOT/'pv-bridge-restart-manifest.json'
    path.write_text(json.dumps(manifest, indent=2)+'\n'); path.chmod(0o600)
    print(json.dumps({'cases': sum(counts.values()), 'groups': counts, 'hardware_access': False}))


if __name__ == '__main__':
    main()
