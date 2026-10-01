#!/usr/bin/env python3
"""Show Gen 2 backup configurations that actual firmware D9 cannot distinguish.

Synthetic offline inputs only. Uses the real 1.1.4.9 selector, serializer and
005e handler through the existing disaster-plan replay harness.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path


def run_suite():
    from emulate_disaster_plan import (
        AUTOMATIC, AUTO_SWITCH, DisasterMachine, MANUAL, MANUAL_SWITCH, NOW,
    )
    from replay_io import FIRMWARE_SHA256, firmware_image

    firmware_image()
    pairs = []
    # This entire 38-byte block is persistent configuration, not active cache.
    def saved(machine):
        return bytes(machine.uc.mem_read(AUTOMATIC, 38))

    def compare(name, left, right):
        a, b = saved(left), saved(right)
        assert a != b
        first, second = left.d9(), right.d9()
        assert first == second
        pairs.append({"case": name, "d9": first.hex(),
                      "configuration_hashes": [hashlib.sha256(x).hexdigest() for x in (a, b)],
                      "active": [left.active(), right.active()]})

    empty, future = DisasterMachine(), DisasterMachine()
    empty.uc.mem_write(AUTO_SWITCH, b"\x01")
    future.seed(kind=2, start=NOW + 3600, end=NOW + 7200)
    compare("empty_vs_enabled_future_automatic", empty, future)

    first, second = DisasterMachine(), DisasterMachine()
    first.seed(kind=2, index=0, start=NOW + 3600, end=NOW + 7200)
    second.seed(kind=2, index=2, start=NOW + 10800, end=NOW + 14400)
    compare("different_dormant_automatic_windows_and_slots", first, second)

    for kind in (1, 2):
        for active in (False, True):
            first, second = DisasterMachine(), DisasterMachine()
            start = NOW - 10 if active else NOW + 3600
            first.seed(kind=kind, start=start, end=NOW + 7200, maximum=60)
            second.seed(kind=kind, start=start, end=NOW + 7200, maximum=100)
            compare(f"kind_{kind}_active_{active}_different_maximum", first, second)

    cancellations = []
    for enabled in (0, 1):
        for name, start, end in (("current", NOW-10, NOW+10),
                                  ("future", NOW+100, NOW+200),
                                  ("expired", NOW-200, NOW-100),
                                  ("exact_start", NOW, NOW+10),
                                  ("exact_end", NOW-10, NOW)):
            cancel = DisasterMachine()
            cancel.seed(kind=1, start=NOW+3600, end=NOW+7200)
            cancel.seed(kind=2, start=start, end=end, switch=enabled)
            before, active_before = saved(cancel), cancel.active()
            assert cancel.disaster(option=0, switch=0) == 1
            after = saved(cancel)
            erased = start <= NOW < end
            assert (before[:9] != after[:9]) == erased
            if erased:
                assert after[:9] == b"\x64" + b"\xff"*8
            assert cancel.uc.mem_read(MANUAL_SWITCH, 1) == b"\x00"
            assert cancel.uc.mem_read(AUTO_SWITCH, 1) == bytes((enabled,))
            manual = slice(MANUAL-AUTOMATIC, MANUAL-AUTOMATIC+9)
            assert before[manual] == after[manual]
            cancellations.append({"window": name, "automatic_enabled": bool(enabled),
                                  "active_kind_before": active_before["kind"],
                                  "automatic_record_invalidated": erased,
                                  "manual_record_preserved": True})
    return {"image_sha256": FIRMWARE_SHA256, "indistinguishable_pairs": pairs,
            "manual_disable": cancellations,
            "cases": len(pairs) + len(cancellations),
            "scope": "Synthetic RAM; physical RTC, persistence and electrical behavior not tested"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path,
                        help="Directory containing decoded C1000 Gen 2 1.1.4.9 main image")
    parser.add_argument("--output", type=Path, required=True, help="Synthetic JSON results")
    args = parser.parse_args()
    if args.firmware_dir:
        os.environ["SOLIX_FIRMWARE_DIR"] = str(args.firmware_dir)
    os.environ["SOLIX_ANALYSIS_OUTPUT"] = str(args.output.resolve().parent)
    result = run_suite()
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
