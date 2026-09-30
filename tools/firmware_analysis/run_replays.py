"""Reproduce the synthetic suites and compare their published results."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("firmware-analysis-results"),
                        help="Directory for synthetic replay results and provenance manifest")
    args = parser.parse_args()
    os.umask(0o077)
    os.environ["SOLIX_ANALYSIS_OUTPUT"] = str(args.output.expanduser().resolve())

    import unicorn
    from replay_io import FIRMWARE_NAME, FIRMWARE_SIZE, FIRMWARE_SHA256, FIRMWARE_LOAD_ADDRESS, ROOT, firmware_image
    from emulate_general_settings import main as settings
    from emulate_offgrid_alert import main as alert
    from emulate_charging_followup import main as charging
    from emulate_feature_candidates import main as features
    from emulate_additional_features import main as additional
    from emulate_device_timeout import main as timeout

    firmware_image()  # Refuse an absent or mismatched image before any emulation.
    suites = (
        (settings, "general-settings-emulation-results.json", 1063),
        (alert, "offgrid-alert-emulation-results.json", 20),
        (charging, "charging-followup-results.json", 245),
        (features, "feature-candidates-results.json", 212),
        (additional, "additional-features-results.json", 147),
        (timeout, "device-timeout-results.json", 155),
    )
    package = Path(__file__).resolve().parent
    results = {}
    for run, filename, expected_count in suites:
        run()
        path = ROOT / filename
        actual = json.loads(path.read_text())
        expected = json.loads((package / "expected_results" / filename).read_text())
        count = len(actual) if isinstance(actual, list) else sum(len(value) for value in actual.values())
        if actual != expected or count != expected_count:
            raise RuntimeError("Replay differs from published synthetic results: " + filename)
        results[filename] = {"cases": count, "sha256": digest(path), "matches_expected": True}

    manifest = {
        "hardware_access": False,
        "firmware": {"filename": FIRMWARE_NAME, "bytes": FIRMWARE_SIZE,
                     "sha256": FIRMWARE_SHA256, "load_address": hex(FIRMWARE_LOAD_ADDRESS)},
        "runtime": {"python": platform.python_version(), "unicorn": unicorn.__version__,
                    "system": platform.system(), "architecture": platform.machine()},
        "results": results,
        "source_sha256": {path.name: digest(path) for path in sorted(package.glob("*.py"))},
    }
    path = ROOT / "reproduction-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"total_cases": sum(value["cases"] for value in results.values()),
                      "all_results_match": True, "hardware_access": False}))


if __name__ == "__main__":
    main()
