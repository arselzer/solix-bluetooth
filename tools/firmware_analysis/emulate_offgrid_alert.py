"""Offline C1000 off-grid alert transition predicate; no hardware/network I/O.

Actual debounce/setting/disable-condition branches execute. SOC getter returns
synthetic73; notification queue and logger are substituted. No timer scheduler
or downstream app/cloud notification delivery is modeled.
"""
import json
import os
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1
from emulate_general_settings import SettingsMachine, ROOT, SETTINGS, STACK

os.umask(0o077)


class AlertMachine(SettingsMachine):
    def __init__(self, setting, inhibited, previous):
        super().__init__()
        self.events=[]
        self.writes=[]
        self.uc.mem_write(SETTINGS+0x188,bytes((setting,)))
        self.uc.mem_write(0x200003e9,bytes((inhibited,)))
        self.uc.mem_write(0x200001f4,bytes((previous,0,0)))
        self.uc.hook_add(UC_HOOK_MEM_WRITE,lambda uc,access,address,size,value,data:self.writes.append((address,size)))

    def step(self,uc,address,size,data):
        if address==0x08018384:
            assert uc.reg_read(UC_ARM_REG_R0)==2
            self.back(73)
        elif address==0x0800f76c:
            p=uc.reg_read(UC_ARM_REG_R0)
            raw=bytes(uc.mem_read(p,24))
            payload=int.from_bytes(raw[4:8],'little')
            self.events.append({'event':raw[0],'flag':raw[1],'payload':bytes(uc.mem_read(payload,1)).hex(),'size':int.from_bytes(raw[16:18],'little')})
            self.back(1)
        elif 0x0800d02c<=address<0x0800d0a4 or 0x0801bc7c<=address<0x0801bc84:
            return
        else:
            super().step(uc,address,size,data)


def main():
    results=[]
    for setting in (0,1,2,3,255):
        for inhibited in (0,1):
            for previous,current in ((1,0),(0,1)):
                m=AlertMachine(setting,inhibited,previous)
                m.uc.mem_write(0x200004be,bytes((current,)))
                for _ in range(4):m.run(0x0800d02c,0)
                assert not m.events
                m.run(0x0800d02c,0)
                expected=int(setting==1 and not inhibited and current==0)
                assert len(m.events)==expected
                if expected:assert m.events==[{'event':89,'flag':1,'payload':'49','size':1}]
                m.run(0x0800d02c,0)
                assert len(m.events)==expected
                assert all(STACK-64<=a<STACK or 0x200001f4<=a<0x200001f7 for a,n in m.writes)
                results.append({'setting':setting,'inhibit_flag':inhibited,'previous_power_gate':previous,'new_power_gate':current,'notification_on_fifth_sample':bool(expected),'events':m.events,'writes_only_stack_and_alert_debounce_state':True})
    output=ROOT/'offgrid-alert-emulation-results.json'
    output.write_text(json.dumps(results,indent=2)+'\n');output.chmod(0o600)
    print(json.dumps({'offgrid_alert_cases':len(results),'hardware':False}))


if __name__=='__main__':main()
