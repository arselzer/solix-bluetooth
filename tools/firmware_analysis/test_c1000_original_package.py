"""Offline corruption fixtures using the published original C1000 package."""

import hashlib
import struct
import unittest

from extract_c1000_original import DEFAULT_INPUT, SHA256, inspect


class OriginalPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = DEFAULT_INPUT.read_bytes()

    def test_exact_vendor_input_and_all_component_checks(self):
        self.assertEqual(hashlib.sha256(self.package).hexdigest(), SHA256)
        manifest, images = inspect(self.package)
        self.assertEqual(len(images), 5)
        self.assertEqual(sum(x.get("dsp_block_count", 0) for x in manifest["files"]), 158)
        self.assertEqual(manifest["package"]["uninterpreted_trailer_bytes"], 68)

    def test_truncation_rejected(self):
        with self.assertRaisesRegex(ValueError, "length"):
            inspect(self.package[:-1])

    def test_wrong_model_rejected(self):
        data = bytearray(self.package)
        data[0x3f4] = ord("3")
        with self.assertRaisesRegex(ValueError, "product"):
            inspect(bytes(data))

    def test_overlapping_extent_rejected(self):
        data = bytearray(self.package)
        struct.pack_into("<I", data, 44, 0x400)
        with self.assertRaisesRegex(ValueError, "extent"):
            inspect(bytes(data))

    def test_encoded_payload_corruption_rejected(self):
        data = bytearray(self.package)
        data[0x500] ^= 1
        with self.assertRaisesRegex(ValueError, "byte sum"):
            inspect(bytes(data))

    def test_main_corruption_rejected_even_with_repaired_outer_sum(self):
        data = bytearray(self.package)
        data[0x500] ^= 1
        struct.pack_into("<I", data, 4, sum(data[0x400:0x66000]) & 0xffffffff)
        with self.assertRaisesRegex(ValueError, "MainMcu: component checksum"):
            inspect(bytes(data))

    def test_dsp_corruption_rejected_even_with_repaired_outer_sum(self):
        data = bytearray(self.package)
        data[0x27000 + 0x3000] ^= 1
        struct.pack_into("<I", data, 4, sum(data[0x400:0x66000]) & 0xffffffff)
        with self.assertRaisesRegex(ValueError, "DSP block CRC"):
            inspect(bytes(data))


if __name__ == "__main__":
    unittest.main()
