"""Offline scope audit of Gen2 DCDC recovery-event origins.

Exhausts an internal 16-bit command word in a synthetic instruction replay.
It does not send commands, authenticate a public opcode or access hardware.
"""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform

import unicorn
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

from emulate_pv_retry_actuation import ActuationMachine, DspStatusReplay, DSP_NAME, DSP_HASH, dsp_words
from emulate_pv_mode4_recovery import RecoveryReplay, FAULT1, MODE, HEALTHY, RETRY
from replay_io import ROOT, FIRMWARE_NAME, FIRMWARE_SHA256

os.umask(0o077)


def event_machine(words):
    return DspStatusReplay(words, qualifier=1, mode=4, fault1=0, fault2=0, status=0)


def bank_bits(replay, bank):
    address = 0xad0c + 2 * bank
    return replay.mem.get(address, 0) | (replay.mem.get(address + 1, 0) << 16)


def main():
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    words = dsp_words()
    results = {"register0015_complete_domain": {}, "direct_event_writer_audit": [],
               "main_start_stop_builder": [], "invalid_main_selectors": [],
               "command_to_recovery_effect": [], "other_bank_bit6": []}

    counts = Counter()
    recognized = []
    for command in range(0x10000):
        replay = event_machine(words)
        replay.mem[0x419] = command
        replay.run(0x0866bf, stop_at=0x0866d0)
        bits = bank_bits(replay, 0)
        assert bits == {0x57: 8, 0x58: 16, 0x59: 32}.get(command, 0)
        assert not bits & 64
        counts[bits] += 1
        if bits:
            recognized.append({"register0015_word": command, "bank0_bits": bits})
    assert counts == {0: 65533, 8: 1, 16: 1, 32: 1}
    results["register0015_complete_domain"] = {
        "values_executed": 65536, "outcome_counts": {str(key): counts[key] for key in sorted(counts)},
        "recognized": recognized, "bank0_bit6_produced": False,
        "entry_assumes_register_pending_bit_selected": True,
        "uart_receiver_executed": False, "function_return_substitutions": [],
    }

    # Scan exact long-call words, then require an explicit bank literal in the
    # final three setup words. This describes those direct setup sites, not
    # every possible indirect call, aliased memory write or control-flow entry.
    sites = sorted(pc for pc in words if words[pc] == 0x7648 and words.get(pc + 1) == 0x98dc)
    assert len(sites) == 62
    bank0_sites = []
    for pc in sites:
        setups = [(pc - distance, words.get(pc - distance, 0)) for distance in (1, 2, 3)]
        immediate = [(address, word & 0xff) for address, word in setups if word & 0xff00 == 0x9a00]
        assert immediate, f"Direct call lacks audited bank setup: {pc:06x}"
        address, bank = immediate[0]
        assert bank in (0, 1, 3, 4)
        row = {"call_word_address": f"{pc:06x}", "bank_setup_address": f"{address:06x}",
               "bank_literal": bank, "scope": "local direct-call setup only"}
        if bank == 0:
            bank0_sites.append(pc)
            if pc == 0x0866ce:
                row["established_event_bits"] = [3, 4, 5]
            elif pc == 0x0871fc:
                row["established_event_bits"] = [3, 4]
            else:
                assert words[pc - 2] == 0x9a00 and words[pc - 1] & 0xff00 == 0x9b00
                event = words[pc - 1] & 0xff
                assert event in (1, 2, 9)
                replay = event_machine(words)
                replay.run(pc - 2, stop_at=pc + 2)
                assert bank_bits(replay, 0) == 1 << event
                row["established_event_bits"] = [event]
        results["direct_event_writer_audit"].append(row)
    assert len(bank0_sites) == 17

    # Existing main builder: selector2 means the DC-input/MPPT branch.
    # Nonzero is a boolean test here, not a raw register-value argument.
    for enabled in (0, 1, 2, 0x59, 0xffff, 0xffffffff):
        replay = ActuationMachine(active=False)
        replay.uc.reg_write(UC_ARM_REG_R1, enabled)
        replay.uc.reg_write(UC_ARM_REG_R2, 0)
        replay.run(0x0802a590, 2)
        assert replay.uc.reg_read(UC_ARM_REG_R0) == 1
        assert len(replay.queued) == 1
        request = replay.queued[0]
        assert request["selector"] == 1 and request["register"] == 0x15
        assert request["payload_hex"] == ("5700" if enabled else "5800")
        results["main_start_stop_builder"].append({"internal_helper_selector": 2,
            "enable_argument": enabled, "queued": request,
            "raw_register_argument": False, "queue_is_substituted": True})
    for selector in (0, 3):
        replay = ActuationMachine(active=False)
        replay.uc.reg_write(UC_ARM_REG_R1, 0x59)
        replay.uc.reg_write(UC_ARM_REG_R2, 0)
        replay.run(0x0802a590, selector)
        assert replay.uc.reg_read(UC_ARM_REG_R0) == 0 and not replay.queued
        results["invalid_main_selectors"].append({"selector": selector, "rejected": True})

    for changed_mode in (False, True):
        producer = event_machine(words)
        producer.mem[0x419] = 0x59
        producer.run(0x0866bf, stop_at=0x0866d0)
        consumer = RecoveryReplay(words, mode=4, last_mode=1 if changed_mode else 4,
            events=bank_bits(producer, 0), periodic=0, fault1=32,
            healthy=99, retry=11, request=0)
        consumer.execute()
        assert consumer.mem[MODE] == 4 and not consumer.start_writes()
        assert consumer.mem[FAULT1] == (32 if changed_mode else 0)
        assert consumer.mem[HEALTHY] == 0
        assert consumer.mem[RETRY] == (0 if changed_mode else 11)
        results["command_to_recovery_effect"].append({"internal_word": 0x59,
            "changed_mode_initialization": changed_mode, "after": consumer.snapshot(),
            "public_opcode_established": False})

    # A real command-consumer branch contains AH=6, but AL=1 selects bank1.
    # It therefore cannot create the bank0 event inspected by the MPPT handler.
    replay = event_machine(words)
    replay.run(0x086531, stop_at=0x086539)
    assert bank_bits(replay, 1) == 64 and bank_bits(replay, 0) == 0
    results["other_bank_bit6"].append({"entry": "086531", "bank1_bits": 64,
        "bank0_bits": 0, "function_return_substitutions": []})

    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / "pv-recovery-event-origins-results.json"
    path.write_text(json.dumps(results, indent=2) + "\n")
    path.chmod(0o600)
    sources = (Path(__file__).name, "emulate_pv_mode4_recovery.py", "emulate_pv_start_continuation.py",
               "emulate_pv_retry_actuation.py", "emulate_pv_retry_origins.py", "emulate_general_settings.py",
               "emulate_additional_features.py", "emulate_clock_semantics.py", "replay_io.py", "extract_dsp.py")
    manifest = {"hardware_access": False, "network_access": False,
        "case_counts": {"register_word_values": 65536, "direct_calls_audited": 62,
                        "fixed_bank0_setup_replays": 15, "main_builder": 6, "invalid_selectors": 2,
                        "command_to_effect": 2, "other_bank_bit6": 1},
        "firmware": {FIRMWARE_NAME: FIRMWARE_SHA256, DSP_NAME: DSP_HASH},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__},
        "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in sources},
        "dsp_function_return_substitutions": [],
        "arm_substitutions": ["allocation/free", "internal command queue", "memcpy/memset",
                              "inherited inactive LCD/logging/persistence/timer boundaries"],
        "excluded": ["UART", "radio/BLE/MQTT authentication and routing", "public opcode provenance",
                     "indirect DSP call graph", "aliased event-bank writes", "physical fault producers",
                     "concurrent scheduling", "peripheral behavior", "physical charging"],
        "conclusion": "No public fault-clear command or bank0 bit6 producer established by this bounded audit"}
    path = ROOT / "pv-recovery-event-origins-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"register_values": 65536, "direct_call_sites": 62,
                      "main_builder_cases": 8, "new_public_control": False, "hardware_access": False}))


if __name__ == "__main__":
    main()
