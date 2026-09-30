"""Offline replay of C1000 internal clock sync and FE serialization.

Actual ARM handlers, offset setter, RTC polling, register read/write and FE
builder execute. Logger, system tick, persistence backend, response transport,
and memcpy are substitutes. MMIO values are synthetic, not real hardware.
"""
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC

from replay_io import ROOT, firmware_image
STACK, STOP = 0x2001f000, 0x08005000
BUFFER, LENGTH, DESC, REQUEST, PAYLOAD = 0x21000000, 0x21000400, 0x21000500, 0x21000600, 0x21000700


class ClockMachine:
    def __init__(self, *, rtc=1600000000, old_utc=1699999000, old_offset=-3600, ready=True):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000, 0x40000)
        self.uc.mem_write(0x08005000, firmware_image())
        self.uc.mem_map(0x20000000, 0x20000)
        self.uc.mem_map(BUFFER, 0x1000)
        self.uc.mem_map(0x40002000, 0x1000)
        self.uc.mem_write(0x40002804, struct.pack('<I', 0x20 if ready else 0))
        self.uc.mem_write(0x40002818, struct.pack('<II', rtc >> 16, rtc & 0xffff))
        self.uc.mem_write(0x20000768, struct.pack('<Ii', old_utc, old_offset))
        self.uc.mem_write(0x20001d4d, struct.pack('<i', old_offset))
        self.ticks = 0
        self.persistence_calls = 0
        self.responses = []
        self.stopped = False
        self.uc.hook_add(UC_HOOK_CODE, self.step)

    def back(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def step(self, uc, address, size, data):
        if address == STOP:
            self.stopped = True
            uc.emu_stop()
        elif address == 0x0800ad44:
            self.ticks += 1
            self.back(self.ticks)
        elif address == 0x0800d284:
            self.back()
        elif address == 0x0802c994:
            self.persistence_calls += 1
            self.back(1)
        elif address == 0x080273c8:
            self.responses.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2))))
            self.back()
        elif address == 0x080052a8:
            dest, src, n = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            assert n == 5
            uc.mem_write(dest, bytes(uc.mem_read(src, n)))
            self.back(dest)
        elif not (0x0800cc18 <= address < 0x0800cc7c
                  or 0x08014668 <= address < 0x080146a4
                  or 0x08029c9c <= address < 0x08029cec
                  or 0x08029d24 <= address < 0x08029d4c
                  or 0x0802b56c <= address < 0x0802b580
                  or 0x08018e4c <= address < 0x08018eb6
                  or 0x08019fd0 <= address < 0x08019fd4
                  or 0x0801a6b0 <= address < 0x0801a6b8):
            raise RuntimeError(f'Unexpected instruction {address:08x}')

    def run(self, address, argument):
        self.stopped = False
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=3000)
        assert self.stopped

    def sync(self, utc, offset):
        body = b'\xa1\x04' + struct.pack('<I', utc) + b'\xa2\x04' + struct.pack('<I', offset & 0xffffffff)
        self.uc.mem_write(PAYLOAD, body)
        self.uc.mem_write(REQUEST+0x14, struct.pack('<I', PAYLOAD))
        self.run(0x0800cc18, REQUEST)
        assert self.responses == [b'\x00']

    def rtc(self):
        hi, lo = struct.unpack('<II', self.uc.mem_read(0x40002818, 8))
        return ((hi << 16) | lo) & 0xffffffff

    def fe(self):
        self.uc.mem_write(LENGTH, b'\x00\x00')
        self.uc.mem_write(DESC, struct.pack('<B3xIIHBx', 3, BUFFER, LENGTH, 64, 1))
        self.run(0x08018e4c, DESC)
        body = bytes(self.uc.mem_read(BUFFER, 6))
        assert body[:2] == b'\x05\x03'
        return int.from_bytes(body[2:], 'little')
