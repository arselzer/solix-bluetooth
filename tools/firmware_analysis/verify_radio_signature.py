"""Verify the published vendor radio image and tamper cases offline."""
import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path
from replay_io import REPOSITORY_FIRMWARE, ROOT
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-directory', type=Path, default=REPOSITORY_FIRMWARE)
    args = parser.parse_args()
    root = ROOT; root.mkdir(parents=True, exist_ok=True)
    image = (args.firmware_directory/'c1000-radio-validated.bin').read_bytes()
    if len(image) != 1482800 or hashlib.sha256(image).hexdigest() != 'e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8':
        raise ValueError('Radio input size/hash mismatch')
    if not __debug__:
        raise RuntimeError('Assertions are required; do not use Python -O')
    offset = 24
    for _ in range(image[1]):
        _, length = struct.unpack_from('<II', image, offset)
        offset += 8 + length
    signature_offset = (offset+4095) & ~4095
    block = image[signature_offset:signature_offset+1216]
    assert block[:4] == b'\xe7\x02\x00\x00'
    assert zlib.crc32(block[:1196]) == int.from_bytes(block[1196:1200], 'little')
    digest = hashlib.sha256(image[:signature_offset]).digest()
    assert digest == block[4:36]
    modulus = int.from_bytes(block[36:420], 'little')
    exponent = int.from_bytes(block[420:424], 'little')
    key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
    signature = block[812:1196][::-1]
    cases = [('original', digest, signature, True),
             ('modified_digest', bytes([digest[0]^1])+digest[1:], signature, False),
             ('modified_signature', digest, bytes([signature[0]^1])+signature[1:], False)]
    results = []
    for name, sample_digest, sample_signature, expected in cases:
        try:
            key.verify(sample_signature, sample_digest,
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                       utils.Prehashed(hashes.SHA256()))
            valid = True
        except InvalidSignature:
            valid = False
        assert valid == expected
        results.append({'case': name, 'valid': valid})
    result = {'image_bytes': len(image), 'signature_block_offset': hex(signature_offset),
              'block_magic': hex(block[0]), 'block_version': block[1],
              'rsa_bits': modulus.bit_length(), 'exponent': exponent,
              'crc32_valid': True, 'image_sha256_matches_block': True,
              'rsa_pss_signature_valid': True,
              'other_signature_blocks_empty': image[signature_offset+1216:signature_offset+3648] == b'\xff'*2432,
              'tail_after_signature_sector_bytes': len(image)-signature_offset-4096,
              'cases': results}
    path = root/'firmware-signature-results.json'
    path.write_text(json.dumps(result, indent=2)+'\n')
    path.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
