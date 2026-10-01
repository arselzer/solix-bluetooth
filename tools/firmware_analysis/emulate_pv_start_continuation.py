"""Offline C1000 Gen 2 DCDC mode-1 continuation; no hardware or network.

Uses exact hashed DSP words and the previous limited interpreter's fault
predicate replay. Synthetic RAM/MMIO has no physical peripheral semantics.
Entry points and excluded scheduling/state-machine stages are in the manifest.
"""

import hashlib
import json
import os
from pathlib import Path
import platform

import unicorn

from emulate_pv_retry_actuation import DspStatusReplay, DSP_HASH, DSP_NAME, dsp_words
from replay_io import ROOT

os.umask(0o077)
MODE, PREVIOUS, REASON = 0xacea, 0xaceb, 0xacef
FLAGS, RUN, COUNTER = 0xacf0, 0xacf1, 0xacf3
PERIODIC, FAULT1, FAULT2 = 0xaaae, 0xad17, 0xad16
START_MMIO = (0x4097, 0x4197)


class ContinuationReplay(DspStatusReplay):
    """Fail-closed integer subset for the selected continuation and callees."""

    def __init__(self, words, *, events=1, periodic=1, fault1=0, fault2=0,
                 counter=0, request=1):
        super().__init__(words, qualifier=1, mode=1, fault1=fault1,
                         fault2=fault2, status=0xffff)
        self.ar1, self.ar6, self.ar7 = events, 0, 0
        self.mem.update({PERIODIC: periodic << 6, RUN: request, COUNTER: counter,
                         PREVIOUS: 9, REASON: 0x55, FLAGS: 0xffff,
                         0xaced: 7, 0xab40: 0xffff, 0xabbf: 0xffff,
                         0x7f05: 0x1000, 0x4097: 0x1200, 0x4197: 0x3401})
        self.protected = self.interrupt_masked = False
        self.writes = []
        self.unsigned_ge = False

    def operand(self, code):
        if code == 0xa1:
            return self.ar1 & 0xffff
        if code == 0xa7:
            return self.ar7 & 0xffff
        if code == 0xa9:
            return self.al
        if code == 0xa8:
            return self.ah
        if code >= 0x40:
            raise RuntimeError(f"Unsupported operand {code:02x}")
        return self.mem.get((self.dp << 6) + code, 0)

    def write(self, code, value, pc):
        if code >= 0x40:
            raise RuntimeError(f"Unsupported write operand {code:02x}")
        address = (self.dp << 6) + code
        self.mem[address] = value & 0xffff
        self.writes.append({"pc": f"{pc:06x}", "address": f"{address:04x}",
                            "value": value & 0xffff,
                            "protected_access": self.protected,
                            "interrupt_masked": self.interrupt_masked})

    def compare(self, value, rhs):
        signed = value if value < 0x8000 else value - 0x10000
        self.eq, self.lt, self.ge = signed == rhs, signed < rhs, signed >= rhs
        self.unsigned_ge = value >= rhs

    def execute(self, pc=0x087464, stop_at=0x0874a1):
        stack = []
        for _ in range(1000):
            if pc == stop_at:
                assert not stack
                return
            self.visited.add(pc)
            op, following = self.words[pc], self.words[pc + 1]
            nxt, low = pc + 1, op & 0xff
            if op == 0x761f:
                self.dp = following
                nxt += 1
            elif op == 0x7648:
                target = 0x080000 | following
                if target == 0x087422:
                    # The earlier tool executes this whole integer predicate,
                    # including real fault getters; no return value is stubbed.
                    DspStatusReplay.run(self, target)
                    nxt += 1
                else:
                    stack.append(pc + 2)
                    nxt = target
            elif op == 0x0006:
                if not stack:
                    return
                nxt = stack.pop()
            elif op & 0xff00 == 0x9200:
                self.al = self.operand(low)
                self.eq = self.al == 0
            elif op & 0xff00 == 0x9300:
                self.ah = self.operand(low)
                self.eq = self.ah == 0
            elif op & 0xff00 == 0xcc00:
                self.al = self.operand(low) & following
                self.eq = self.al == 0
                nxt += 1
            elif op & 0xfff0 == 0xffc0:
                self.al >>= (op & 15) + 1
                self.eq = self.al == 0
            elif op & 0xf000 == 0x4000:
                self.tc = bool(self.operand(low) & (1 << ((op >> 8) & 15)))
            elif op & 0xff00 in (0x5200, 0x5300):
                self.compare(self.ah if op >> 8 == 0x53 else self.al, low)
            elif op & 0xff00 in (0x9c00, 0x9d00):
                value = self.ah if op >> 8 == 0x9d else self.al
                value = (value + (low if low < 128 else low - 256)) & 0xffff
                if op >> 8 == 0x9d:
                    self.ah = value
                else:
                    self.al = value
                self.eq = value == 0
            elif op & 0xff00 in (0x6000, 0x6100, 0x6300, 0x6400,
                                 0x6700, 0x6800, 0x6c00, 0x6d00, 0x6f00):
                take = {0x60: not self.eq, 0x61: self.eq, 0x63: self.ge,
                        0x64: self.lt, 0x67: self.unsigned_ge,
                        0x68: not self.unsigned_ge, 0x6c: not self.tc,
                        0x6d: self.tc, 0x6f: True}[op >> 8]
                if take:
                    nxt = pc + (low if low < 128 else low - 256)
            elif op in (0x56bf, 0x56b1):
                if op == 0x56bf or self.eq:
                    self.write(following & 0xff, following >> 8, pc)
                nxt += 1
            elif op & 0xff00 == 0x9600:
                self.write(low, self.al, pc)
            elif op & 0xff00 == 0x2b00:
                self.write(low, 0, pc)
            elif op & 0xff00 == 0x0a00:
                self.write(low, self.operand(low) + 1, pc)
            elif op & 0xff00 == 0x1a00:
                self.write(low, self.operand(low) | following, pc)
                nxt += 1
            elif op & 0xff00 == 0x1800:
                self.write(low, self.operand(low) & following, pc)
                nxt += 1
            elif op & 0xff00 == 0x9800:
                self.write(low, self.operand(low) | self.al, pc)
            elif op & 0xff00 == 0x9000:
                self.al &= low
            elif op & 0xff00 == 0x9a00:
                self.al = low
            elif op & 0xff00 == 0xbe00:
                self.ar6 = low
            elif op == 0x5603 and following == 0x04a9:
                value = self.al << 4
                self.al, self.ah = value & 0xffff, value >> 16
                nxt += 1
            elif op in (0x7622, 0x761a):
                self.protected = op == 0x7622
            elif op in (0x3b10, 0x2910):
                self.interrupt_masked = op == 0x3b10
            elif op == 0xff69:  # SPM #0; no multiply occurs in these paths.
                pass
            else:
                raise RuntimeError(f"Unsupported instruction {pc:06x}: {op:04x}")
            pc = nxt
        raise RuntimeError("Instruction limit exceeded")

    def snapshot(self):
        return {"mode": self.mem[MODE], "previous_mode": self.mem[PREVIOUS],
                "reason": self.mem[REASON], "counter": self.mem[COUNTER],
                "run_request": self.mem[RUN], "writes": self.writes.copy()}

    def start_writes(self):
        return [row for row in self.writes if int(row["address"], 16) in START_MMIO]


def main():
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    words = dsp_words()
    result = {"periodic_guards": [], "counter_boundaries": [], "fault_reset": [],
              "second_fault_word_scope": [], "priority_events": [],
              "start_stop_at_threshold": [], "periodic_flag_producer": [],
              "qualifying_tick_sequences": []}

    def continuation(group, label, **inputs):
        replay = ContinuationReplay(words, **inputs)
        before = dict(replay.mem)
        replay.execute()
        event, pulse = bool(replay.ar1 & 1), bool(before[PERIODIC] & 0x40)
        expected_counter = before[COUNTER]
        starts = False
        if event and pulse:
            if before[FAULT1]:
                expected_counter = 0
            else:
                incremented = (before[COUNTER] + 1) & 0xffff
                expected_counter = min(incremented, 100)
                starts = incremented >= 100 and before[RUN] == 1
                if starts:
                    expected_counter = 0
        assert replay.mem[COUNTER] == expected_counter
        exits_on_fault = event and pulse and bool(before[FAULT1])
        assert replay.mem[MODE] == (4 if exits_on_fault else (2 if starts else 1))
        assert bool(replay.start_writes()) == starts
        assert replay.mem[RUN] == (0 if exits_on_fault else before[RUN])
        if exits_on_fault:
            assert replay.mem[PREVIOUS] == 1
            assert replay.mem[REASON] == before[REASON]
            assert replay.mem[FLAGS] == before[FLAGS]
        assert replay.mem[FAULT1] == before[FAULT1] and replay.mem[FAULT2] == before[FAULT2]
        if starts:
            assert [row["address"] for row in replay.start_writes()] == ["4097", "4197"]
            assert all(row["protected_access"] for row in replay.start_writes())
            assert replay.mem[PREVIOUS] == 1
        assert not replay.protected and not replay.interrupt_masked
        result[group].append({"case": label, "inputs": inputs, "after": replay.snapshot()})

    for event in (0, 1):
        for pulse in (0, 1):
            for fault in (0, 1):
                continuation("periodic_guards", f"event{event}_pulse{pulse}_fault{fault}",
                             events=8 | event, periodic=pulse, fault1=fault, counter=99)
    for counter in (0, 1, 98, 99, 100):
        for request in (0, 1, 2):
            continuation("counter_boundaries", f"count{counter}_request{request}",
                         counter=counter, request=request)
    for fault in (1, 0x8000):
        for counter in (0, 99, 100):
            continuation("fault_reset", f"fault{fault}_count{counter}",
                         fault1=fault, counter=counter)
    for fault in (1 << 1, 1 << 4, 1 << 5, 0xffff):
        continuation("second_fault_word_scope", f"already_latched_request_fault2_{fault}",
                     fault2=fault, counter=99, request=1)

    # Include the preceding selected-event fragment to establish preemption.
    # Empty-event prologue and event-bank retrieval remain outside the fixture.
    for event in (1 << 1, 1 << 6, (1 << 1) | (1 << 6)):
        for request in (0, 1):
            replay = ContinuationReplay(words, events=event | 1, counter=99, request=request)
            replay.execute(0x08744c)
            expected_mode, reason = (6, 4) if event & (1 << 6) else (4, 12)
            assert replay.mem[MODE] == expected_mode and replay.mem[REASON] == reason
            assert replay.mem[RUN] == 0 and replay.mem[PREVIOUS] == 1
            assert replay.mem[FLAGS] == 0xfffc and replay.mem[0xaced] == 0
            assert replay.mem[0xab40] == 0xfffe and replay.mem[0xa49e] == 0xffef
            assert replay.mem[0xabbf] == (0xffff & 0xffed & 0xfff6)
            assert replay.mem[0x7f05] == 0x1003 and not replay.start_writes()
            assert not replay.interrupt_masked
            result["priority_events"].append({"event_bits": event | 1,
                                               "old_request": request, "after": replay.snapshot()})
    for event in (1 << 3, 1 << 4, (1 << 3) | (1 << 4), 1 << 2):
        for request in (0, 1):
            replay = ContinuationReplay(words, events=event | 1, counter=99, request=request)
            replay.execute(0x08744c)
            after_request = 0 if event & (1 << 4) else (1 if event & (1 << 3) else request)
            assert replay.mem[RUN] == after_request
            assert replay.mem[MODE] == (2 if after_request == 1 else 1)
            assert bool(replay.start_writes()) == bool(after_request)
            result["start_stop_at_threshold"].append({"event_bits": event | 1,
                                                     "old_request": request, "after": replay.snapshot()})

    # Replay the periodic flag's actual divider tail, starting after its
    # upstream quotient-change decision. No real time is injected or inferred.
    for quotient_edge in (0, 1):
        for count in (0, 3, 4, 5):
            replay = ContinuationReplay(words)
            replay.dp, replay.ar7 = 0x2aa, quotient_edge
            replay.mem[0xaabb], replay.mem[PERIODIC] = count, 0xffff
            replay.execute(0x0894a5, stop_at=0x0894b4)
            pulse = bool(quotient_edge and count + 1 >= 5)
            assert bool(replay.mem[PERIODIC] & 0x40) == pulse
            assert replay.mem[PERIODIC] & ~0x40 == 0xffff & ~0x40
            assert replay.mem[0xaabb] == (0 if pulse else count + quotient_edge)
            result["periodic_flag_producer"].append({"quotient_edge": quotient_edge,
                "old_divider_count": count, "new_divider_count": replay.mem[0xaabb],
                "periodic_bit6": pulse, "writes": replay.writes})

    for name in ("normal", "wait_for_request", "periodic_paused", "event_paused", "fault_exits_mode1"):
        replay = ContinuationReplay(words)
        stages = []

        def ticks(count, *, request=1, periodic=1, event=1, fault=0):
            replay.mem[RUN], replay.mem[PERIODIC] = request, periodic << 6
            replay.ar1, replay.mem[FAULT1] = event, fault
            for _ in range(count):
                assert replay.mem[MODE] == 1
                replay.execute()
            stages.append({"invocations": count, "request": request, "periodic": periodic,
                           "event_bits": event, "fault1": fault, "mode": replay.mem[MODE],
                           "counter": replay.mem[COUNTER]})

        if name == "wait_for_request":
            ticks(120, request=0)
            assert replay.mem[COUNTER] == 100
            ticks(1)
        else:
            if name == "periodic_paused":
                ticks(50, periodic=0)
            if name == "event_paused":
                ticks(50, event=8)
            if name == "fault_exits_mode1":
                ticks(60)
                ticks(1, fault=1)
                assert replay.mem[MODE] == 4 and replay.mem[RUN] == 0
            else:
                ticks(99)
                assert replay.mem[MODE] == 1 and replay.mem[COUNTER] == 99
                ticks(1)
        assert replay.mem[MODE] == (4 if name == "fault_exits_mode1" else 2)
        assert replay.mem[COUNTER] == 0
        assert len(replay.start_writes()) == (0 if name == "fault_exits_mode1" else 2)
        result["qualifying_tick_sequences"].append({"case": name, "stages": stages,
                                                    "start_write_intents": replay.start_writes()})

    counts = {key: len(rows) for key, rows in result.items()}
    assert counts == {"periodic_guards": 8, "counter_boundaries": 15, "fault_reset": 6,
                      "second_fault_word_scope": 4, "priority_events": 6,
                      "start_stop_at_threshold": 8, "periodic_flag_producer": 8,
                      "qualifying_tick_sequences": 5}
    ROOT.mkdir(parents=True, exist_ok=True)
    output = ROOT / "pv-start-continuation-results.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.chmod(0o600)
    sources = (Path(__file__).name, "emulate_pv_retry_actuation.py", "emulate_pv_retry_origins.py",
               "emulate_general_settings.py", "emulate_additional_features.py",
               "emulate_clock_semantics.py", "replay_io.py", "extract_dsp.py")
    manifest = {"hardware_access": False, "network_access": False, "case_counts": counts,
                "firmware": {DSP_NAME: DSP_HASH},
                "runtime": {"python": platform.python_version(), "unicorn_import_version": unicorn.__version__},
                "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in sources},
                "function_return_substitutions": [],
                "synthetic_boundaries": ["RAM and MMIO values", "event word after retrieval",
                                         "periodic quotient edge", "initial DSP mode1 state"],
                "executed": ["087464..0874a1 continuation", "selected-event prefix08744c",
                             "real fault and periodic getters", "cleanup08730a with real callees",
                             "periodic divider tail0894a5..0894b4"],
                "excluded": ["UART", "event retrieval and empty-event prologue", "full DSP dispatcher",
                             "real timer ISR/frequency", "fault producers", "peripheral side effects",
                             "mode2 and later regulation", "physical PV/battery power"]}
    path = ROOT / "pv-start-continuation-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
