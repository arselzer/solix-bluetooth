"""Extract the sparse DSP container without changing either device.

Container metadata uses little-endian addresses/lengths; payload words are
big-endian. TI program addresses identify 16-bit words. All checks precede export.
"""
from pathlib import Path
import argparse
from replay_io import REPOSITORY_FIRMWARE
import hashlib
import json
import re
import struct

ROOT=REPOSITORY_FIRMWARE
OUTPUT=Path("firmware-analysis-results").resolve()
PAYLOAD=0x3000


def crc16(data):
    value=0xffff
    for byte in data:
        value^=byte
        for _ in range(8):
            value=(value>>1)^0xa001 if value&1 else value>>1
    return value


def parse(data):
    if len(data)<PAYLOAD or data[48:52]!=b'HEX\x00':
        raise ValueError('Invalid DSP container header')
    records=[]
    previous_end=0
    previous_address_end=0
    for position in range(64,PAYLOAD-11,12):
        offset,address,size,check=struct.unpack_from('<IIHH',data,position)
        if (offset,address,size,check)==(0xffffffff,0xffffffff,0xffff,0xffff):
            break
        if offset!=previous_end or size==0 or size%2 or not 0x80000<=address<0xa0000:
            raise ValueError('Invalid DSP record extent')
        if address<previous_address_end or address+size//2>0xa0000:
            raise ValueError('Overlapping or out-of-range DSP word addresses')
        block=data[PAYLOAD+offset:PAYLOAD+offset+size]
        if len(block)!=size or crc16(block)!=check:
            raise ValueError('DSP block CRC/length mismatch')
        records.append({'table_offset':position,'offset':offset,'address':address,
                        'size_octets':size,'crc16_modbus':f'{check:04x}'})
        previous_end=offset+size
        previous_address_end=address+size//2
    else:
        raise ValueError('Missing DSP record terminator')
    if not records:
        raise ValueError('No DSP records')
    return records


def export(name):
    source=ROOT/(name+'-decoded.bin')
    data=source.read_bytes()
    records=parse(data)
    directory=OUTPUT/(name+'-sparse');directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    low=records[0]['address'];high=records[-1]['address']+records[-1]['size_octets']//2
    flat=bytearray(b'\xff'*(2*(high-low)))
    for record in records:
        offset,size,address=record['offset'],record['size_octets'],record['address']
        block=data[PAYLOAD+offset:PAYLOAD+offset+size]
        flat[2*(address-low):2*(address-low)+size]=block
        (directory/f'{address:06x}-be.bin').write_bytes(block)
    (directory/'flash-be.bin').write_bytes(flat)
    swapped=bytearray(len(flat));swapped[0::2]=flat[1::2];swapped[1::2]=flat[0::2]
    (directory/'flash-le.bin').write_bytes(swapped)
    strings=[]
    for record in records:
        offset,size,address=record['offset'],record['size_octets'],record['address']
        block=data[PAYLOAD+offset:PAYLOAD+offset+size]
        for found in re.finditer(rb'(?:\x00[\x20-\x7e]){5,}',block):
            strings.append({'word_address':address+found.start()//2,
                            'value':found.group().decode('utf-16-be')})
    summary={'component':name,'source_sha256':hashlib.sha256(data).hexdigest(),
             'payload_offset_octets':PAYLOAD,'metadata_record_offset_octets':64,
             'metadata_format':'<IIHH: payload octet offset, TI word address, octet length, CRC16/MODBUS',
             'record_count':len(records),'all_block_crc16_verified':True,
             'payload_octets':sum(record['size_octets'] for record in records),
             'flat_word_base':low,'flat_word_end_exclusive':high,
             'flat_be_sha256':hashlib.sha256(flat).hexdigest(),
             'flat_le_sha256':hashlib.sha256(swapped).hexdigest(),
             'header_check':f'{struct.unpack_from("<H",data,52)[0]:04x}',
             'whole_component_check_verified':False,'records':records,'strings':strings}
    (directory/'manifest.json').write_text(json.dumps(summary,indent=2)+'\n')
    for path in directory.iterdir():path.chmod(0o600)
    return summary


def main():
    global ROOT, OUTPUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-directory',type=Path,default=REPOSITORY_FIRMWARE)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args()
    ROOT=args.firmware_directory; OUTPUT=args.output
    if not __debug__: raise RuntimeError('Assertions are required; do not use Python -O')
    for name,expected in (('dspACDC','900627643390602121a67a5bfdedb30051e76f7e0738bed6a205cbdc0fce44e3'),('dspDCDC','7ae2bb915812441b8316d1c3f3efc2379fcb488d4d03e950901cf507c224ceb8')):
        if hashlib.sha256((ROOT/(name+'-decoded.bin')).read_bytes()).hexdigest()!=expected:
            raise ValueError('DSP input hash mismatch')
    summaries=[]
    checks=[]
    for name in ('dspACDC','dspDCDC'):
        summary=export(name);summaries.append(summary)
        original=(ROOT/(name+'-decoded.bin')).read_bytes()
        for case in ('payload_corruption','word_overlap','truncated_payload'):
            data=bytearray(original)
            if case=='payload_corruption':data[PAYLOAD]^=1
            elif case=='word_overlap':struct.pack_into('<I',data,80,summary['records'][0]['address'])
            else:data=data[:PAYLOAD+summary['payload_octets']-1]
            try:parse(data)
            except ValueError:checks.append({'component':name,'case':case,'rejected':True})
            else:raise AssertionError('Corrupt fixture was accepted')
    result={'images':[{k:v for k,v in s.items() if k not in ('records','strings')} for s in summaries],
            'negative_cases':checks}
    path=OUTPUT/'dsp-extraction-results.json';path.write_text(json.dumps(result,indent=2)+'\n');path.chmod(0o600)
    print(json.dumps(result))


if __name__=='__main__':main()
