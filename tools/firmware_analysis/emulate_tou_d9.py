"""Synthetic C1000 D9 serialization helper; no capture ingestion.

Actual D9 builder, getters, tariff selector, RTC and hour conversion execute.
Backup-plan state is a zeroed synthetic substitute. Readiness and power gate
are synthetic true values. No device, network, timer or flash operation runs.
"""
import struct
from emulate_tou_controller_encoding import TouMachine
from emulate_clock_semantics import BUFFER, LENGTH, DESC


class D9Machine(TouMachine):
    def __init__(self,rtc=1700006400):
        super().__init__()
        self.uc.mem_write(0x20001d76,b'\0'*20)
        self.uc.mem_write(0x200007af,b'\1')
        self.uc.mem_write(0x200007b6,b'\2')
        self.uc.mem_write(0x200004be,b'\1')
        self.uc.mem_write(0x20001ecd,b'\1')  # synthetic backup setting
        self.uc.mem_write(0x40002818,struct.pack('<II',rtc>>16,rtc&0xffff))

    def step(self,uc,address,size,data):
        if address == 0x08018aac:
            self.back(0x2000f000)
        elif (0x080190d4 <= address < 0x080191fe
              or 0x0801a608 <= address < 0x0801a6cc
              or 0x0801bcc4 <= address < 0x0801bd3a
              or 0x0800d1d8 <= address < 0x0800d1f4
              or 0x08005210 <= address < 0x080052de):
            return
        else:
            super().step(uc,address,size,data)

    def command(self,body):
        self.acked=0
        return super().command(body)

    def d9(self):
        self.uc.mem_write(BUFFER,b'\0'*256)
        self.uc.mem_write(LENGTH,b'\0\0')
        self.uc.mem_write(DESC,struct.pack('<B3xIIHBx',4,BUFFER,LENGTH,255,1))
        self.run(0x080190d4,DESC)
        size=int.from_bytes(self.uc.mem_read(LENGTH,2),'little')
        packet=bytes(self.uc.mem_read(BUFFER,size))
        assert packet[0] == len(packet)-1
        return packet[1:]
