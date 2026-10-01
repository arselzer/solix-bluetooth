#!/usr/bin/env python3
"""Replay C1000 Gen 2 full-status reads without changing saved backup records.

Actual 0100/parser/serializer traversal and D9/DA/FE callbacks execute. Other
measurement callbacks emit empty typed values. Allocation, transport, memory
helpers, persistence, logging and refresh timers are substituted. Synthetic
RTC, calendar year and RAM only; no network, device or firmware writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct


def run_suite():
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
    from emulate_disaster_plan import DisasterMachine, AUTOMATIC, NOW, record
    from emulate_clock_semantics import PAYLOAD
    from emulate_general_settings import REQUEST, SETTINGS, tlv
    from replay_io import FIRMWARE_SHA256, firmware_image

    image = firmware_image()
    base = 0x08005000
    table = struct.unpack_from('<I', image, 0x08022534-base)[0]
    descriptors = [struct.unpack_from('<BB2xII', image, table-base+12*i) for i in range(19)]
    # Scan Thumb BL encodings, then limit the interpretation to the reviewed
    # callers below. This is not a complete indirect call-graph analysis.
    record_getter_calls = []
    for position in range(0, len(image)-3, 2):
        first, second = struct.unpack_from('<HH', image, position)
        if first & 0xf800 != 0xf000 or second & 0xd000 != 0xd000:
            continue
        sign = (first >> 10) & 1
        i1 = 1 ^ (((second >> 13) & 1) ^ sign)
        i2 = 1 ^ (((second >> 11) & 1) ^ sign)
        immediate = ((sign << 24) | (i1 << 23) | (i2 << 22)
                     | ((first & 0x3ff) << 12) | ((second & 0x7ff) << 1))
        if sign:
            immediate -= 1 << 25
        if base + position + 4 + immediate == 0x0801a608:
            record_getter_calls.append(base + position)
    assert record_getter_calls == [
        0x08009420, 0x08009440, 0x08009480, 0x08018afc, 0x08018b0c,
        0x08018b64, 0x08018b76, 0x08018b82, 0x08018b92, 0x08018ba2,
        0x08018bb2, 0x08018bf6, 0x08018c02, 0x08018c0c, 0x08018c26,
        0x08018c3a, 0x08018c4e, 0x08019184, 0x08019192,
    ]
    callbacks = {address & ~1 for _tag, _kind, address, _extra in descriptors}
    real_callbacks = {0x080190d4, 0x08018730, 0x08018e4c}
    assert real_callbacks <= callbacks
    output, failure_flag = 0x21002000, 0x200028fa

    class QueryMachine(DisasterMachine):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.uc.mem_map(output, 0x1000)
            self.reported_tags = []

        def step(self, uc, address, size, data):
            r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            if address in callbacks:
                destination, length = struct.unpack('<II', uc.mem_read(r0+4, 8))
                position = struct.unpack('<H', uc.mem_read(length, 2))[0]
                tag = uc.mem_read(destination+position-1, 1)[0]
                self.reported_tags.append(tag)
                if address not in real_callbacks:
                    kind = uc.mem_read(r0, 1)[0]
                    uc.mem_write(destination+position, bytes((1, kind)))
                    uc.mem_write(length, struct.pack('<H', position+2))
                    self.back(); return
            if address == 0x08020a4c:
                assert r0 == 1024
                self.back(output)
            elif address == 0x08017a8c:
                assert r0 == output
                self.back()
            elif address == 0x0801095c:
                self.back(1)  # Status refresh timer, not a saved setting.
            elif any(lo <= address < hi for lo, hi in (
                    (0x0800b57c, 0x0800b62c), (0x080224a0, 0x08022538),
                    (0x08018730, 0x08018818), (0x0800cfd4, 0x0800cfe0))):
                return
            else:
                super().step(uc, address, size, data)

        def query(self):
            self.responses.clear()
            body = tlv(0xa1, b'\x22') + tlv(0xfe, b'\x03' + struct.pack('<I', NOW+86400))
            self.uc.mem_write(PAYLOAD, body)
            self.uc.reg_write(UC_ARM_REG_R1, len(body))
            self.uc.reg_write(UC_ARM_REG_R2, REQUEST+0x18)
            self.run(0x080225a6, PAYLOAD)
            self.run(0x0800b57c, REQUEST)
            assert len(self.responses) == 1 and self.responses[0][0] == 0
            packet, fields, position = self.responses[0], {}, 1
            while position < len(packet):
                tag, size = packet[position:position+2]
                position += 2
                fields[tag] = packet[position:position+size]
                position += size
            assert position == len(packet)
            return fields

    rows = []
    for enabled in (0, 1):
        for offset in (-7200, 0, 19800):
            for failure in (0, 1, 2, 3):
                m = QueryMachine(now=NOW, offset=offset)
                # Deliberately distinct dormant records and maxima; not a D9
                # counterexample test, but a whole-query preservation guard.
                records = b''.join(record(NOW+3600+i*7200, NOW+5400+i*7200, 71+i)
                                   for i in range(4)) + b'\x01\x01'
                assert len(records) == 38
                m.uc.mem_write(AUTOMATIC, records)
                m.uc.mem_write(SETTINGS+0x19, bytes((85 if enabled else 10,)))
                m.uc.mem_write(SETTINGS+0x2e, bytes((enabled, 2, 1, 0, 12, 3, 12, 24)) + bytes(12))
                m.uc.mem_write(failure_flag, bytes((failure,)))
                before = bytes(m.uc.mem_read(SETTINGS, 0x190))
                outputs = bytes(m.uc.mem_read(0x20000164, 4))
                rtc = bytes(m.uc.mem_read(0x40002818, 8))
                fields = m.query()
                assert m.reported_tags == [item[0] for item in descriptors]
                assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == before
                assert bytes(m.uc.mem_read(AUTOMATIC, 38)) == records
                assert bytes(m.uc.mem_read(0x20000164, 4)) == outputs
                assert bytes(m.uc.mem_read(0x40002818, 8)) == rtc
                assert m.uc.mem_read(failure_flag, 1)[0] == failure
                assert fields[0xda][2] == failure
                assert fields[0xfe] == b'\x03' + struct.pack('<I', NOW)
                d9 = fields[0xd9]
                assert d9[2] == enabled and d9[3] == (85 if enabled else 10)
                assert d9[6] == 2 and d9[7:13] == bytes((1, 0, 12, 3, 12, 24))
                assert m.persistence_calls == 0
                rows.append({'tou_enabled': bool(enabled), 'offset_seconds_west': offset,
                             'clock_screen_failure_flag': failure,
                             'd9_prefix': d9[:13].hex(), 'fe_utc': NOW, 'request_fe_utc': NOW+86400,
                             'all_saved_settings_preserved': True,
                             'all_38_backup_bytes_preserved': True,
                             'failure_flag_preserved': True,
                             'rtc_and_output_flags_preserved': True})
    return {'model': 'C1000 Gen 2 main 1.1.4.9', 'firmware_sha256': FIRMWARE_SHA256,
            'cases': len(rows), 'results': rows,
            'direct_record_getter_calls': [f'{address:08x}' for address in record_getter_calls],
            'telemetry_descriptors': [{'tag': f'{tag:02x}', 'type': kind,
                'handler': f'{address & ~1:08x}', 'executed': (address & ~1) in real_callbacks}
                for tag, kind, address, _extra in descriptors],
            'limits': __doc__.strip(),
            'claim_scope': 'Preservation proved for these synthetic cases with the declared substitutes. '
                           'Unrelated measurement callbacks and hardware effects are not verified. '
                           'No complete backup-record export was added or proved.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.firmware_dir:
        os.environ['SOLIX_FIRMWARE_DIR'] = str(args.firmware_dir)
    os.environ['SOLIX_ANALYSIS_OUTPUT'] = str(args.output.resolve().parent)
    result = run_suite()
    result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(f"Passed {result['cases']} synthetic full-status query cases")


if __name__ == '__main__':
    main()
