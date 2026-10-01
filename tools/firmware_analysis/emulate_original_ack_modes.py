#!/usr/bin/env python3
"""Original C1000 1.5.9 common ACK suppression; entirely offline synthetic replay.

Unlike the older command harness, execute 08007718 and its real route gate.
Only persistence and final response transport remain substituted.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path

from emulate_original_power_paths import CEILING, CTX, IMAGE, IMAGE_SHA256, PAYLOAD, PowerMachine

ROUTE_ACTIVE = 0x20000d13
ROUTE_FLAGS = 0x20000d30


class AckMachine(PowerMachine):
    def step(self, uc, address, size, data):
        if address == 0x08007718:
            # The parent harness normally substitutes this helper. Execute it.
            return
        super().step(uc, address, size, data)


def run_suite(image: bytes) -> dict:
    if not __debug__:
        raise RuntimeError("Assertions are required; do not run Python with -O")
    m = AckMachine(image)
    results = []
    for handler, route, flags, source in itertools.product(
            (0x08007718, 0x0800b908), (0, 1, 2), (0, 1, 2, 3), (0x21, 0x22)):
        m.reset()
        m.put(ROUTE_ACTIVE, route, "B")
        m.put(ROUTE_FLAGS, flags, "B")
        m.put(PAYLOAD + 2, source, "B")
        m.put(PAYLOAD + 6, 900, "H")
        m.run(handler, CTX)
        suppressed = route != 0 and bool(flags & 2)
        expected_reply = b"\x00\xa1\x01" + bytes(((source & 15) | 0x30,))
        assert m.replies == ([] if suppressed else [expected_reply])
        expected_calls = [] if handler == 0x08007718 else ["deferred_configuration_persistence"]
        if not suppressed:
            expected_calls += ["response_transport"]
        assert m.calls == expected_calls
        if handler == 0x0800b908:
            assert m.get(CEILING, "H") == 900
        results.append({"entry": f"{handler:08x}", "route_active": route,
                        "route_flags": flags, "source": source,
                        "response_suppressed": suppressed,
                        "reply_payloads": [reply.hex() for reply in m.replies],
                        "stored_watts": m.get(CEILING, "H") if handler == 0x0800b908 else None})
    return {"image_sha256": IMAGE_SHA256, "cases": len(results), "results": results,
            "scope": "Actual common ACK and 0044 instructions; synthetic transport/persistence"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=IMAGE,
                        help="Decoded original C1000 main 1.5.9 (SHA-256 checked)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_suite(args.image.read_bytes())
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed {result['cases']} synthetic cases; saved {args.output}")


if __name__ == "__main__":
    main()
