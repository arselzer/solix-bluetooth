"""Offline C1000 1.1.4.9 setting handler and A4 telemetry replay.

Actual parser, 0103 handler, setters/getters, software timer query/event marking,
A4 builder and refresh descriptor builder execute. Persistence, response send,
LCD event delivery, memcpy/memset and final refresh queue transport are stubs.
Display-off callback is a recorded boundary, not simulated hardware.
"""
import json
import os
import struct
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, UC_ARM_REG_LR
from emulate_clock_semantics import ClockMachine, ROOT, BUFFER, LENGTH, DESC, PAYLOAD, STACK, STOP

os.umask(0o077)
REQUEST=0x21001000
SETTINGS=0x20001d48
EVENT=0x20007218+3*12
TIMERS=0x20007344


def tlv(tag,value):
    return bytes((tag,len(value)))+value


class SettingsMachine(ClockMachine):
    def __init__(self, *, display_on=1, common_timer=0, special_mode=0):
        super().__init__()
        self.uc.mem_map(REQUEST,0x1000)
        self.uc.mem_write(SETTINGS,bytes(0x190))
        self.uc.mem_write(SETTINGS+0xf,struct.pack('<HHH',1200,0,30))
        self.uc.mem_write(SETTINGS+0x17,bytes((100,1,10)))
        self.uc.mem_write(SETTINGS+0x21,b'\x01')
        self.uc.mem_write(SETTINGS+0x24,b'\x03\x32')
        self.uc.mem_write(SETTINGS+0x188,b'\x01')
        self.uc.mem_write(0x20000462,bytes((3,0,0,4,5)))
        self.uc.mem_write(EVENT,b'\x01'+bytes(11))
        self.uc.mem_write(TIMERS+4*20,bytes((2 if common_timer else 3,)))
        self.uc.mem_write(TIMERS+5*20,bytes((2 if display_on else 3,)))
        self.uc.mem_write(0x2000039a,bytes((special_mode<<1,)))
        self.uc.mem_write(0x20000164,struct.pack('<I',0x30))
        self.uc.mem_write(0x20000144,bytes((6,7)))
        self.uc.mem_write(TIMERS+6*20,b'\x02')
        self.uc.mem_write(TIMERS+7*20,b'\x02')
        self.uc.mem_write(0x20000141,b'\x05')
        self.uc.mem_write(0x2000218c,b'\x55'*12)
        self.calls=[]
        self.acked=0
        self.scheduled=0
        self.refresh=[]

    def step(self,uc,address,size,data):
        r0,r1,r2=(uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2))
        if address==0x080238f4:
            self.scheduled+=1;self.back(1)
        elif address==0x08008060:
            self.acked+=1;self.back()
        elif address==0x08016a70:
            body=bytes(uc.mem_read(r0,20))
            self.refresh.append({'delay':int.from_bytes(body[12:16],'little'),'command':body[16:18].hex(),'function':body[18]})
            self.back(1)
        elif address==0x08026e30:
            self.calls.append('LCD_DATA_UPDATE_EVENT');self.back()
        elif address==0x0801be60:
            self.calls.append('display_off_callback_boundary');self.back()
        elif address==0x08011730:
            self.calls.append('special_mode_refresh_boundary');self.back()
        elif address==0x080052a8:
            uc.mem_write(r0,bytes(uc.mem_read(r1,r2)));self.back(r0)
        elif address==0x080052da:
            uc.mem_write(r0,bytes(r1));self.back(r0)
        elif any(lo<=address<hi for lo,hi in (
            (0x0800c530,0x0800c6f2),(0x08022576,0x08022612),
            (0x0802b2fc,0x0802b306),(0x0802b420,0x0802b42a),
            (0x0802b490,0x0802b4d2),(0x0802b55c,0x0802b568),
            (0x0802a294,0x0802a2cc),(0x0800fbe4,0x0800fc18),
            (0x08013d14,0x08013d4a),(0x0801c2bc,0x0801c2d2),
            (0x0801bf3c,0x0801bf52),(0x08010840,0x08010864),
            (0x080106b4,0x080106e4),(0x08010998,0x080109c0),
            (0x0801a018,0x0801a1f8),(0x0801a574,0x0801a608),
            (0x0801a648,0x0801a6b0),(0x08018d9c,0x08018da4),
            (0x0801a444,0x0801a450),(0x08015494,0x0801549e),
            (0x0802af48,0x0802af56),(0x08010580,0x0801059a),
            (0x0802a1ec,0x0802a20a))):
            return
        else:
            super().step(uc,address,size,data)

    def run(self,address,argument):
        self.stopped=False
        self.uc.reg_write(UC_ARM_REG_R0,argument)
        self.uc.reg_write(UC_ARM_REG_SP,STACK)
        self.uc.reg_write(UC_ARM_REG_LR,STOP|1)
        self.uc.emu_start(address|1,0,count=30000)
        assert self.stopped
        return self.uc.reg_read(UC_ARM_REG_R0)

    def command(self,tag=None,value=0):
        payload=b'\x01'+bytes((value,)) if tag!=0xa6 else b'\x02'+struct.pack('<H',value)
        return self.body(tlv(0xa1,b'\x22')+(tlv(tag,payload) if tag else b'')+tlv(0xfe,b'\x03\x00\xf1\x53\x65'))

    def body(self,body):
        self.acked=0
        self.uc.mem_write(PAYLOAD,body)
        self.uc.reg_write(UC_ARM_REG_R1,len(body))
        self.uc.reg_write(UC_ARM_REG_R2,REQUEST+0x18)
        self.run(0x080225a6,PAYLOAD)
        self.run(0x0800c530,REQUEST)
        assert self.acked==1
        assert int.from_bytes(self.uc.mem_read(0x20000164,4),'little')==0x30
        assert self.refresh[-1]=={'delay':50,'command':'2104','function':15}
        return body.hex()

    def a4(self):
        self.uc.mem_write(BUFFER,bytes(128));self.uc.mem_write(LENGTH,b'\0\0')
        self.uc.mem_write(DESC,struct.pack('<B3xIIHBx',4,BUFFER,LENGTH,128,3))
        self.run(0x0801a018,DESC)
        size=int.from_bytes(self.uc.mem_read(LENGTH,2),'little')
        raw=bytes(self.uc.mem_read(BUFFER,size))
        assert size==35 and raw[0]==34 and raw[1]==4
        return raw[1:]


def main():
    results={'byte_fields':[],'restoration':[],'brightness':[],'display':[],'memory':[],'device_timeout':[],'malformed_alert':[]}
    for tag,offset,readback in ((0xa5,0x29,20),(0xb0,0x188,32)):
        for active in (0,1):
            for value in range(256):
                m=SettingsMachine(common_timer=active)
                baseline=bytes(m.uc.mem_read(SETTINGS,0x190))
                m.command(tag,value)
                actual=bytes(m.uc.mem_read(SETTINGS,0x190))
                expected=bytearray(baseline);expected[offset]=value
                assert actual==bytes(expected)
                a4=m.a4();observed=a4[readback] if tag==0xa5 else (a4[readback]>>1)&1
                assert observed==(value if tag==0xa5 else value&1)
                assert bool(m.uc.mem_read(EVENT+1,1)[0])==bool(active)
                assert m.scheduled==1 and not m.calls
                results['byte_fields'].append({'tag':f'{tag:02x}','value':value,'common_timer':active,'stored':actual[offset],'telemetry':observed,'settings_only_target_changed':True,'display_event_pending':active,'persistence_requests':1})
    for tag,offset in ((0xa5,0x29),(0xb0,0x188)):
        for initial in (0,1):
            m=SettingsMachine();m.uc.mem_write(SETTINGS+offset,bytes((initial,)))
            before=m.a4();settings=bytes(m.uc.mem_read(SETTINGS,0x190))
            outbound=m.command(tag,1-initial);m.command(tag,initial)
            assert m.a4()==before and bytes(m.uc.mem_read(SETTINGS,0x190))==settings
            results['restoration'].append({'tag':f'{tag:02x}','initial':initial,'outbound_body':outbound,'a4_restored_exactly':True,'persistent_settings_restored_exactly':True})
    for value in (0,1,2,3,4,255):
        for special in (0,1):
            m=SettingsMachine(special_mode=special);m.command(0xa3,value);a4=m.a4()
            assert a4[18]==(value or 3)
            assert bool(m.uc.mem_read(EVENT+1,1)[0])==bool(value)
            assert ('display_off_callback_boundary' in m.calls)==(value==0)
            assert bool(m.uc.mem_read(0x2000039a,1)[0]&8)==bool(special and value)
            results['brightness'].append({'value':value,'special_mode':special,'stored_and_telemetry':a4[18],'calls':m.calls,'persistence_requests':m.scheduled,'display_event_pending':bool(value)})
    for display_on in (0,1):
        for value in (0,1,2,255):
            m=SettingsMachine(display_on=display_on);m.command(0xa2,value)
            should_toggle=(value==0 and display_on==1) or (value==1 and display_on==0)
            assert ('display_off_callback_boundary' in m.calls)==bool(should_toggle and display_on)
            assert bool(m.uc.mem_read(EVENT+1,1)[0])==bool(should_toggle and not display_on)
            results['display'].append({'before':display_on,'value':value,'calls':m.calls,'display_wake_event_pending':bool(m.uc.mem_read(EVENT+1,1)[0]),'persistence_requests':m.scheduled,'exact_0_or_1_needed':True})
    for value in (0,1,2,255):
        m=SettingsMachine();m.command(0xa8,value);a4=m.a4()
        assert a4[23]==bool(value)
        cleared=bytes(m.uc.mem_read(0x2000218c,12))==bytes(12)
        assert cleared==(value==0)
        results['memory'].append({'value':value,'readback':a4[23],'recovery_state_cleared':cleared,'recovery_timer_states':[m.uc.mem_read(TIMERS+i*20,1)[0] for i in (6,7)]})
    for value in (0,30,60,120,240,360,720,1440,65535):
        m=SettingsMachine();m.command(0xa6,value);a4=m.a4()
        assert int.from_bytes(a4[14:16],'little')==value
        results['device_timeout'].append({'value':value,'readback':value,'unit':'minutes','handler_range_checks':False})
    for payload in (b'',b'\x01'):
        m=SettingsMachine();m.body(tlv(0xa1,b'\x22')+tlv(0xb0,payload))
        assert m.uc.mem_read(SETTINGS+0x188,1)==b'\x01' and m.scheduled==0
        results['malformed_alert'].append({'field_length':len(payload),'ignored':True})
    output=ROOT/'general-settings-emulation-results.json'
    output.write_text(json.dumps(results,indent=2)+'\n');output.chmod(0o600)
    print(json.dumps({'counts':{k:len(v) for k,v in results.items()},'total':sum(map(len,results.values())),'hardware':False}))


if __name__=='__main__':main()
