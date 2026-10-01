"""Offline A1763 saved-frequency and Smart-mode getters/A4 serialization.

Runs the existing real ARM getters and A4 serializer against synthetic RAM.
No command handler, transport, persistence operation or output policy runs.
Inherited memory/timer substitutes are described in the reproduction guide.
"""

import hashlib
import json
import os
from pathlib import Path

from emulate_general_settings import SETTINGS, SettingsMachine
from replay_io import FIRMWARE_NAME, FIRMWARE_SHA256, ROOT, firmware_image

os.umask(0o077)


def main():
    firmware_image()
    rows = []
    for frequency in (0, 49, 50, 60, 61, 255):
        for ac_saving in (0, 1):
            for dc_saving in (0, 1):
                machine = SettingsMachine()
                baseline_a4 = bytearray(machine.a4())
                machine.uc.mem_write(SETTINGS + 0x25, bytes((frequency,)))
                machine.uc.mem_write(SETTINGS + 0x1f, bytes((ac_saving,)))
                machine.uc.mem_write(SETTINGS + 0x20, bytes((dc_saving,)))
                settings = bytes(machine.uc.mem_read(SETTINGS, 0x190))
                outputs = bytes(machine.uc.mem_read(0x20000164, 4))
                assert machine.run(0x0801a580, 0) == frequency
                assert machine.run(0x0801a5ac, 0) == ac_saving
                assert machine.run(0x0801a5dc, 0) == dc_saving
                baseline_a4[7] = frequency
                baseline_a4[8] = ac_saving
                baseline_a4[13] = dc_saving
                assert machine.a4() == bytes(baseline_a4)
                assert machine.uc.mem_read(SETTINGS, 0x190) == settings
                assert machine.uc.mem_read(0x20000164, 4) == outputs
                assert machine.acked == machine.scheduled == 0
                assert not machine.calls and not machine.refresh
                rows.append({
                    "frequency_raw": frequency,
                    "frequency_supported_by_loader": frequency in (50, 60),
                    "ac_saving": ac_saving, "dc_saving": dc_saving,
                    "a4_offsets": [7, 8, 13],
                    "settings_and_output_flags_preserved": True,
                    "command_or_output_policy_executed": False,
                })
    result = {"firmware": FIRMWARE_NAME, "sha256": FIRMWARE_SHA256,
              "cases": len(rows), "hardware_access": False, "results": rows}
    path = ROOT / "gen2-preference-readback-results.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    path.chmod(0o600)
    names = (Path(__file__).name, "emulate_general_settings.py", "emulate_clock_semantics.py", "replay_io.py")
    manifest = {"firmware_sha256": FIRMWARE_SHA256, "cases": len(rows), "hardware_access": False,
                "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in names},
                "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest_path = ROOT / "gen2-preference-readback-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    print(json.dumps({"cases": len(rows), "results_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "hardware_access": False, "output": str(path)}))


if __name__ == "__main__":
    main()
