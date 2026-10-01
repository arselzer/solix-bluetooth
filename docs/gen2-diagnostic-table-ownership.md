# Gen 2 diagnostic command-table ownership

## Result

The radio-facing diagnostic descriptor at **`20000760`** is initialized to
zero and remains untouched by the reviewed software initialization paths in
published **A1763 C1000 Gen 2 main 1.1.4.9**. Those same paths demonstrably
install the adjacent ordinary app table and register function `0c` itself.
This strengthens the [previous exposure finding](gen2-diagnostic-getter-audit.md#exposure-registration-is-not-an-installed-command-handler)
without claiming that every possible later writer has been excluded.

**22 synthetic instruction cases pass:** 16 initialization variants and six
bounded indexed-read cases. A separate computed-address candidate search
finds no resolved store to the descriptor. No station, network, cloud,
storage backend, firmware modification or production API was used.

All addresses apply only to `MainMcu-decoded.bin` in
`firmware/c1000_gen2/1.1.4.9/`, loaded at `08005000`, 198,656 bytes:

```text
SHA-256 21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9
```

## Exact startup ownership

The startup loop `08005a8c..08005aa2` executes two scatter descriptors:

| Descriptor | Source | Destination | Size | Initializer |
| --- | --- | --- | --- | --- |
| `080353b4` | `080353d4` | `20000000` | `0be0` | `08005b6a` |
| `080353c4` | `08035720` | `20000be0` | `120f8` | `08011a28` |

The diagnostic descriptor lies in the **first, compressed initialized-data
region**, not the later zero-fill region. Starting all mapped RAM with
`A5`, the actual initializer writes zero to each byte `20000760..20000767`
at instruction **`08005b9c`**. This rules out an emulator's initially empty
RAM as the explanation for the zero descriptor.

A word-aligned search of the initialized global RAM finds no stored pointer
into those eight bytes. This is a snapshot of startup data; later aliases
and unaligned/encoded pointers are outside that statement.

## Complete reviewed software initialization

The replay executes these routines and their actual callees, substituting
only log sinks `0800d284` and `08026fd4`:

| Entry | Purpose and relevant descendants |
| --- | --- |
| `08010808` | Timer-system initialization and allocation |
| `08013c84` | Ordinary app-table installation through `080291b0`, then app timers |
| `080309c8` | Wireless initialization: callback configuration, all three function registrations, timers, queue initialization and saved-flag reads |
| `08031144` | Within wireless init: callback structures → `080275f8`, protocol init `08013b3c`, sender registration `08027ad0` |
| `08013b3c` | Copies protocol callbacks through `080275b8` → `08027040`; initializes the protocol queue through `08029eb0` |

Memory/string operations, timer functions, queue initializers and registration
instructions execute from the image. No peripheral region is mapped, and
every emulated write must target mapped RAM. Bounded execution must return
normally; the results retain executed direct-call edges.

The ordinary descriptor is positively installed:

```text
080291b2: write pointer 08032e60 → 20000758
080291b4: write count   0011     → 2000075c
```

By contrast, the adjacent diagnostic descriptor receives **zero writes**
after startup initialization. Its pointer is `20000760`, count is
`20000764`, and the wrapper reads them at `0801771c` / `0801771a`.

Other callback ownership is separate:

| Structure | RAM location | Installation |
| --- | --- | --- |
| Wireless callbacks | `20007e78`, 40 bytes | `080275f8` |
| Protocol callbacks | `20009888`, 128 bytes | `08027040` |
| Function callback registry | `20009908`, 16-byte slots | `08017d08` |
| Sender callback array | `20009a88` | `08027ad0` |

Each replay confirms registry callbacks **`10 → 08022614`**, **`0f →
080080a8`**, and **`0c → 08017704`**. Registering the last wrapper does not
install its own command table.

Sixteen cases vary the saved binding byte, saved configuration byte
`SETTINGS + 187`, zero versus an eight-byte descriptor canary, and the order
of app versus wireless initialization. Both zero and canary are preserved.
The normal main path calls app init from `08006ffc` before wireless init
through `08009524`; reverse order is a synthetic robustness case.

These initializers do have other effects in synthetic RAM: they allocate
timers, copy callbacks, update the binding cache and, when configuration byte
`187` is set, clear bit `10000000` in the word at `20000164`. This replay
does not describe the initializers as passive device commands or execute
them on hardware. It does not emulate all main-loop or peripheral startup.

## Computed-address search and candidate review

The standalone tool searches from **2,060 aligned candidate seeds**:
PC-relative loads of RAM constants and `MOVW` constructions. It follows
known constants through supported register moves, arithmetic, shifts,
flash loads, `MOVT`, and bounded branch paths. Calls invalidate caller-saved
register constants. This goes beyond searching for literal `20000760`.

Resolved accesses in the 16-byte ordinary/diagnostic descriptor window are
exactly the ordinary table's two reads and two writes, plus the diagnostic
table's two reads. No nearby `MOVW`/`MOVT` construction resolves to another
descriptor access. Two indexed candidates require separate review:

| Candidate | Resolution |
| --- | --- |
| `08019b5e`, base `200007bc` | Actual read-only variant lookup. Six instruction cases prove reads at `200007bc + 20*i`, `i=0..4`, including the no-match case. These addresses cannot overlap the descriptor. |
| Apparent `08026ed4` store | False instruction candidate inside public string `LCD_DATA_UPDATE_EVENT` at `08026ecc`. Actual `ADR` at `08026e7a` references the string. |

The candidate list and both resolutions are checked by assertions. Including
aligned data as potential instructions deliberately makes this a candidate
search; reviewing false positives is necessary.

**Limits:** 298 seeds reach a 96-instruction-depth or 300-state bound. The
analysis does not reconstruct all stack aliases, runtime RAM pointer loads,
indirect calls, IT state or unsupported transformations. It does not follow
every asynchronous callback or peripheral event. Therefore the result is
“no installer established by these paths,” not a whole-program proof that
no writer can exist.

## Implication for further work

The known controller startup/registration path supplies no demonstrated
BLE/native route to the inner factory getters. The radio's ability to
forward function `0c` does not close this controller-side gap. Getter
semantics and their timer side effects remain as documented in the
[getter audit](gen2-diagnostic-getter-audit.md); there is still no complete
disaster-plan export or new diagnostic SDK command to expose.

The next useful evidence would be an executed later installer, a supported
build/mode that initializes the descriptor, or a different proven transport.
This finding supplies no reason to guess additional live diagnostic frames.

## Reproduce

Dependencies verified: Unicorn 2.1.4 and Capstone 5.0.7.

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/audit_gen2_diagnostic_table.py \
  --image firmware/c1000_gen2/1.1.4.9/MainMcu-decoded.bin \
  --output /tmp/gen2-diagnostic-table-results.json \
  --manifest /tmp/gen2-diagnostic-table-manifest.json
```

Checked-in [synthetic results](../tools/firmware_analysis/expected_results/gen2-diagnostic-table-results.json)
and [hash manifest](../tools/firmware_analysis/expected_results/gen2-diagnostic-table-manifest.json)
retain the startup descriptors, write provenance, initialization call edges,
candidate accesses, bounds and case assertions. The tool checks its firmware
hash and requires assertions. No captured identity or credentials are used.
