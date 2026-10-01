"""Offline A1763 DCDC mode4 recovery with actual dispatcher and event bank.

Extends the preceding limited C28x subset in this new file. No hardware,
network, real clock, peripheral behavior or fault producers are modeled.
"""

import hashlib
import json
import os
from pathlib import Path
import platform

import unicorn

from emulate_pv_start_continuation import (
    ContinuationReplay, MODE, PREVIOUS, REASON, FLAGS, RUN, COUNTER,
    PERIODIC, FAULT1, FAULT2, DSP_NAME, DSP_HASH, dsp_words,
)
from emulate_pv_retry_actuation import DspStatusReplay
from replay_io import ROOT

os.umask(0o077)
HEALTHY, RETRY = 0xacf7, 0xacf8
LAST_MODE, INITIALIZED, EVENT_BANK = 0xacec, 0xacee, 0xad0c


class RecoveryReplay(ContinuationReplay):
    """The prior integer engine extended for dispatcher, stack and event bank."""

    def __init__(self, words, *, mode=4, last_mode=None, events=1,
                 healthy=0, retry=0, **kwargs):
        super().__init__(words, events=events, **kwargs)
        self.mem.update({MODE: mode, LAST_MODE: mode if last_mode is None else last_mode,
                         INITIALIZED: 1, HEALTHY: healthy, RETRY: retry,
                         EVENT_BANK: events & 0xffff, EVENT_BANK + 1: events >> 16})

    def write_address(self, address, value, pc):
        self.mem[address] = value & 0xffff
        self.writes.append({"pc": f"{pc:06x}", "address": f"{address:04x}",
                            "value": value & 0xffff, "protected_access": self.protected,
                            "interrupt_masked": self.interrupt_masked})

    def queue(self, events):
        self.mem[EVENT_BANK] |= events & 0xffff
        self.mem[EVENT_BANK + 1] |= events >> 16

    def snapshot(self):
        value = super().snapshot()
        value.update({"last_dispatched_mode": self.mem[LAST_MODE],
                      "healthy_counter": self.mem[HEALTHY], "retry_counter": self.mem[RETRY],
                      "fault1": self.mem[FAULT1], "fault2": self.mem[FAULT2],
                      "pending_events": self.mem[EVENT_BANK] | (self.mem[EVENT_BANK + 1] << 16)})
        return value

    def execute(self, pc=0x0874d5, stop_at=None):
        stack = []
        saved_ar1 = []
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
                if target in (0x087422, 0x08919f):
                    # The earlier tool executes this whole integer predicate,
                    # including real fault getters; no return value is stubbed.
                    DspStatusReplay.run(self, target)
                    nxt += 1
                else:
                    stack.append(pc + 2)
                    nxt = target
            elif op == 0x0006:
                if not stack:
                    assert not saved_ar1
                    return
                nxt = stack.pop()
            elif op == 0xb2bd:
                saved_ar1.append(self.ar1)
            elif op == 0x8bbe:
                self.ar1 = saved_ar1.pop()
            elif op == 0xff58:
                self.eq = (self.al | (self.ah << 16)) == 0
            elif op == 0x8ba9:
                self.ar1 = self.al | (self.ah << 16)
            elif op == 0x8f00:
                self.xar4 = following
                nxt += 1
            elif op == 0x5603 and following == 0x01a9:
                value = self.al << 1
                self.al, self.ah = value & 0xffff, value >> 16
                nxt += 1
            elif op == 0x5601 and following == 0x00a4:
                self.xar4 += self.al | (self.ah << 16)
                nxt += 1
            elif op == 0x06c4:
                self.al = self.mem.get(self.xar4, 0)
                self.ah = self.mem.get(self.xar4 + 1, 0)
            elif op == 0xc2c4:
                self.write_address(self.xar4, self.ar6 & 0xffff, pc)
                self.write_address(self.xar4 + 1, self.ar6 >> 16, pc)
            elif op == 0x0201:
                self.al, self.ah = 1, 0
            elif op == 0x2901:  # CLRC SXM; used by the integer event-bank helpers.
                pass
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
            elif op & 0xff00 == 0x5400:
                self.compare(self.al, self.operand(low))
            elif op & 0xff00 == 0x1b00:
                self.compare(self.operand(low), following)
                nxt += 1
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


def main():
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    words = dsp_words()
    results = {"dispatcher_entry": [], "healthy_threshold": [], "periodic_event_gates": [],
               "timed_fault_clear": [], "event_priority": [], "second_fault_word_eligibility": [],
               "recovery_sequences": []}

    def record(group, inputs, replay):
        assert not replay.protected and not replay.interrupt_masked
        results[group].append({"inputs": inputs, "after": replay.snapshot()})

    for mode in (4, 6):
        for transition in (False, True):
            for events in (0, 1, 9, 0x61):
                inputs = dict(mode=mode, last_mode=1 if transition else mode, events=events,
                              healthy=7, retry=11, fault1=1, request=1)
                replay = RecoveryReplay(words, **inputs)
                replay.execute()
                entered = transition or events == 0
                if entered:
                    assert replay.mem[MODE] == mode and replay.mem[REASON] == 9
                    assert replay.mem[HEALTHY] == replay.mem[RETRY] == replay.mem[RUN] == 0
                elif events & 0x40:
                    assert replay.mem[MODE] == 5 and replay.mem[REASON] == 10
                    assert replay.mem[PREVIOUS] == mode and replay.mem[RUN] == 0
                else:
                    assert replay.mem[MODE] == mode and replay.mem[RUN] == 1
                    assert replay.mem[HEALTHY] == 0 and replay.mem[RETRY] == 12
                assert replay.mem[FAULT1] == 1
                assert replay.mem[EVENT_BANK] == 1 and replay.mem[EVENT_BANK + 1] == 0
                assert not replay.start_writes()
                record("dispatcher_entry", inputs, replay)

    for mode in (4, 6):
        for count in (0, 198, 199, 200):
            for pulse in (0, 1):
                inputs = dict(mode=mode, healthy=count, retry=17, request=0, periodic=pulse)
                replay = RecoveryReplay(words, **inputs)
                replay.execute()
                recovered = bool(pulse and count + 1 >= 200)
                assert replay.mem[MODE] == (1 if recovered else mode)
                assert replay.mem[HEALTHY] == (0 if recovered else count + pulse)
                assert replay.mem[RETRY] == (0 if pulse and not recovered else 17)
                assert replay.mem[RUN] == 0
                assert not replay.start_writes()
                record("healthy_threshold", inputs, replay)

    for event_tick in (0, 1):
        for pulse in (0, 1):
            inputs = dict(events=8 | event_tick, periodic=pulse, healthy=199, request=0)
            replay = RecoveryReplay(words, **inputs)
            replay.execute()
            assert replay.mem[MODE] == (1 if event_tick and pulse else 4)
            assert replay.mem[RUN] == 0
            record("periodic_event_gates", inputs, replay)

    for fault in (1, 0x8000, 0x20, 0x21):
        for count in (0, 298, 299, 300):
            inputs = dict(fault1=fault, retry=count, healthy=99, request=0)
            replay = RecoveryReplay(words, **inputs)
            replay.execute()
            blocked = bool(fault & 0x20)
            cleared = not blocked and count + 1 >= 300
            assert replay.mem[FAULT1] == (0 if cleared else fault)
            assert replay.mem[RETRY] == (0 if blocked or cleared else count + 1)
            assert replay.mem[HEALTHY] == 0 and replay.mem[MODE] == 4
            assert replay.mem[RUN] == 0 and not replay.start_writes()
            record("timed_fault_clear", inputs, replay)

    for event in (8, 16, 32, 64, 96):
        for fault in (1, 32):
            inputs = dict(events=event | 1, fault1=fault, healthy=10, retry=13, request=0)
            replay = RecoveryReplay(words, **inputs)
            replay.execute()
            if event & 64:
                assert replay.mem[MODE] == 5 and replay.mem[REASON] == 10
                assert replay.mem[FAULT1] == fault
            elif event & 32:
                assert replay.mem[MODE] == 4 and replay.mem[FAULT1] == 0
                assert replay.mem[HEALTHY] == 1 and replay.mem[RETRY] == 0
            else:
                assert replay.mem[MODE] == 4 and replay.mem[FAULT1] == fault
            assert replay.mem[RUN] == 0 and not replay.start_writes()
            record("event_priority", inputs, replay)

    for fault2 in (0, 1, 2, 16, 32, 0xffff):
        replay = RecoveryReplay(words, fault2=fault2, healthy=199, request=0)
        replay.execute()
        assert replay.mem[MODE] == 1
        replay.execute()  # Actual changed-mode dispatcher and mode1 initialization.
        for _ in range(100):
            replay.execute()
        assert replay.mem[MODE] == 1 and replay.mem[COUNTER] == 100 and replay.mem[RUN] == 0
        replay.queue(8)
        replay.execute()
        accepted = not bool(fault2 & ((1 << 1) | (1 << 4) | (1 << 5)))
        assert replay.mem[MODE] == (2 if accepted else 1)
        assert replay.mem[RUN] == int(accepted)
        assert bool(replay.start_writes()) == accepted
        record("second_fault_word_eligibility", {"fault2": fault2}, replay)

    for name in ("fault_to_eligible_requires_fresh_start", "blocking_fault_waits_for_clear",
                 "healthy_gap_resets_retry_count", "fault_restarts_healthy_count",
                 "internal_clear_event_without_periodic_gate"):
        replay = RecoveryReplay(words, request=0)
        stages = []

        def stage(label, count=1, **changes):
            for address, key in ((FAULT1, "fault1"), (PERIODIC, "periodic")):
                if key in changes:
                    replay.mem[address] = changes[key] << 6 if key == "periodic" else changes[key]
            if "events" in changes:
                replay.queue(changes["events"])
            for _ in range(count):
                assert replay.mem[MODE] in (1, 4, 6)
                replay.execute()
            state = replay.snapshot()
            state.pop("writes")
            stages.append({"stage": label, "invocations": count, "after": state})

        if name == "fault_to_eligible_requires_fresh_start":
            replay.mem[MODE] = replay.mem[LAST_MODE] = 1
            replay.mem[RUN] = 1
            stage("mode1_fault_exit", fault1=1)
            assert replay.mem[MODE] == 4 and replay.mem[RUN] == 0
            stage("mode4_entry_discards_early_start", events=8)
            assert replay.mem[HEALTHY] == replay.mem[RETRY] == 0
            stage("299_nonblocking_fault_observations", 299)
            assert replay.mem[FAULT1] == 1 and replay.mem[RETRY] == 299
            stage("300th_observation_clears_stored_fault")
            assert replay.mem[FAULT1] == 0 and replay.mem[MODE] == 4
            stage("199_healthy_observations", 199, events=8)
            assert replay.mem[MODE] == 4 and replay.mem[RUN] == 0
            stage("200th_healthy_observation_returns_mode1", events=8)
            assert replay.mem[MODE] == 1 and replay.mem[RUN] == 0
            stage("mode1_entry_discards_queued_start", events=8)
            assert replay.mem[RUN] == 0
            stage("startup_counter_ready_without_request", 100)
            assert replay.mem[MODE] == 1 and replay.mem[COUNTER] == 100
            stage("fresh_start_after_mode1_entry", events=8)
            assert replay.mem[MODE] == 2 and len(replay.start_writes()) == 2
        elif name == "blocking_fault_waits_for_clear":
            stage("400_blocked_observations", 400, fault1=32)
            assert replay.mem[MODE] == 4 and replay.mem[FAULT1] == 32 and replay.mem[RETRY] == 0
            stage("fault_producer_now_clear_199_observations", 199, fault1=0)
            assert replay.mem[MODE] == 4
            stage("200th_healthy_returns_to_mode1")
            assert replay.mem[MODE] == 1 and replay.mem[RUN] == 0
        elif name == "healthy_gap_resets_retry_count":
            stage("100_fault_observations", 100, fault1=1)
            stage("20_healthy_observations", 20, fault1=0)
            assert replay.mem[HEALTHY] == 20 and replay.mem[RETRY] == 0
            stage("299_further_fault_observations", 299, fault1=1)
            assert replay.mem[HEALTHY] == 0 and replay.mem[RETRY] == 299
            stage("300th_consecutive_fault_observation_clears")
            assert replay.mem[FAULT1] == 0 and replay.mem[MODE] == 4
        elif name == "fault_restarts_healthy_count":
            stage("199_healthy_observations", 199)
            stage("one_fault_observation", fault1=1)
            assert replay.mem[HEALTHY] == 0 and replay.mem[RETRY] == 1
            stage("199_new_healthy_observations", 199, fault1=0)
            assert replay.mem[MODE] == 4
            stage("200th_new_healthy_returns_mode1")
            assert replay.mem[MODE] == 1
        else:
            stage("100_fault_observations", 100, fault1=1)
            stage("internal_clear_event_clears_even_blocking_fault", fault1=32, events=32, periodic=0)
            assert replay.mem[FAULT1] == 0 and replay.mem[HEALTHY] == 0 and replay.mem[RETRY] == 100
            stage("next_healthy_periodic_observation_resets_retry", periodic=1)
            assert replay.mem[HEALTHY] == 1 and replay.mem[RETRY] == 0
            stage("299_new_fault_observations", 299, fault1=1)
            assert replay.mem[RETRY] == 299
            stage("next_observation_clears_again")
            assert replay.mem[FAULT1] == 0
        results["recovery_sequences"].append({"case": name, "stages": stages,
                                              "start_write_intents": replay.start_writes()})

    counts = {key: len(rows) for key, rows in results.items()}
    assert counts == {"dispatcher_entry": 16, "healthy_threshold": 16, "periodic_event_gates": 4,
                      "timed_fault_clear": 16, "event_priority": 10,
                      "second_fault_word_eligibility": 6, "recovery_sequences": 5}
    ROOT.mkdir(parents=True, exist_ok=True)
    output = ROOT / "pv-mode4-recovery-results.json"
    output.write_text(json.dumps(results, indent=2) + "\n")
    output.chmod(0o600)
    sources = (Path(__file__).name, "emulate_pv_start_continuation.py", "emulate_pv_retry_actuation.py",
               "emulate_pv_retry_origins.py", "emulate_general_settings.py", "emulate_additional_features.py",
               "emulate_clock_semantics.py", "replay_io.py", "extract_dsp.py")
    manifest = {"hardware_access": False, "network_access": False, "case_counts": counts,
                "firmware": {DSP_NAME: DSP_HASH}, "function_return_substitutions": [],
                "runtime": {"python": platform.python_version(), "unicorn_import_version": unicorn.__version__},
                "result_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in sources},
                "executed": ["mode dispatcher0874d5", "mode4/6 handler08736b",
                             "event-bank read/clear0898ed/089909", "fault clear089233",
                             "real fault and periodic getters", "mode1 entry/continuation087439",
                             "cleanup08730a and real callees"],
                "synthetic_boundaries": ["RAM and MMIO values", "event injection",
                                         "periodic flag observations", "fault producer state changes",
                                         "abstract register-save/call stacks"],
                "excluded": ["UART", "main MCU policy", "full physical fault producers", "real time",
                             "interrupt or peripheral effects", "mode2 regulation", "physical charging"]}
    output = ROOT / "pv-mode4-recovery-manifest.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    output.chmod(0o600)
    print(json.dumps({"cases": sum(counts.values()), "groups": counts, "hardware_access": False}))


if __name__ == "__main__":
    main()
