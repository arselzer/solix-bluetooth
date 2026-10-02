# Protected loader strings and the DEX recovery boundary

## Result and scope

An offline follow-up to [UPX loader recovery](android-loader-carriers.md)
decodes selected obfuscated strings through **68 native initialization routines**:
49 in `libexec.so` and 19 in `libijmDataEncryption_arm64.so`. These routines
perform local data operations without calls to Android, JNI, libc, a device
or a network service. Other initializers and the loader's JNI methods are excluded.

The decoded strings identify protected asset names, Java class-loader signatures
and the static registration of `DETool.dowork`. This gives concrete locations
for subsequent SDK recovery. It does **not** recover Java SDK classes, Anker's
action serializer, decrypted DEX buffers or a charging-disable packet.
No running-app behavior or station firmware equivalence is established.

## Inputs and initialization selection

The exact base APK hash remains
`27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110`.
Recovered input snapshots from the earlier UPX-only tool are:

| Private virtual image | Bytes | SHA-256 |
| --- | ---: | --- |
| `libexec.so.virtual-memory.bin` | 1,160,588 | `b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57` |
| `libijmDataEncryption_arm64.so.virtual-memory.bin` | 1,171,816 | `72ab38e97a6f5eaaada32eb82b82ec5224a5da90532f9d5f4c67988c46d00c3e` |

These are private virtual images, not executable SDK packages. The new tool
checks hashes, regenerates init-array targets from dynamic relocation metadata,
and obtains exact function ranges from the exception-frame table. It does not
trust the pilot's editable function inventory.

`libexec.so` has 70 populated init-array slots and 63 unique targets. Of those,
49 pass the bounded selection. The data-encryption library has 23 populated
targets, of which 19 pass. Functions with direct calls, indirect calls, system
calls or direct branches leaving their own range are excluded. Remaining
indirect branches must stay inside the selected function during replay.
Selection and case counts do not establish that all required runtime
initialization has occurred.

## Selected decoded references

| Library / virtual address | Selected value | What it establishes |
| --- | --- | --- |
| `libexec` / `00103898` | `ijiami.ajm` | A decoded protected-container name |
| `libexec` / `001038a8` | `ijiami.dat` | A decoded protected-container name |
| `libexec` / `001038b8` | `IJMDal.Data` | A decoded protected-container name |
| `libexec` / `00100590` | `dalvik/system/InMemoryDexClassLoader` | A class-loader reference |
| `libexec` / `001005c0` | `(Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V` | A single-buffer constructor signature |
| `libexec` / `001094c0` | `([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V` | A multiple-buffer constructor signature |
| Data-encryption library / `000b4070` | `com/ijm/dataencryption/DETool` | A decoded JNI class reference |

These are selected strings and addresses from one pinned build. Their presence
does not prove which branch the running app selects, which asset contains the
Anker SDK or when a plaintext buffer exists. A narrow adjacent `ADRP`/`ADD`
search found no references for the selected in-memory-loader anchors; other
loads, descriptor tables and interpreter paths remain open. This is not an
all-reference or absence claim.

The readable base DEX separately contains the dotted `DETool` class name and
`getDeclaredMethod`, but no exact `dowork` string or method-ID reference to
that class. Reflection or another protected caller remains possible; no
direct clear-DEX call sequence was recovered by this check.

## A concrete JNI method boundary

After the selected string initializers, the static `JNINativeMethod` row at
**`000b4008`** resolves to:

```text
class: com/ijm/dataencryption/DETool
method: dowork
signature: (Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;Z)Z
function: 000234b8
```

Static inspection of `JNI_OnLoad` corroborates the class reference and the
one-entry registration table. JNI registration itself was not executed.
The `dowork` body spans **12,772 bytes**, with **98 direct calls** and
**6 indirect calls** in the selected disassembly. It is outside the pure replay
and has not been run. Its name and signature do not establish argument meanings,
an encryption key, a container decoder or a safe standalone invocation.

The examined action/property literals remain absent after these selected
initializers. This bounded negative result does not exclude protected Java
bytecode, generated strings or another runtime decoder containing them.

## Reproduce with private inputs

First use `inspect_android_loader_carriers.py --private-images-dir` as documented
in the [loader recovery note](android-loader-carriers.md). Then run:

```sh
python3 tools/firmware_analysis/inspect_android_loader_strings.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/recovered-virtual-images \
  --output-dir /tmp/android-loader-strings
```

Compare complete `android-loader-strings-{results,manifest}.json` files with
`tools/firmware_analysis/expected_results/`. Default output exposes only
selected metadata, fixed string values, hashes and synthetic execution results.
Raw decoded images, arbitrary strings and private pilot notes stay ignored.
Per-case write metadata uses merged `[offset, length]` ranges (excluding
`offset + length`), plus the count and SHA-256 of the ordered write events.
Event hashing uses a little-endian 64-bit image offset followed by a 32-bit
write size.
These **68 cases** are separate from the prior 3 UPX / 256 protector cases
and the combined 1,842-case firmware runner.

Memory access is restricted to the reconstructed image regions and dedicated
stack; writes are limited to original writable segments and stack. Entire
instruction blocks must remain in the selected function. Guest system calls
and reads of unresolved imported slots are refused. CPU and stack state are
reset between cases while intended initializer globals accumulate. Failure
stops the run; partial initialization is not published as a successful result.

## Next useful evidence

The remaining offline boundary is connecting the protected asset format and
interpreter descriptors to a validated plaintext DEX decoder. A future
authorized app instrumentation session could inspect the identified method
and class-loader buffer boundaries, retaining all recovered code privately.
Neither route has recovered the actual SDK charging encoder yet.

A usable charging setter still needs its exact binary frame, capability guard,
independent device-state readback and restoration behavior. The
[Dart](gen2-dart-action-interceptors.md) and
[radio action](gen2-iot-action-firmware-boundary.md) findings remain the limits
on sending that action; this follow-up adds no station command.
