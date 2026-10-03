# Protected loader record 2: JNI setup and native registration

## Result

Bounded static analysis decodes **all 182 bytes of record 2**, including
both conditional paths, rolling keys, special key-reset markers and the
terminal return. It prepares `JavaVM.GetEnv`, a runtime context callback,
and native JNI registration at **`00073864`**. The next concrete code-loader
leads are registered Application and ClassLoader methods, particularly
`al` at **`00077134..00078c30`**.

This is a static token/control-flow result. No protector VM, JNI method,
native callback, Android code, initializer, cloud service or station command
was executed. The runtime callback and Java class remain unresolved. No
plaintext SDK DEX, Anker action serializer or AC-input-disable packet was
recovered. This applies to one retained app protection build; it establishes
no station model or firmware equivalence.

## Exact inputs

Use the same base APK and carrier image as the
[preceding wrapper proof](android-loader-vm-boundary.md):

| Input | SHA-256 |
| --- | --- |
| Base APK | `27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110` |
| Packed `libexec.so` | `4fd13d12d64e6d451f5b567c173917225c1b1e2deb97abdeca10110b8dc4f379` |
| Unpacked carrier snapshot | `b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57` |
| Private snapshot after the earlier 49 pure string initializers | `14c73475f02e708a07d982b65d5fcea65792c8d477ba293ca95187f461e6e06f` |

The last snapshot supplies only seven whitelisted registration names and
signatures. Token and native-code checks use the original unpacked image
plus pure ELF relocation parsing. This investigation does not replay the
initializers. Both snapshots stay private; public fixtures contain selected
metadata and hashes. Addresses below are virtual-image offsets, and ranges
end exclusively.

## Complete bounded control flow

The table entry at `001137a0` stores offset 220 and length 182 relative to
record base `001135f0`, giving **`001136cc..00113782`**. The header occupies
four bytes; all remaining **178 bytes** are covered by **59 static token
states**, ending at record offset **181**.

The parser keeps the rolling key and last decoded opcode separately. Special
markers after branch opcodes dispatch handler `3b`, which loads a new key
from its one-byte operand. Treating every `3b` byte as an ordinary encoded
opcode, or continuing with a constant key, fails on this record.

| Record offsets | Static work |
| --- | --- |
| 4–80 | Reserve locals; initialize a JNIEnv pointer cell; obtain JavaVM function-table slot `+0x30`; prepare call arguments |
| 81 | Native thunk 1 prepares `GetEnv(JavaVM, &local_env, 00010004)` |
| 83–101 | Store the unknown return, compare it with zero, branch on the resulting byte |
| 105 | Nonzero `GetEnv` return jumps directly to offset 174 |
| 109–147 | Zero return crosses a key-reset marker and prepares the runtime context callback |
| 149–165 | Store callback result and prepare JNI registration using the JNIEnv cell |
| 167–174 | Store the registration return byte, then cross the common key-reset marker |
| 176–181 | Push and return literal `00010004` |

Branch `86` at offset 101 has signed 24-bit displacement **8**, producing
successor offsets **109** for a nonzero comparison byte and **105** for a
zero comparison byte. Branches `85` at offsets 105 and 170 have displacements
**69** and **4**; both target offset **174**. The native branch code makes
these displacements relative to the opcode address. Runtime return values
are kept unknown; both control-flow edges are inspected.

Both decoded branches return the JNI 1.4 literal. This does not prove that
registration succeeded or that the running app takes either path.

## Three native call preparations

### JNI environment request

At offset 81, the prepared target is symbolically
`*(JavaVM.functions + 0x30)`. The original JavaVM argument, address of a local
JNIEnv pointer cell, and `00010004` are on the VM stack. The 64-bit JNI
InvokeInterface layout identifies slot `0x30` as `GetEnv`; the corresponding
local OpenJDK 21 header is recorded in the private evidence manifest.

Thunk table entry 1 resolves to **`000e8040..000e80c8`**. It reconstructs
64-bit arguments `x0` and `x1`, a 32-bit argument `w2`, and calls the prepared
function at **`000e80b0`**. No actual JavaVM object is dereferenced.

### Runtime context callback

The zero-return path loads call-table entry 1, which points to the runtime
context pointer slot at **`000fd220`**. It dereferences that pointer, adds
**0x78** bytes, and prepares the function stored there. Thunk entry 2
(**`000e80c8`**, indirect call **`000e8110`**) passes original JavaVM and
stores the returned word. The target depends on runtime context
initialization and is not recovered by this record alone.

That result is stored but does not select the subsequent registration call.
It must not be treated as a decoded asset buffer or a verified loader status.

### Native registration

Call-table entry 0 is **`00073864`**. At record offset 165, thunk entry 3
(**`000e8128`**, indirect call **`000e8170`**) passes the JNIEnv pointer from
the cell and masks the return to its low byte. The record stores that byte
without using it to select the return literal.

## A concrete registration table and a false decoder lead

Static code at `00073864..00073f68` constructs seven `JNINativeMethod` rows
and prepares `RegisterNatives` at **`00073a00`** with count **7**. Its JNI
table base is traced from the passed JNIEnv pointer. `FindClass` obtains the
class name through runtime context descriptor field `+0x08`; that class name
has not been resolved by this bounded trace.

| Method | Selected signature | Native function |
| --- | --- | --- |
| `l` | `(Application,String) → boolean` | `000740e0` |
| `r` | `(Application,String) → boolean` | `000768f0` |
| `ra` | `(Application,String) → boolean` | `00076f78` |
| `b2b` | `(byte[],int) → byte[]` | `00077124` |
| `m` | `(String,int) → void` | `0007712c` |
| `sa` | `(String,String) → void` | `00077130` |
| `al` | `(ClassLoader,ApplicationInfo,String,String) → ClassLoader` | `00077134` |

Full JNI signatures and their exact literal offsets are in the result
fixture. These rows establish registration candidates, not running-app
calls or SDK action support.

The byte-array name looks promising, but **`b2b` is exactly a null-return
stub**: its eight-byte body sets `x0` to zero and returns. `m` and `sa` each
have a single return instruction. These methods cannot supply a standalone
container decoder in this pinned build.

`al` has a real 6,908-byte body, with class-loader and dynamic-library work.
It also contains system-call/error paths and unresolved context callbacks.
The entire function remains unexecuted. Presence of a ClassLoader signature
is not proof of a plaintext DEX buffer. The Application entries are likewise
next static targets; none has been invoked.

## Reproduction and validation

```sh
python3 tools/firmware_analysis/inspect_android_loader_record_two.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/carrier-virtual-images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /tmp/android-loader-record-two
```

The initialized image is the previously retained private output of the
[49 libexec string initializers](android-loader-string-initializers.md),
not a new Android execution requirement. Compare complete
`android-loader-record-two-{results,manifest}.json` files with
`tools/firmware_analysis/expected_results/`.

The tool retains the earlier 189-check wrapper proof as a separate
foundation and adds **616 selected native instruction checks**, 20 handler
widths, exact descriptor/table/thunk checks and full token byte coverage.
Its static queue is limited to 64 states. Unsupported handlers, branches
outside the record, incompatible rolling-key interpretations, operand
entry, partial byte coverage and invalid inputs fail closed. Results are
written only after all checks pass. These counts are static checks, not
emulated execution cases; **guest instruction count is zero**.

Ten private negative checks cover tampered APK/carrier/initialized inputs,
short or invalid headers, unsupported handlers, invalid branch targets,
corrupt reset markers and the static state bound. The three altered-file
cases reject before creating an output directory. Raw disassembly,
snapshots and selected string extracts remain ignored with owner-only
permissions.

## Next bounded lead

Trace the registered `al` or Application entry's actual asset read, decode
and ClassLoader argument construction, and the context initialization that
populates callback `+0x78` and descriptor class field `+0x08`. A validated
plaintext DEX boundary or exact SDK encoder still needs independent proof.
The null `b2b` path and the separate `DETool.dowork` preferences/database
path should not be used as speculative SDK decoder invocations.
