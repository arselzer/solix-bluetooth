# Original C1000: saved charging limits and configuration reload

Offline investigation, 2026-10-02, using public **A1761 MainMcu 1.5.9**.
The installed station runs **1.7.1**, whose controller image is unavailable.
No device, radio, network, output, flash or persistent setting was accessed.
These results do not establish what the installed firmware does on restart.

## A write and a configuration reload have different validation

The existing `0044` handler accepts a raw charging-power word and stores it
at `20002040`; getter `08017500` returns it. The
[charging-producer investigation](c1000-bypass-firmware-followup.md) already
proved that storing zero does not select the charging-disable branch.

This follow-up finds another limit: saved-settings validator **`08025768`**
checks the charging word against an inclusive range:

| Synthetic variant byte at `20000454` | Saved charging-power range |
|---|---|
| 0 | 200–1000 |
| 1 or 2 | 200–750 |

The exact checks use `08010176(value, minimum, maximum)`, returning one for
out-of-range values. The variant is an internal firmware selector; this
does not identify or validate another hardware model. These ranges establish
configuration acceptance in this image, not realized charging watts or an
electrical safety limit.

Six actual handler → getter → CRC-valid synthetic file → loader cases
show the distinction: **100 is accepted and read back by the command path,
then rejected by this older image's configuration loader; 200 is retained.**
Saved 199/200, 750/751 and 1000/1001 are tested explicitly. This does not
change the SDK's live-verified 100 W domain for main 1.7.1.

## Valid files can still cause a broader default reset

Configuration loader **`08023410`**, called at `080162a0`, reads a 44-byte
record into `20002038`. It checks the `sysP` header and the CRC-8 byte at
`+4` over the 36 setting bytes at `+8`, using actual helper `08009644`.
After those checks, `08023480` calls the validator and requires the retained
marker at `+1f` to equal `f7`. A settings rejection reaches
**`0802348e` → default initializer `08023b6c`**.

With synthetic Fast enabled, Device Timeout Never, both Smart modes Normal
and both output flags enabled, a valid file containing 100 W produces:

| Setting | Before load | After defaults, variant 0 |
|---|---|---|
| Charging-power word | 100 | 1000 |
| Device Timeout | Never / 0 | 720 minutes / 12 hours |
| Fast flag | On | Off; unrelated bits preserved |
| AC and DC Smart modes | Normal / 1 | Smart / 2 |
| Brightness | 3 | 2 |

Nonzero variants instead restore the charging word to 750. The default
initializer also copies the rest of its template, retains four specific
configuration members, derives frequency/temperature preference from seeded
controller caches, and clears one report-cache byte. This is a broader reset,
not a one-field charging correction.

Valid files inside the corresponding range preserve the complete tested
configuration, Fast, Never and Normal modes. Exact full low-RAM comparisons
assert all observed changes in both accepted and defaulted cases.

### Bad CRC takes an earlier branch

Nine separate corrupt-CRC cases use saved 100, 200 and 750. They reach
**`08023474`** and initialize defaults **without reaching `08023480`**.
Three header-corruption cases take the same earlier branch. In contrast,
the CRC-valid 100 W cases do reach settings validation before defaulting.
This distinguishes settings rejection from a damaged or misconstructed file.
Synthetic file open/read/close services replace real storage; the actual
header comparison, CRC calculation, validation and default instructions run.

## Limits for automation and charging control

The synchronous replay preserves the complete power-state word, including
AC-output, car/DC-output and charging-gate bits. It captures a display event
and persistence boundaries. **The later event loop, Smart auto-off policy,
DSP, relay and physical output are not executed.** RAM preservation is not
an uninterrupted-output result: defaulting into Smart can affect future
operation, as the separate [Smart policy analysis](c1000-smart-auto-off-policy.md)
demonstrates.

The [main 1.7.1 charging-rate trial](c1000-charging-rate-validation.md) verified
100 W write/readback and restoration, while remaining inconclusive about
physical charging rate. Its image and restart retention are unverified;
the [Fast/current investigation](c1000-fast-current-limits.md) separately
limits electrical interpretations. Do not apply this 1.5.9 reset behavior to
the installed device or change its SDK domain based on this image alone.

No new external charging-pause, input-disable, bypass-selection or
battery-only command was established. This result adds a versioned
configuration-retention boundary. A matching main 1.7.1 image or a separately
authorized, noncritical restart test would resolve the newer boundary.

## Reproduce

```sh
python3 -m pip install -r tools/firmware_analysis/requirements.txt
python3 tools/firmware_analysis/emulate_original_settings_load.py \
  --output /tmp/original-settings-load.json \
  --manifest /tmp/original-settings-load-manifest.json
cmp /tmp/original-settings-load.json \
  tools/firmware_analysis/expected_results/original-settings-load-results.json
```

**108 cases pass:** 45 validators, 45 complete CRC-valid file loads, nine
bad-CRC cases, three bad-header cases and six handler-to-file-load sequences.
The controller hash is
`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`.
The manifest records tool/helper/dependency/result hashes and substitutions.
Public fixtures use synthetic settings and public firmware metadata only.
Exploratory probes remain owner-only in ignored
`.solix-private/original-external-gates-20261002/`.
