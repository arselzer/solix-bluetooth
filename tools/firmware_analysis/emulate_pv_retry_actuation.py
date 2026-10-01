"""Offline A1763 DC-input status -> policy -> MPPT queued-command replay.

Executes exact ARM firmware and a fail-closed integer C28x subset. Hardware,
UART, DSP actuation, timing and app transport are excluded. See the manifest
and companion document for substituted allocation/queue and event boundaries.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import struct

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_pv_retry_origins import RetryOriginMachine
from emulate_general_settings import SETTINGS, TIMERS, EVENT
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, REPOSITORY_FIRMWARE, ROOT, firmware_image
from extract_dsp import parse as parse_dsp, PAYLOAD

os.umask(0o077)
DSP_NAME = "dspDCDC-decoded.bin"
DSP_HASH = "7ae2bb915812441b8316d1c3f3efc2379fcb488d4d03e950901cf507c224ceb8"


def dsp_words():
    path = Path(os.environ.get("SOLIX_FIRMWARE_DIR", REPOSITORY_FIRMWARE))/DSP_NAME
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != DSP_HASH:
        raise ValueError("Wrong DCDC image; addresses require the documented hash")
    words = {}
    for record in parse_dsp(data):
        block = data[PAYLOAD+record["offset"]:PAYLOAD+record["offset"]+record["size_octets"]]
        for index, word in enumerate(struct.unpack(">"+str(len(block)//2)+"H", block)):
            words[record["address"]+index] = word
    assert words[0x082f4c] | (words[0x082f4d] << 16) == 0x086acf
    return words


class DspStatusReplay:
    """Only instructions reached by the named integer predicates and setters."""
    def __init__(self, words, *, qualifier, mode, fault1, fault2, status):
        self.words = words
        self.mem = {0xaaae: qualifier, 0xacea: mode, 0xad17: fault1, 0xad16: fault2, 0xa49e: status}
        self.al = self.ah = self.dp = self.t = self.ar1 = self.xar4 = 0
        self.tc = False
        self.eq = self.lt = self.ge = False
        self.visited = set()

    def run(self, pc, stop_at=None):
        stack = []
        for _ in range(200):
            if pc == stop_at:
                assert not stack
                return self.al
            self.visited.add(pc)
            op, following = self.words[pc], self.words[pc+1]
            nxt = pc+1
            if op == 0x761f:
                self.dp = following; nxt += 1
            elif op == 0x7648:
                stack.append(pc+2); nxt = 0x080000 | following
            elif op == 0x0006:
                if not stack: return self.al
                nxt = stack.pop()
            elif op == 0x92a1:
                self.al = self.ar1 & 0xffff
                self.eq = self.al == 0
            elif op & 0xff00 == 0x9200:  # MOV AL, direct word
                self.al = self.mem.get((self.dp << 6)+(op & 0xff), 0)
                self.eq = self.al == 0
            elif op & 0xff00 == 0xcc00:  # AND AL, direct word, immediate
                self.al = self.mem.get((self.dp << 6)+(op & 0xff), 0) & following; nxt += 1
            elif op & 0xff00 == 0x5200:
                rhs = op & 0xff
                signed = self.al if self.al < 0x8000 else self.al-0x10000
                self.eq, self.lt, self.ge = signed == rhs, signed < rhs, signed >= rhs
            elif op & 0xff00 in (0x6000, 0x6100, 0x6300, 0x6400, 0x6700, 0x6c00, 0x6d00, 0x6f00):
                code = op >> 8
                take = {0x60: not self.eq, 0x61: self.eq, 0x63: self.ge, 0x64: self.lt, 0x67: self.ge, 0x6c: not self.tc, 0x6d: self.tc, 0x6f: True}[code]
                if take:
                    delta = op & 0xff
                    nxt = pc+(delta if delta < 128 else delta-256)
            elif op & 0xff00 == 0x9a00:
                self.al = op & 0xff
            elif op & 0xff00 == 0x5300:
                rhs = op & 0xff
                self.eq, self.lt, self.ge = self.ah == rhs, self.ah < rhs, self.ah >= rhs
            elif op & 0xff00 == 0x9b00:
                self.ah = op & 0xff
            elif op & 0xff00 == 0x9c00:
                delta = op & 0xff
                self.al = (self.al+(delta if delta < 128 else delta-256)) & 0xffff
                self.eq = self.al == 0
            elif op == 0x2da9:
                self.t = self.al
            elif op == 0x2da8:
                self.t = self.ah
            elif op == 0xff67:
                self.ah = (self.ah << self.t) & 0xffff
                self.eq = self.ah == 0
            elif op & 0xff00 == 0xcf00:
                self.ah &= self.mem.get((self.dp << 6)+(op & 0xff), 0)
                self.eq = self.ah == 0
            elif op & 0xf0ff == 0x40a9:
                self.tc = bool(self.al & (1 << ((op >> 8) & 15)))
            elif op == 0x56b1:  # MOVB direct, immediate, EQ
                if self.eq:
                    self.mem[(self.dp << 6)+(following & 0xff)] = following >> 8
                nxt += 1
            elif op & 0xff00 == 0x2b00:
                self.mem[(self.dp << 6)+(op & 0xff)] = 0
            elif op == 0x2901:  # CLRC SXM; subsequent ACC operations are unsigned
                pass
            elif op == 0x8f00:
                self.xar4 = following; nxt += 1
            elif op == 0x5603 and following == 0x01a9:
                value = self.al << 1
                self.al, self.ah = value & 0xffff, value >> 16; nxt += 1
            elif op == 0x5601 and following == 0x00a4:
                self.xar4 += self.al | (self.ah << 16); nxt += 1
            elif op == 0x0201:
                self.al, self.ah = 1, 0
            elif op == 0x563b:
                value = ((self.al | (self.ah << 16)) << self.t) & 0xffffffff
                self.al, self.ah = value & 0xffff, value >> 16
            elif op == 0x98c4:
                self.mem[self.xar4] = self.mem.get(self.xar4, 0) | self.al
            elif op == 0x99cc:
                self.mem[self.xar4+1] = self.mem.get(self.xar4+1, 0) | self.ah
            elif op == 0x5603 and following == 0x05a9:
                self.al = (self.al << 5) & 0xffff; nxt += 1
            elif op & 0xff00 == 0x1800:
                address = (self.dp << 6)+(op & 0xff)
                self.mem[address] = self.mem.get(address, 0) & following; nxt += 1
            elif op & 0xff00 == 0x9000:
                self.al &= op & 0xff
            elif op & 0xff00 == 0x9800:
                address = (self.dp << 6)+(op & 0xff)
                self.mem[address] = self.mem.get(address, 0) | self.al
            else:
                raise RuntimeError(f"Unimplemented C28x instruction at {pc:06x}: {op:04x}")
            pc = nxt
        raise RuntimeError("DSP instruction limit exceeded")


class ActuationMachine(RetryOriginMachine):
    def __init__(self, *, active=False, blocked=False, bms_scalar11=1):
        super().__init__(locked=False)
        # Execute the firmware's own initialized-data decompressor. This loads
        # the real ROM policy row descriptors instead of constructing rules.
        self.uc.reg_write(UC_ARM_REG_R1, 0x20000000)
        self.uc.reg_write(UC_ARM_REG_R2, 0xbe0)
        self.run(0x08005b6a, 0x080353d4)
        self.uc.mem_write(0x20000164, struct.pack("<I", 0x30 | int(active) | (int(blocked) << 23)))
        self.uc.mem_write(0x2000040c, struct.pack("<H", bms_scalar11))
        self.uc.mem_write(0x2000014d, b"\x01")  # Already awake; exclude first-wake actions.
        self.uc.mem_write(0x2000015b, b"\x03")
        self.uc.mem_write(0x20000462, b"\x03\0\0\x04\x05")
        self.uc.mem_write(EVENT, b"\x01"+bytes(11))
        for port in (3, 4):
            self.uc.reg_write(UC_ARM_REG_R1, 0x08031b6c)
            self.run(0x080291d4, port)
        self.queued = []
        self.allocation_ok = self.queue_ok = True
        self.freed = 0
        self.boundaries = []
        self.uc.mem_write(0x20000014, b"\x05")
        self.uc.mem_write(TIMERS+5*20, b"\x02"+bytes(19))

    def step(self, uc, address, size, data):
        r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if address == 0x08020a4c:
            assert r0 <= 64
            self.back(0x21001800 if self.allocation_ok else 0)
        elif address == 0x08027e40:
            assert r0 == 1 and r2 == 0
            desc = bytes(uc.mem_read(r1, 24))
            length = int.from_bytes(desc[12:14], "little")
            ptr = int.from_bytes(desc[16:20], "little")
            self.queued.append({"selector": int.from_bytes(desc[8:10], "little"),
                "register": int.from_bytes(desc[10:12], "little"),
                "payload_hex": bytes(uc.mem_read(ptr, length)).hex(),
                "callback": f"{int.from_bytes(desc[:4], 'little'):08x}",
                "accepted_by_substitute": self.queue_ok})
            self.back(int(self.queue_ok))
        elif address == 0x08017a8c:
            self.freed += 1; self.back()
        elif address == 0x0801687c:
            self.boundaries.append("MPPT diagnostic collector"); self.back()
        elif address == 0x0801c30c:
            self.boundaries.append("XT60i auxiliary configuration"); self.back()
        elif any(lo <= address < hi for lo, hi in (
            (0x08005b6a, 0x08005bc0), (0x080291d4, 0x080291e0),
            (0x08007258, 0x08008024), (0x08015fb4, 0x08016040),
            (0x08019594, 0x080195b2), (0x08018304, 0x0801832a),
            (0x08018da8, 0x08018dc6), (0x0802a2e8, 0x0802a338),
            (0x0802aeb8, 0x0802af2e), (0x0802b1d0, 0x0802b204),
            (0x08025140, 0x08025420), (0x0802a590, 0x0802a752),
            (0x0801649c, 0x080164b2), (0x0802b778, 0x0802b784))):
            return
        else:
            super().step(uc, address, size, data)

    def event(self, rising):
        self.run(0x0802601c, 2 if rising else 1)
        pending = int.from_bytes(self.uc.mem_read(0x200004c4, 2), "little")
        assert pending == (2 if rising else 0x80)
        self.run(0x08007258, 0)
        assert bytes(self.uc.mem_read(0x200004c4, 2)) == b"\0\0"
        return self.uc.mem_read(0x20000164, 1)[0] & 1

    def mppt(self):
        self.run(0x08025140, 0)


def main():
    words = dsp_words()
    results = {"dcdc_status_predicate": [], "real_dc_input_policy": [], "queued_mppt_actions": [],
               "dcdc_command_events": [], "dcdc_mode1_request_guard": []}
    for qualifier in (0, 1):
        for mode in (0, 4, 5):
            for faults in ((0, 0), (1, 0), (0, 0x8000)):
                for original in (0, 0x20, 0xffdf, 0xffff):
                    replay = DspStatusReplay(words, qualifier=qualifier, mode=mode,
                        fault1=faults[0], fault2=faults[1], status=original)
                    replay.run(0x084a5d)
                    status = replay.run(0x086acf)
                    expected = (original if not qualifier else
                        (original & ~0x20) | (int(mode != 4 and faults == (0, 0)) << 5))
                    assert status == expected
                    results["dcdc_status_predicate"].append({"qualifier": qualifier, "mode": mode,
                        "fault_words": faults, "old_register_0126": original, "new_register_0126": status,
                        "unchanged_when_qualifier_zero": not bool(qualifier), "function_returns_substituted": False})
    for command, event in ((0x57, 3), (0x58, 4), (0x59, 5), (0x56, None), (0, None)):
        replay = DspStatusReplay(words, qualifier=1, mode=1, fault1=0, fault2=0, status=0)
        replay.mem[0x419] = command
        replay.run(0x0866bf, stop_at=0x0866d0)
        flags = replay.mem.get(0xad0c, 0) | (replay.mem.get(0xad0d, 0) << 16)
        assert flags == (1 << event if event is not None else 0)
        results["dcdc_command_events"].append({"register_0015_word": command,
            "event_bank0_bits": flags, "function_returns_substituted": False,
            "entry_assumes_register_pending_bit": True})
    for events in (0, 1 << 3, 1 << 4, (1 << 3) | (1 << 4)):
        for faults in ((0, 0), (1, 0), (0, 1 << 4), (0, 1 << 5), (0, 1 << 1)):
            for previous in (0, 1):
                replay = DspStatusReplay(words, qualifier=1, mode=1,
                    fault1=faults[0], fault2=faults[1], status=0)
                replay.ar1 = events
                replay.mem[0xacf1] = previous
                replay.run(0x08744c, stop_at=0x087464)
                expected = previous
                if events & (1 << 3) and faults == (0, 0):
                    expected = 1
                if events & (1 << 4):
                    expected = 0
                assert replay.mem[0xacf1] == expected
                results["dcdc_mode1_request_guard"].append({"event_bits": events,
                    "fault_words": faults, "old_run_request": previous,
                    "new_run_request": replay.mem[0xacf1], "function_returns_substituted": False,
                    "stopped_before_periodic_converter_sequence": True})
    for active in (False, True):
        for blocked in (False, True):
            for scalar in (0, 1):
                for rising in (False, True):
                    m = ActuationMachine(active=active, blocked=blocked, bms_scalar11=scalar)
                    settings = bytes(m.uc.mem_read(SETTINGS, 0x190))
                    observed = m.event(rising)
                    expected = int(rising and not blocked and scalar != 0)
                    assert observed == expected
                    global_word = int.from_bytes(m.uc.mem_read(0x20000164, 4), "little")
                    assert global_word & 0x30 == 0x30
                    assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == settings
                    results["real_dc_input_policy"].append({"previous_dc_input_bit": active,
                        "global_bit23": blocked, "bms_sensor11_word": scalar, "rising": rising,
                        "dc_input_bit_after": observed, "output_state_and_saved_settings_preserved": True})
    for name, active, configured, ack, allocation, queue in (
        ("inactive_no_previous_command", False, False, None, True, True),
        ("inactive_previous_command", False, True, None, True, True),
        ("active_configuration_then_enable", True, False, 1, True, True),
        ("configuration_failure_no_enable", True, False, 0, True, True),
        ("input_lost_before_configuration_ack", True, False, 1, True, True),
        ("allocation_failure", True, False, None, False, True),
        ("queue_failure", True, False, None, True, False),
    ):
        m = ActuationMachine(active=active)
        m.allocation_ok, m.queue_ok = allocation, queue
        m.uc.mem_write(0x20000012, bytes((int(configured),)))
        m.uc.mem_write(0x20003f4c, b"\x20")
        settings = bytes(m.uc.mem_read(SETTINGS, 0x190))
        m.mppt()
        if name == "input_lost_before_configuration_ack":
            m.uc.mem_write(0x20000164, struct.pack("<I", 0x30))
        if ack is not None:
            m.run(0x0801649c, ack)
        registers = [row["register"] for row in m.queued]
        if name == "inactive_no_previous_command" or not allocation:
            assert not registers
        elif name == "inactive_previous_command":
            assert registers == [0x15] and m.queued[0]["payload_hex"] == "5800"
        elif ack == 1:
            assert registers == [0x16, 0x15]
            assert m.queued[-1]["payload_hex"] == ("5800" if "lost" in name else "5700")
        else:
            assert registers == [0x16]
        assert bytes(m.uc.mem_read(SETTINGS, 0x190)) == settings
        assert int.from_bytes(m.uc.mem_read(0x20000164, 4), "little") & 0x30 == 0x30
        results["queued_mppt_actions"].append({"case": name, "queued": m.queued,
            "buffers_freed": m.freed, "diagnostic_boundaries": m.boundaries,
            "output_state_and_saved_settings_preserved": True})
    counts = {key: len(value) for key, value in results.items()}
    assert counts == {"dcdc_status_predicate": 72, "real_dc_input_policy": 16, "queued_mppt_actions": 7,
                      "dcdc_command_events": 5, "dcdc_mode1_request_guard": 40}
    output = ROOT/"pv-retry-actuation-results.json"
    output.write_text(json.dumps(results, indent=2)+"\n"); output.chmod(0o600)
    sources = (Path(__file__).name, "emulate_pv_retry_origins.py", "emulate_general_settings.py",
               "emulate_additional_features.py", "emulate_clock_semantics.py", "replay_io.py", "extract_dsp.py")
    manifest = {"hardware_access": False, "case_counts": counts,
        "firmware": {FIRMWARE_NAME: FIRMWARE_SHA256, DSP_NAME: DSP_HASH},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in sources},
        "substitutions": ["inherited LCD/persistence/timer/ACK/logging boundaries", "allocation/free",
            "internal command queue", "MPPT diagnostic collector", "XT60i auxiliary configuration"],
        "excluded": ["DSP periodic qualification producer", "full DSP state machine/fault producers", "UART",
            "real asynchronous event delivery", "physical charger", "initial device wake path"]}
    output = ROOT/"pv-retry-actuation-manifest.json"
    output.write_text(json.dumps(manifest, indent=2)+"\n"); output.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
