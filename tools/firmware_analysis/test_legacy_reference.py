"""Synthetic fixtures for the offline A1761 source audit; no external dependency."""

import unittest

from audit_legacy_reference import audit


COMMANDS = '''
BASE = {"a1": {NAME: "pattern_22"}}
POWER = BASE | {COMMAND_NAME: Commands.charge_power,
    "a2": {NAME: "power", TYPE: Types.sile.value, VALUE_MIN: 300, VALUE_MAX: 1800}}
REALTIME = BASE | {COMMAND_NAME: Commands.realtime_trigger,
    "a2": {VALUE_OPTIONS: {"off": 0, "on": 1}}}
'''
MAPPING = '''
_A1761_0405 = {"d1": {NAME: "ac_input_limit"}, "e5": {NAME: "ac_fast_charge_switch"}}
SOLIXMQTTMAP: dict = {
    "A1761": {"0044": POWER | {"a2": {**POWER["a2"], VALUE_MIN: 100, VALUE_MAX: 1000}},
              "0057": REALTIME, "0405": _A1761_0405},
    "A1763": {"0090": {COMMAND_NAME: Commands.tariff}},
}
'''


class LegacyReferenceTests(unittest.TestCase):
    def test_overrides_preserve_inherited_type_and_original_model_domain(self):
        power = audit(MAPPING, COMMANDS)["messages"]["0044"]["a2"]
        self.assertEqual((power["value_min"], power["value_max"]), (100, 1000))
        self.assertEqual(power["type"], {"expression": "Types.sile.value"})

    def test_other_models_do_not_supply_missing_commands(self):
        result = audit(MAPPING, COMMANDS)
        self.assertNotIn("0090", result["messages"])
        self.assertIn("0090", result["unmapped_here_only"])
        self.assertEqual(result["selected_0405_fields"]["e5"], "ac_fast_charge_switch")

    def test_unknown_source_calls_are_never_executed(self):
        result = audit("raise RuntimeError('must not execute')\n" + MAPPING, COMMANDS)
        self.assertEqual(result["messages"]["0057"]["command"], {"expression": "Commands.realtime_trigger"})

    def test_missing_original_model_refuses_instead_of_using_gen2(self):
        with self.assertRaises(ValueError):
            audit(MAPPING.replace('"A1761"', '"A1765"'), COMMANDS)


if __name__ == "__main__":
    unittest.main()
