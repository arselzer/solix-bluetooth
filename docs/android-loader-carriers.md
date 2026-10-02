# Android protected loaders: bounded offline recovery

## What changed

The retained Android app has three **UPX-packed loader libraries**, in addition
to the clear loader DEX and native crypto libraries described in the
[SDK boundary inventory](android-sdk-native-boundaries.md). Replaying their
unpacking stubs in isolated ARM64 emulated memory recovers readable native
instructions. It does **not** yet recover the protected Java SDK, its
`action_set_ac_params` encoder, OTA envelope or station capability response.

This investigation dated **2026-10-02** used no phone, station, account or
network connection. No JNI function, Android loader, class initialization or
station control ran. A separate bounded replay covers the protector's pure
byte-remapping function; that distinction matters when counting executed code.
App binaries and recovered virtual images remain private.

## Exact inputs and recovered boundaries

The accepted base APK SHA-256 is
`27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110`.
The public tool verifies this hash and every selected entry before inspection.

| APK entry | Packed bytes | UPX entry → stopped before original entry | Selected readable export |
| --- | ---: | --- | --- |
| `assets/ijm_lib/arm64-v8a/libexec.so` | 791,584 | `000985a0` → `00072d80` | `JNI_OnLoad` at `00072e74`, 120 bytes |
| `assets/ijm_lib/arm64-v8a/libexecmain.so` | 35,880 | `00003f80` → `0000ef38` | `getOpCode` at `00001f5c`, 4,240 bytes |
| `assets/libijmDataEncryption_arm64.so` | 450,720 | `00062b68` → `0001de74` | `JNI_OnLoad` at `0001de7c`, 1,936 bytes |

The respective packed library hashes are:

```text
4fd13d12d64e6d451f5b567c173917225c1b1e2deb97abdeca10110b8dc4f379
82d35cb6e2e5f1918e5ef53aae83590f7257cbec29f60357155060eb883eafd9
3dd7349af2d0ca697e7ab30a673231fa9ec4fd0b1cf13c3ef577cd7355c05032
```

The section table of `libexec.so` contains a malformed symbol-table size.
The tool reads program headers, `PT_LOAD`, `PT_DYNAMIC` and the dynamic hash
table directly, avoiding section-table assumptions. Stored export addresses
alone were insufficient: their original bytes were packed data. Full selected
function disassembly succeeds only after unpacking.

These outputs are **virtual-memory snapshots**, not rebuilt ELF files. Dynamic
linker relocations, imported functions, constructors, JNI registration and the
protector's runtime state are not established by the snapshot.

## Remaining protected containers and an empty DEX

| Entry | Bytes | Entropy, bits/byte | SHA-256 |
| --- | ---: | ---: | --- |
| `assets/ijiami.dat` | 8,847,278 | 7.9837 | `56cb2bd0d8c6eea2aeced1b274c006be6b852309b5e267daa53a50641e43eb1c` |
| `assets/ijiami.ajm` | 7,176,288 | 7.9938 | `ea68ea4fa5981f12e89651f50d637f5d4c80e24fe1700b41c11c2a02cc055960` |
| `assets/IJMDal.Data` | 32,016 | 7.9951 | `358ac3092eaa68058739c497ff6cbb27d2cc54e3e92bddb1599fa57e96f5fa31` |

These three selected containers have no examined DEX magic, ZIP local-header
magic or exact charging-action/property literals. High entropy does not identify
an encryption or compression algorithm, prove which container holds the SDK,
or establish that identifiers are absent after runtime decoding.

The DEX signature at packed `libexec.so` file offset **723854** is a real
**156-byte DEX 035**, with valid SHA-1 and Adler-32. Its string, type, prototype,
field, method and class counts are all zero. It is an empty DEX, not the missing
SDK implementation. The result fixture retains its hash and header checks.

## The native protector has another interpreter boundary

Readable `libexec.so.JNI_OnLoad` calls `000e2d48`, pushes its JNI arguments
through `000e2e10`, invokes `000e2e00` and extracts a return value through
`000e7fa0`. Static review shows stack operations and an indirect interpreter
dispatch through `000bd624`; the selected path supplies descriptor addresses
`001135b0`, `001135d0`, `001135f0` and `00113790`. No recovered JNI body was run.

Private static call-site inspection also resolves Android asset imports and
read helpers around `00055218`, `00055314` and `00055718`. Their asset names
come from arguments; this does not establish a complete container decoder or
a specific SDK class location. Exact notes and virtual images are retained
locally for subsequent analysis.

Separately, `libexecmain.so.getOpCode` was replayed for **all 256 byte inputs**,
with no guest system calls or calls outside that pinned function. Its outputs
form a 256-value permutation with SHA-256
`de4520c22fedb26d19ce80c61621bc202fe18f68a76b20957e7ec939ef28b436`.
This is the **protector's bytecode remapping**, not MQTT/BLE station opcodes
and not a newly discovered set of 256 battery controls.

## Reproduction and execution limits

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/inspect_android_loader_carriers.py \
  --base-apk /private/path/base.apk \
  --output-dir /tmp/android-loader-carriers
```

Compare both `android-loader-carriers-{results,manifest}.json` files with
`tools/firmware_analysis/expected_results/`. These are **3 unpacker replays
plus 256 protector mapping cases**, separate from the 1,842-case firmware runner.
Static metadata adds no further CPU cases.

The unpacker allows only pinned original/copied code regions and an exactly
validated 12-byte final trampoline, checking entire translated block ranges.
Guest `mmap`, `mprotect` and `munmap` are memory-only substitutes; every other
guest system call is refused. Heap execution is revoked on guest unmap. Each
run has a 20-million-instruction and 20-second limit, and an unexpected handoff
fails. The pure mapping function has its own 1,000-instruction per-input limit.
No guest system call is forwarded to the host.

Optional `--private-images-dir /private/local/directory` saves recovered virtual
images with file mode `600`; the directory must have mode `700`. Keep these
images in ignored local storage. Default output contains only selected metadata,
hashes, literal-presence booleans and synthetic execution results.

## Next useful evidence

The [Dart interceptor audit](gen2-dart-action-interceptors.md) establishes that
the AC-input-disable parameter crosses Flutter IPC unchanged, with optional
cached owner metadata. The [readable asset audit](android-readable-sdk-assets.md)
finds another native bridge but no station packet serializer. The
[firmware action audit](gen2-iot-action-firmware-boundary.md) confirms that
normal MQTT admission requires binary frames and that arbitrary action JSON
can be misinterpreted as a controller command.

The next offline lead is mapping the recovered interpreter's descriptors and
asset decoder to protected SDK classes. An actual action encoder must still
identify function, opcode, field type, capability guard and independent readback
before input-disable control can be implemented or tested. No new charging
setter, recovery action or account-free claim follows from unpacking alone.
