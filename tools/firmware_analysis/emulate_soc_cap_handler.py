"""Offline C1000 native 0103 TLV parser, handler and SoC setter replay.

Real ARM TLV parsing, lookup, handler and upper/lower setters execute. Logging,
persistence scheduling, response send, display-timer status, and refresh are
substitutes. No physical device or storage access occurs.
"""
import json
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, UC_ARM_REG_LR
from emulate_clock_semantics import ClockMachine, ROOT, PAYLOAD, STACK, STOP

REQUEST = 0x21001000


class SocMachine(ClockMachine):
    def __init__(self, baseline=(90, 1, 10)):
        super().__init__()
        self.uc.mem_map(REQUEST, 0x1000)
        self.uc.mem_write(0x20001d5f, bytes(baseline))
        self.scheduled = 0
        self.acked = 0

    def step(self, uc, address, size, data):
        if address == 0x080238f4:
            self.scheduled += 1
            self.back(1)
        elif address == 0x08008060:
            self.acked += 1
            self.back()
        elif address in (0x0801c2bc, 0x08013d14):
            self.back()
        elif (0x0800c530 <= address < 0x0800c6f2
              or 0x08022576 <= address < 0x08022612
              or 0x0802b504 <= address < 0x0802b514
              or 0x0802b528 <= address < 0x0802b53a):
            return
        else:
            super().step(uc, address, size, data)

    def run(self, address, argument):
        self.stopped = False
        self.uc.reg_write(UC_ARM_REG_R0, argument)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.uc.emu_start(address | 1, 0, count=25000)
        assert self.stopped

    def command(self, *, upper=None, lower=None):
        body = b'\xa1\x01\x22'
        for tag, value in ((0xaa, upper), (0xab, lower)):
            if value is not None:
                body += bytes([tag, 2, 1, value])
        body += b'\xfe\x05\x03\x00\xf1\x53\x65'
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST+0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800c530, REQUEST)
        assert self.acked == 1
        return tuple(self.uc.mem_read(0x20001d5f, 3)), body.hex()
