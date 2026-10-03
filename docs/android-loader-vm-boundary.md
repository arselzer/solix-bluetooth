# Protected JNI wrapper and data-encryption boundaries

## Result

Static follow-up resolves the first opaque `libexec.so.JNI_OnLoad` wrapper
into an exact native call and another protected record. The useful remaining
code-loader target is **record 2, 182 bytes at `001136cc..00113782`**.
No JNI method, interpreter, call thunk or Android method was executed. This
is evidence from one pinned app protection build, not a station-firmware
finding or proof of an AC-input-disable command.

A separate trace shows that `libijmDataEncryption_arm64.so.DETool.dowork`
handles protected app data, with preferences/database paths. Its registered
name does not identify the missing SDK DEX decoder.

## Inputs and address conventions

Addresses below are offsets in recovered ARM64 virtual images; end addresses
are exclusive. Relative pointers use the analysis base `10000000`.

| Input | SHA-256 |
| --- | --- |
| Retained base APK | `27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110` |
| Packed `libexec.so` | `4fd13d12d64e6d451f5b567c173917225c1b1e2deb97abdeca10110b8dc4f379` |
| Unpacked `libexec.so.virtual-memory.bin` | `b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57` |
| Packed data-encryption library | `3dd7349af2d0ca697e7ab30a673231fa9ec4fd0b1cf13c3ef577cd7355c05032` |
| Private post-initializer data-encryption image | `241a7cf3156479a4c35e7028d9fcd7ebdd10eca2f742ea17c63bed4c68393bef` |

The public metadata tool needs only the APK and the existing carrier tool's
unpacked `libexec` snapshot. Pure ELF relocation parsing reproduces the
selected code, record and table bytes without running string initializers.
The data-encryption argument trace uses the separately retained private
post-initializer image from the [68 initializer investigation](android-loader-string-initializers.md).

## First protected JNI record

`JNI_OnLoad` at `00072e74` saves original arguments, pushes each 64-bit pointer
through `000e2e10`, and passes selector zero to `000e2e00`. That selector reads
the signed offset from a pair-indexed table at `00113790`, adds it to record
base `001135f0`, and branches to interpreter `000bd624`.

The three stored offset/length pairs are:

| Selector | Record range | Stored bytes |
| ---: | --- | ---: |
| 0 | `001135f0..00113608` | 24 |
| 1 | `00113608..001136cc` | 196 |
| 2 | `001136cc..00113782` | 182 |

All three selected headers decode a four-word frame and first handler `2c`.
The stored lengths establish static boundaries; the selector wrapper itself
loads the offset, not the adjacent length. These lengths do not establish an
interpreter runtime bounds check.

For record zero only, static handler reads and pointer increments establish:

| Record offset | Handler | Operand bytes / value | Static operation |
| ---: | --- | --- | --- |
| 4 | `2c` → `000bd760` | 1 / 2 | Reserve and zero two words |
| 6 | `4c` → `000befc0` | 2 / 2 | Push call-table entry 2 |
| 9 | `39` → `000c39c8` | 2 / 0 | Push pointer from local word 0 |
| 12 | `39` → `000c39c8` | 2 / 2 | Push pointer from local word 2 |
| 15 | `4d` → `000c3768` | 1 / 0 | Invoke native thunk entry 0 |

The header and opcode arithmetic use a byte transform followed by XOR.
After the first token, the previous decoded opcode participates in the next
key; a single constant key produces the wrong second opcode. The interpreter
also has a special marker branch. It is excluded explicitly for this prefix.
Only bytes through offset 17 are parsed; the remaining seven record bytes
and the bodies of records 1 and 2 remain unparsed.

Call-table entry 2 at `001135c0` resolves to **`000848cc`**. Thunk-table entry
zero at `001135d0` resolves to **`000e7fc4`**. The thunk reconstructs two
64-bit arguments from the VM stack and its `BLR` at `000e8028` calls the
pushed target with original JNI `x0`, then original JNI `x1`. This is a
static argument-order proof, not an invocation.

With non-null `x0`, `000848cc` tail-branches to **`00072dfc`**, which uses
the same tables with selector **2**. Thus the 182-byte record is the next
bounded target. The null branch has recursive/indirect loading behavior and
is outside the proven path. The resolved wrapper does not expose DEX bytes,
an encryption key or the SDK action serializer.

## Separate `DETool.dowork` path

The registered method at `000234b8` has four string arguments, an integer
and a boolean after the JNI implicit arguments. Static stack saves preserve
first/second strings at `SP+40`/`SP+48`, third/fourth at `SP+28`/`SP+30`,
the integer at `SP+3c`, and the boolean as a canonicalized byte.

The first string reaches `GetStringUTFChars` / `ReleaseStringUTFChars` at
`00024b10` / `00024dd4`; the second reaches them at `000252d8` /
`000254ac`. Their function-table bases are traced from the saved `JNIEnv*`,
not inferred from an arbitrary matching structure offset. The other two
strings use wrapper `00039eb0` → `00039a40`, which resolves
`java/lang/String.getBytes:()[B`, obtains byte-array elements, copies them
into a C++ string and releases the array. This is string conversion, not
a demonstrated protected-code decryptor.

The method references `/preferences`, `/databases/db`, `sp_e`, `db_e` and a
logging schema naming `datapath`, `param`, `timestamp`, `buildtime` and
`is_first_run`. These consumers support the data-protection boundary; exact
Java caller argument construction and all encryption paths remain unknown.
Helpers `0002669c` and `00026a60` perform C++ string capacity/assignment
operations and must not be treated as decoder entry points.

## Asset read versus decoding

The raw asset reader `00055218..00055314` takes its filename as an argument,
allocates length plus one, zeroes the buffer, calls `AAsset_read`, and stores
the returned byte count separately. It reports success after the read without
requiring a full-length return. A buffer here can still contain encrypted
container data; it is not a validated plaintext DEX boundary.

Three bounded callers obtain filename fields through a runtime context's
`+2d0` descriptor: `00054998` uses field `+28`, `00054a60` uses `+30`, and
`00054b4c` uses `+68`. Some branches choose a separate archive/file path.
Static relocated slots also point at the known container names, but this
trace does not prove that those slots populate that runtime descriptor.
`00055314` passes asset bytes to `000553dc`, whose observed work is writing
a file, including selected cleanup operations; that call is not a proven
decryption step and must not be executed as an offline decoder.

## Reproduction and next investigation

```sh
python3 tools/firmware_analysis/inspect_android_loader_vm_boundary.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/carrier-virtual-images \
  --output-dir /tmp/android-loader-vm-boundary
```

Compare `android-loader-vm-boundary-{results,manifest}.json` with
`tools/firmware_analysis/expected_results/`. The proof checks exact input
hashes, FDE bounds, 189 selected instructions, three record boundaries,
five token widths, rolling keys and table/thunk/target identities. It writes
metadata only after all checks pass. Raw code, APKs and private traces stay
ignored; this tool executes **zero guest instructions**.

Next, statically recover only the handler widths and external table uses
needed by record 2, then follow the resulting asset/decode calls. Stop at
unsupported tokens or indirect targets. Recovering a charging-disable packet
still requires the SDK serializer, exact model gates and independent state
readback; no station write follows from this loader result.
