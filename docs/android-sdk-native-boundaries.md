# Android SDK: retained bytecode and native crypto boundaries

Static inspection dated **2026-10-02** extends the
[OTA HTTP-wrapper audit](c1000-ota-http-wrapper.md) and the app's normal SDK
action investigation. No APK/native code, phone connection, cloud request or
device command was executed. APKs and extracted libraries remain private.

A subsequent [protected-loader investigation](android-loader-carriers.md)
replays three UPX stubs and a pure protector byte mapper in emulated memory.
That separate tool recovers readable loader instructions, while SDK classes,
OTA envelopes and the charging action encoder remain unresolved. The static
inventory described here does not execute those instructions.

## The clear APK does not contain the action implementation

| Retained input | SHA-256 |
| --- | --- |
| `base.apk` | `27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110` |
| ARM64 split | `373aefb48f1384b6065000cfab7ab4b25c9969b441d2547788f8e1807bbed601` |

The base APK has one clear `classes.dex`: **13,892 bytes**, 193 strings,
DEX version 038. Its anchors include the Anker application class,
`com.ijm.dataencryption.DETool`, class-loader instantiation and native library
loading. Retained assets include `IJMDal.Data`, InteGration version metadata,
`libexec.so`, `libexecmain.so` and IJM encryption libraries. This is evidence
of a loader/protected-bytecode boundary; it does not establish how the original
SDK classes can be recovered.

The clear DEX string inventory lacks `akiot.cloud_api`,
`akiot.device.invoke_action`, `action_set_ac_params` and
`acInputDisableSwitch`. Their presence in the separately examined Flutter
AOT image does not recover the native action dispatcher or HTTP-envelope
builder. The inventory is not a claim that these names are absent from all
encrypted assets or runtime memory.

## Crypto entry points are available, but do not supply a protocol mapping

The ARM64 split contains `libcrypto-security.so` (SHA-256
`ad6e86ebe54341673a6180f9d2d124b47a401262e66d3148f059596854ec0ba3`)
and `libecc-encryption.so` (SHA-256
`20fba95954b6a6c24142a182b00b7d127c6b1677ee16c71dd8b5c4322ca74aea`).
Their exported JNI package is `com.anker.esiotkit.support.crypto`.

| Examined exported entry | Actual selected call boundary |
| --- | --- |
| `Cryptography.aesDecrypt` at `0015c888` | `aes_decrypt` at `00105218`; hardcoded 128-bit key setup and `AES_ecb_encrypt` with decrypt flag 0 |
| `Cryptography.cryptoHkdf` at `0015d0d8` | `crypto_hkdf` at `00105c78`; mbedTLS HMAC extract/expand calls |
| `Cryptography.computeEcdh` at `0015ced0` | `compute_ecdh` at `00105754`; OpenSSL point/private-key parsing and ECDH boundary |
| `Cryptography.genEcdhKey` at `0015cfac` | `ecdh_gen`, key generation and hex conversion |
| `Cryptography.genCheckCode` at `0015c990` | `gen_pic_code_v1`, then picture/check-code helpers; not an established OTA signature routine |
| `EccEncryption.eccEncrypt/eccDecrypt` | Separate `EncryptECC` / `DecryptECC` routines |

The HKDF selector table at `000ba04c` / relocated pointers `002763d8` maps
**0 → SHA-1/20 bytes, 1 → SHA-224/28, 2 → SHA-256/32**. These table entries are
resolved against actual dynamic symbol relocations, not inferred from a
generic “HKDF” name. No OTA caller's selected algorithm/arguments were found.

This inspection does not replay crypto primitives or validate their full error,
padding, memory or input-length behavior. No key, salt, nonce, signing input,
HTTP body or raw device frame is recovered by these call names. In particular,
AES-ECB at one entry is not evidence that the logged-in OTA request uses ECB.
Do not implement an OTA envelope or charging setter from this inventory alone.

## Reproduce without publishing the app

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/inspect_android_sdk_boundary.py \
  --base-apk /private/path/base.apk \
  --arm64-apk /private/path/arm64.apk \
  --output-dir /tmp/android-sdk-boundary
```

The tool verifies both exact APK hashes before parsing, checks the extracted
library hashes, and exports selected metadata/call targets with no DEX string
dump or asset contents. Compare both `android-sdk-boundary-*.json` files with
`tools/firmware_analysis/expected_results/`. Results are deterministic static
inspection, **not additional firmware emulation cases**. The practical OTA
lead remains the app's plaintext success/model-parser boundary, while a raw
charging action still requires its actual frame and device readback mapping.
