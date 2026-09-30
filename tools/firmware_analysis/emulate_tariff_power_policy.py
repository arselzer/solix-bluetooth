"""Offline C1000 tariff policy comparison; execute original charging policy.

Real instructions run for policy08014d58, tariff selection, settings getters,
fast-charge setter and mode-bit assignments. Substitutes: battery SOC/charge
voltage/current-limit sensor getter; backup status; charge-plan destination;
load-history helper; persistence, logging/LCD events and refresh inherited from
base harness. No DSP/I/O or real charging/discharge operation executes.
"""
import json
import struct
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_C1_C0_2,UC_ARM_REG_FPEXC
from emulate_tou_d9 import D9Machine
from emulate_clock_semantics import ROOT


class PolicyMachine(D9Machine):
    def __init__(self,tariff,soc,reserve,fast=False,charging=True,mode_bits=1):
        super().__init__()
        self.uc.reg_write(UC_ARM_REG_C1_C0_2,0xf<<20)
        self.uc.reg_write(UC_ARM_REG_FPEXC,0x40000000)
        self.soc=soc;self.charge_plans=[];self.history_actions=[]
        self.uc.mem_write(0x20000164,struct.pack('<I',(0x40 if charging else 0)|(mode_bits<<4)))
        self.uc.mem_write(0x20001d76,bytes([int(bool(tariff)),int(bool(tariff)),tariff,0,24])+b'\0'*15)
        self.uc.mem_write(0x20001d61,bytes([reserve]))
        self.uc.mem_write(0x20002188,bytes([4 if fast else 0]))
        self.uc.mem_write(0x20001d57,struct.pack('<H',300))

    def step(self,uc,address,size,data):
        if address==0x08018384:
            kind=uc.reg_read(UC_ARM_REG_R0)
            assert kind in (2,10,11),kind
            self.back({2:self.soc,10:600,11:10}[kind])
        elif address==0x0802a15c:
            pointer=uc.reg_read(UC_ARM_REG_R0);channel=uc.reg_read(UC_ARM_REG_R1)
            self.charge_plans.append({'channel':channel,'power_voltage_current':list(struct.unpack('<HHH',uc.mem_read(pointer+8*channel,6)))})
            self.back()
        elif address==0x080147e0:
            self.history_actions.append(uc.reg_read(UC_ARM_REG_R0));self.back()
        elif address in (0x08026e30,0x0800d0c8):
            self.back()
        elif (0x08014d58<=address<0x0801542a
              or 0x08018560<=address<0x080185ae
              or 0x08019488<=address<0x080194b2
              or 0x0801a444<=address<0x0801a44e
              or 0x0801a574<=address<0x0801a57c
              or 0x0802b20c<=address<0x0802b246
              or 0x0801473c<=address<0x080147dc):
            return
        else:
            super().step(uc,address,size,data)

    def policy(self):
        self.run(0x08014d58,0)
        return {'mode_bits':(int.from_bytes(self.uc.mem_read(0x20000164,4),'little')>>4)&3,
                'fast_charge':bool(self.uc.mem_read(0x20002188,1)[0]&4),
                'reserve':self.uc.mem_read(0x20001d61,1)[0],
                'charge_plans':self.charge_plans,'history_actions':self.history_actions}
