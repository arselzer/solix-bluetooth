"""Replay C1000 1.1.4.9 app0090 encoding/storage, entirely offline.

Execute actual ARM TLV parser, lookup, handler, RAM getters/setters and memcpy.
Substitute logger, persistence scheduling, response send, display-timer query
(return 0), and refresh. No controller loop, timer or peripheral executes.
C2000-shaped input is a synthetic cross-model comparison, never a live probe.
"""
import json
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2
from emulate_soc_cap_handler import SocMachine, REQUEST
from emulate_clock_semantics import ROOT, PAYLOAD


def tlv(tag,value):
    return bytes([tag,len(value)])+value


class TouMachine(SocMachine):
    def __init__(self):
        super().__init__()
        self.uc.mem_write(0x20001d76, b'\0\0'+b'\x55'*18)

    def step(self,uc,address,size,data):
        if (0x0800c7c4 <= address < 0x0800c93a
            or 0x0801a698 <= address < 0x0801a6cc
            or 0x0802b550 <= address < 0x0802b558
            or 0x0802b5b4 <= address < 0x0802b5c8
            or 0x080052a8 <= address < 0x080052cc):
            return
        super().step(uc,address,size,data)

    def command(self,body):
        self.uc.mem_write(PAYLOAD,body)
        self.uc.reg_write(UC_ARM_REG_R1,len(body))
        self.uc.reg_write(UC_ARM_REG_R2,REQUEST+0x18)
        self.run(0x080225a6,PAYLOAD)
        self.run(0x0800c7c4,REQUEST)
        assert self.acked == 1
        return bytes(self.uc.mem_read(0x20001d76,20))
