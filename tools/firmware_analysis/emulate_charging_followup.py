"""Offline actual-instruction charging/cap follow-up for C1000 main 1.1.4.9.

Uses inherited synthetic RAM/RTC, sensor, persistence, transport and LCD stubs.
Charge policy captures descriptors before physical execution. Validator executes
in full; the actual settings-load decision starts after synthetic file/CRC success.
Lower-cap handler/parser/D9/BMS bound calculation run original instructions;
timer setup, BMS allocator/queue and backup-state calculator are substituted.
"""
import json
import os
import struct
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_SP
from emulate_clock_semantics import ROOT, PAYLOAD, STACK, STOP, BUFFER, LENGTH, DESC
from emulate_general_settings import SettingsMachine, SETTINGS
from emulate_soc_cap_handler import REQUEST
from emulate_tariff_power_policy import PolicyMachine
from emulate_tou_d9 import D9Machine

os.umask(0o077)


class ValidatorMachine(SettingsMachine):
    def __init__(self):
        super().__init__()
        # Valid independent baseline for all fields checked by 0802c890.
        self.uc.mem_write(SETTINGS+0x15, struct.pack('<H', 50))

    def step(self, uc, address, size, data):
        if 0x0802c890 <= address < 0x0802c98e or 0x0801052e <= address < 0x0801053e:
            return
        super().step(uc, address, size, data)


class NativeAcSettingsMachine(ValidatorMachine):
    def __init__(self, tariff=0, fast=False):
        super().__init__()
        self.uc.mem_write(0x20001d76, bytes((int(bool(tariff)), int(bool(tariff)), tariff, 0, 24))+bytes(15))
        self.uc.mem_write(0x200007af, b'\1')
        self.uc.mem_write(0x200007b6, b'\2')
        self.uc.mem_write(0x200004be, b'\1')
        self.uc.mem_write(0x20002188, bytes((4 if fast else 0,)))
        self.feedback = []

    def step(self, uc, address, size, data):
        if address == 0x0801c30c:
            self.feedback.append(uc.reg_read(UC_ARM_REG_R0)); self.back()
        elif any(lo <= address < hi for lo, hi in (
            (0x0800bd1c, 0x0800be5c), (0x0802b20c, 0x0802b246),
            (0x0802b274, 0x0802b2b8), (0x0801a6c8, 0x0801a6cc),
            (0x0801bcc4, 0x0801bd3a), (0x0800d1d8, 0x0800d1f4),
            (0x08005210, 0x080052a8))):
            return
        else:
            super().step(uc, address, size, data)

    def ac_command(self, tag, value):
        self.acked = 0
        typed = b'\2'+struct.pack('<H', value) if tag == 0xa4 else bytes((1, value))
        body = bytes.fromhex('a10122')+bytes((tag, len(typed)))+typed+bytes.fromhex('fe050300f15365')
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST+0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800bd1c, REQUEST)
        assert self.acked == 1
        assert int.from_bytes(self.uc.mem_read(0x20000164, 4), 'little') == 0x30
        assert self.refresh[-1] == {'delay': 50, 'command': '2104', 'function': 15}
        return self.a4()


class DefaultsMachine(NativeAcSettingsMachine):
    def __init__(self):
        super().__init__()
        self.uc.mem_write(SETTINGS+0xd, struct.pack('<H', 1))
        self.uc.mem_write(SETTINGS+0x2c, b'\xf7')

    def step(self, uc, address, size, data):
        if address == 0x080192a0:
            self.back(0)  # Synthetic product-specific option, not a power state.
        elif (0x08029aa0 <= address < 0x08029b70
              or 0x0801a6bc <= address < 0x0801a6c8
              or 0x08028944 <= address < 0x08028960
              or 0x080288e6 <= address < 0x080288ea):
            return
        else:
            super().step(uc, address, size, data)

    def loaded_settings_decision(self):
        # Enter only AFTER successful synthetic file read and CRC check.
        self.stopped = False
        self.uc.mem_write(STACK-8, struct.pack('<II', 0, STOP|1))
        self.uc.reg_write(UC_ARM_REG_SP, STACK-0x60)
        self.uc.reg_write(UC_ARM_REG_R4, SETTINGS)
        self.uc.emu_start(0x08028945, 0, count=30000)
        assert self.stopped


class CapsMachine(D9Machine):
    def __init__(self, upper=100, lower=1, reserve=10, tariff=0, backup=False):
        super().__init__()
        self.uc.mem_write(SETTINGS+0x17, bytes((upper, lower, reserve)))
        self.uc.mem_write(0x20001d76, bytes((int(bool(tariff)), int(bool(tariff)), tariff, 0, 24))+bytes(15))
        self.uc.mem_write(0x2000f00a, bytes((int(backup),)))
        self.bms = []

    def step(self, uc, address, size, data):
        if address in (0x080107f4, 0x0801095c, 0x080105cc, 0x0802940c):
            self.back()
        elif address == 0x08020a4c:
            assert uc.reg_read(UC_ARM_REG_R0) == 3
            self.back(0x2000f100)
        elif address == 0x08027e40:
            descriptor = uc.reg_read(UC_ARM_REG_R1)
            raw = bytes(uc.mem_read(descriptor, 24))
            assert int.from_bytes(raw[10:12], 'little') == 0xf015
            assert int.from_bytes(raw[12:14], 'little') == 3
            pointer = int.from_bytes(raw[16:20], 'little')
            self.bms.append(list(uc.mem_read(pointer, 3)))
            self.back(1)
        elif any(lo <= address < hi for lo, hi in (
            (0x0800c530, 0x0800c6f2), (0x0802b504, 0x0802b514),
            (0x0802b528, 0x0802b53a), (0x0800dc50, 0x0800dd3e),
            (0x08029542, 0x080295a2))):
            return
        else:
            super().step(uc, address, size, data)

    def lower(self, value):
        self.acked = 0
        body = bytes.fromhex('a10122ab0201')+bytes((value,))+bytes.fromhex('fe050300f15365')
        self.uc.mem_write(PAYLOAD, body)
        self.uc.reg_write(UC_ARM_REG_R1, len(body))
        self.uc.reg_write(UC_ARM_REG_R2, REQUEST+0x18)
        self.run(0x080225a6, PAYLOAD)
        self.run(0x0800c530, REQUEST)
        assert self.acked == 1
        return self.d9()

    def bounds(self):
        self.run(0x0800dc50, 0)
        return self.bms[-1]


class CapMirrorsMachine(SettingsMachine):
    """One RAM image, actual lower handler and both A4/D9 serializers.

    Full A4 descriptor mode1's two remaining-time getters return synthetic zero;
    backup-status calculation is the same zeroed substitute as D9Machine.
    """
    def step(self, uc, address, size, data):
        if address in (0x08017fac, 0x080185b4):
            self.back(0)
        elif address == 0x08018aac:
            self.back(0x2000f000)
        elif any(lo <= address < hi for lo, hi in (
            (0x0802b528, 0x0802b53a), (0x080190d4, 0x080191fe),
            (0x0801a608, 0x0801a6cc), (0x0801bcc4, 0x0801bd3a),
            (0x0800d1d8, 0x0800d1f4))):
            return
        else:
            super().step(uc, address, size, data)

    def a4(self, mode=3):
        self.uc.mem_write(BUFFER, bytes(128))
        self.uc.mem_write(LENGTH, b'\0\0')
        self.uc.mem_write(DESC, struct.pack('<B3xIIHBx', 4, BUFFER, LENGTH, 128, mode))
        self.run(0x0801a018, DESC)
        size = int.from_bytes(self.uc.mem_read(LENGTH, 2), 'little')
        raw = bytes(self.uc.mem_read(BUFFER, size))
        assert size == 35 and raw[:2] == bytes((34, 4))
        return raw[1:]

    def d9(self):
        return D9Machine.d9(self)


def main():
    results = {'power_validator': [], 'power_policy': [], 'lower_caps': [], 'bms_bounds': [],
               'native_power_handler': [], 'native_fast_charge': [], 'default_recovery': [],
               'loaded_settings_decision': [], 'a4_d9_limit_mirrors': []}
    for power in (0, 1, 99, 100, 101, 200, 300, 600, 1200, 1201, 65535):
        machine = ValidatorMachine()
        machine.uc.mem_write(SETTINGS+0xf, struct.pack('<H', power))
        invalid = machine.run(0x0802c890, 0)
        assert invalid == int(not 100 <= power <= 1200), (power, invalid)
        results['power_validator'].append({'configured_watts': power, 'invalid': invalid})
        machine = NativeAcSettingsMachine()
        before = bytes(machine.uc.mem_read(SETTINGS, 0x190))
        a4 = machine.ac_command(0xa4, power)
        after = bytes(machine.uc.mem_read(SETTINGS, 0x190))
        assert int.from_bytes(a4[5:7], 'little') == power
        assert before[:0xf] == after[:0xf] and before[0x11:] == after[0x11:]
        assert machine.feedback == ([16] if power <= 600 else [])
        assert machine.scheduled == 1
        results['native_power_handler'].append({'configured_watts': power, 'a4_readback': power,
            'feedback_codes': machine.feedback, 'persistence_requests': machine.scheduled,
            'other_persistent_settings_unchanged': True})
        for tariff in (0, 1, 2, 3):
            for fast in (False, True):
                machine = PolicyMachine(tariff, 91, 10, fast)
                machine.uc.mem_write(SETTINGS+0xf, struct.pack('<H', power))
                result = machine.policy()
                assert result['reserve'] == 10
                assert result['fast_charge'] == (fast and tariff == 0)
                ac = [plan['power_voltage_current'] for plan in result['charge_plans'] if plan['channel'] == 0]
                assert len(ac) == 1
                assert ac[0][2] == (100 if tariff in (0, 3) else 0)
                if power == 0:
                    assert ac[0][0] == (1400 if fast and tariff == 0 else 0)
                results['power_policy'].append({'configured_watts': power, 'tariff': tariff,
                                                'initial_fast': fast, 'result': result})
    for tariff in (0, 1, 2, 3):
        for initial in (False, True):
            for requested in (0, 1, 2, 255):
                machine = NativeAcSettingsMachine(tariff, initial)
                before = bytes(machine.uc.mem_read(SETTINGS, 0x190))
                a4 = machine.ac_command(0xa7, requested)
                expected = int(initial if tariff and requested else bool(requested))
                assert a4[21] == expected
                assert bytes(machine.uc.mem_read(SETTINGS, 0x190)) == before
                assert machine.scheduled == 0
                assert machine.feedback == ([9] if not initial and expected else [])
                results['native_fast_charge'].append({'active_tariff': tariff, 'initial_fast': initial,
                    'requested_byte': requested, 'readback': a4[21], 'acknowledged': True,
                    'feedback_codes': machine.feedback, 'persistent_settings_unchanged': True})
    machine = DefaultsMachine()
    machine.uc.mem_write(SETTINGS+0xf, b'\0\0')
    before = machine.a4()
    machine.run(0x08029aa0, 0)
    after = machine.a4()
    assert int.from_bytes(before[5:7], 'little') == 0
    assert int.from_bytes(after[5:7], 'little') == 1200
    assert machine.run(0x0802c890, 0) == 0
    results['default_recovery'].append({'initial_power': 0, 'recovered_power': 1200,
        'device_timeout_before_minutes': int.from_bytes(before[14:16], 'little'),
        'device_timeout_after_minutes': int.from_bytes(after[14:16], 'little'),
        'changed_a4_offsets': [i for i, (a, b) in enumerate(zip(before, after)) if a != b],
        'synthetic_product_option': 0, 'flash_loader_replayed': False})
    for power in (0, 99, 100, 1200, 1201):
        machine = DefaultsMachine()
        machine.uc.mem_write(SETTINGS+0xf, struct.pack('<H', power))
        machine.loaded_settings_decision()
        a4 = machine.a4()
        valid = 100 <= power <= 1200
        actual_power = int.from_bytes(a4[5:7], 'little')
        timeout = int.from_bytes(a4[14:16], 'little')
        assert actual_power == (power if valid else 1200)
        assert timeout == (0 if valid else 720)
        results['loaded_settings_decision'].append({'loaded_power': power,
            'power_after_load_decision': actual_power, 'timeout_after_minutes': timeout,
            'synthetic_successful_file_and_crc': True, 'actual_loader_branch': '08028944'})
    for reserve in (10, 25, 85):
        for lower in (1, 5, 10, 15, 20):
            machine = CapsMachine(reserve=reserve)
            before = bytes(machine.uc.mem_read(SETTINGS, 0x190))
            d9 = machine.lower(lower)
            expected_reserve = max(reserve, lower+5)
            assert d9[3:6] == bytes((expected_reserve, 100, lower))
            after = bytes(machine.uc.mem_read(SETTINGS, 0x190))
            changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
            assert set(changed) <= {0x18, 0x19}
            bms = machine.bounds()
            assert bms == [100, lower, lower]
            restored = machine.lower(1)
            assert restored[3:6] == bytes((expected_reserve, 100, 1))
            results['lower_caps'].append({'baseline_reserve': reserve, 'lower': lower,
                'readback_reserve_upper_lower': list(d9[3:6]), 'changed_settings_offsets': changed,
                'bms_f015_bytes': bms, 'restored_lower_only_readback': list(restored[3:6])})
    for upper, lower, reserve in ((100, 1, 10), (90, 5, 85), (80, 20, 85), (80, 20, 10)):
        for tariff in (0, 1, 2, 3):
            for backup in (False, True):
                machine = CapsMachine(upper, lower, reserve, tariff, backup)
                bms = machine.bounds()
                effective = lower if tariff == 0 else max(lower, min(100 if backup else upper, reserve))
                assert bms == [upper, lower, effective], (upper, lower, reserve, tariff, backup, bms)
                results['bms_bounds'].append({'upper': upper, 'lower': lower, 'reserve': reserve,
                    'tariff': tariff, 'backup_active': backup, 'bms_f015_bytes': bms})
    for upper in (80, 85, 90, 95, 100):
        for lower in (1, 5, 10, 15, 20):
            for a4_mode in (1, 3):
                machine = CapMirrorsMachine()
                reserve = max(10, lower+5)
                machine.uc.mem_write(SETTINGS+0x17, bytes((upper, 1, reserve)))
                before_a4, before_d9 = machine.a4(a4_mode), machine.d9()
                machine.command(0xab, lower)
                a4, d9 = machine.a4(a4_mode), machine.d9()
                assert a4[24] == d9[4] == upper
                assert a4[25] == d9[5] == lower
                expected = bytearray(before_a4); expected[25] = lower
                assert a4 == bytes(expected)
                expected = bytearray(before_d9); expected[5] = lower
                assert d9 == bytes(expected)
                machine.command(0xab, 1)
                assert machine.a4(a4_mode) == before_a4 and machine.d9() == before_d9
                results['a4_d9_limit_mirrors'].append({'upper': upper, 'lower': lower,
                    'reserve': reserve, 'a4_descriptor_mode': a4_mode,
                    'upper_mirrors_a4_24_d9_4': upper, 'lower_mirrors_a4_25_d9_5': lower,
                    'only_lower_mirrors_changed': True, 'exact_round_trip': True})
    path = ROOT/'charging-followup-results.json'
    path.write_text(json.dumps(results, indent=2)+'\n'); path.chmod(0o600)
    print(json.dumps({key: len(value) for key, value in results.items()}))


if __name__ == '__main__':
    main()
