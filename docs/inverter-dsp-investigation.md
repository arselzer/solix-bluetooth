# Inverter DSP extraction and AC-input readiness

## Scope

Offline work on 2026-09-29 used the retained **C1000 Gen 2 A1763** update
package for main firmware **1.1.4.9**, including its two DSP **5.0.6.0**
components. No station, Bluetooth connection, broker, SSH host or cloud API
was contacted. The C2000 runs main **2.1.6.4** and reports a different controller
version; its DSP images have not been recovered. Cross-model equivalence remains
unproven. No firmware was flashed.

This follows the [main-controller receive and debounce trace](inverter-state-investigation.md).
The separate [schedule audit](c2000-tou-encoding-audit.md) corrects the malformed
packets used in earlier C2000 Peak trials. Those trials cannot demonstrate that
a correctly encoded schedule is blocked by this gate.

## Nested image format and integrity

Removing the outer package mask exposes another container beginning `HEX\0`.
Its record table starts at **octet 64**. Each 12-octet little-endian record is:

```text
uint32 payload_octet_offset
uint32 destination_word_address
uint16 payload_octet_length
uint16 CRC16_MODBUS
```

The payload area starts at **octet 0x3000**. Offsets are relative to that area;
an all-`ff` record terminates the table. Lengths are octets, while destination
addresses advance in **16-bit words**. Each payload word is stored big-endian.
This distinction matters: TI documents the C28x's 16-bit addressable units in
[its byte-access guide](https://software-dl.ti.com/ccs/esd/documents/c2000_byte-accesses-with-the-c28x-cpu.html).

| Component | Records | Payload octets | Verified checks |
| --- | ---: | ---: | --- |
| ACDC | 76 | 67,642 | 76/76 CRC16/MODBUS |
| DCDC | 83 | 73,856 | 83/83 CRC16/MODBUS |

CRC uses initial `ffff` and reflected polynomial `a001`. Extraction also checks
contiguous payload offsets, even lengths, complete source ranges and nonoverlapping
destination ranges. Six negative cases covering payload corruption, destination
overlap and truncation were rejected. Whole-component header checks and the outer
package trailer remain unresolved; individual block checks do not establish
update signature acceptance.

Embedded build labels identify **TMS320F2800137**, the A1763 platform and December
19, 2025 build times. ACDC identifies the 230 V variant. These are build labels,
not independently verified hardware identification for either attached station.

## Disassembly without changing instructions

The official [TI C2000 compiler tools](https://www.ti.com/tool/download/C2000-CGT),
version **25.11.1.LTS**, assembled the exact extracted words into analysis-only ELF
sections at their recorded addresses. The script checked every section's address
and bytes against the extracted input before using `dis2000`.

Adjacent records must be joined before disassembly: several two-word instructions
cross a container-record boundary. Treating each record as a separate section
causes misleading trap/unknown instructions at those boundaries. The corrected
workflow produces 13 ACDC and 14 DCDC contiguous sections. Gaps remain gaps;
numeric and string tables must not be interpreted as executable code merely
because the tool prints instruction mnemonics for them.

The first ACDC words at program address `082000` decode to a branch to `088f89`.
Code addresses below refer to **DSP word addresses**, unlike the ARM byte
addresses in earlier reports. The analysis ELF is not an installable firmware.

## Producer of register 0100, bit 0

The ACDC response builder at `0861f3` emits lowercase `main` framing. Register
read handler `08631d` dispatches through a getter table at `082f00`, indexed by
`register - 0100`, and emits little-endian register values. This independently
matches the main-controller receiver already mapped.

| Step | Recovered ACDC path |
| --- | --- |
| Register `0100` getter | `08699a` returns RAM word `a833` |
| Status update | `08675a` copies internal `aa80.bit0` into `a833.bit0` |
| Internal getter | `085bdf` returns `aa80 & 1` |
| Qualification | `08584c` sets bit 0 exactly when internal bits 2, 3 and 4 are clear |
| Voltage fault, bit 2 | `0858bb` compares input RMS with fault/recovery bounds |
| Timing/frequency fault, bit 3 | `08591a` compares a period-derived measurement with separate bounds |
| Protection summary, bit 4 | `089e2e` combines additional protection flags |

The retained debug table labels getter `085eda` **`LinVoltRms`**; that is the
measurement used by the voltage qualifier. The timing updater at `08593f`
calculates `2,500,000 / measured_period` when input RMS and the period pass its
guards. Its exact timer units and configured physical limits have not been
established. Do not manufacture thresholds or Hz values from this constant.

Therefore the recovered bit indicates **qualified AC input**. It is separate
from charger feedback (`0100.bit2`), output-switch state and the public mains
presence field. The main controller additionally checks fault registers and
debounces this input before updating its tariff gate. An enabled output or an
AC-input cable alone does not prove the entire gate is ready.

## Verification and remaining limits

A bounded integer C28x instruction-word interpreter executes the qualification
slice, actual fault-bit helpers, status copy and register getter. It fails on
unknown instructions and substitutes no function returns. All **65,536** possible
internal status words produced the expected bit, preserved unrelated status bits,
and updated the associated fault bit correctly. This is targeted instruction
replay, not a full DSP or electrical simulation.

Another **43** bounded cases execute the voltage/frequency qualification paths
with explicitly synthetic thresholds. They confirm fault entry after ten
consecutive out-of-range samples, recovery after 100 qualifying samples, counter
reset on transition, inclusive limits, and frequency-fault reset when input RMS
is below the routine's guard. These sample counts do not imply durations.
Configured physical thresholds, sampling cadence, DSP peripherals and actual
C2000 behavior remain outside the evidence. Neither this trace nor the corrected
schedule simulation establishes battery discharge on attached hardware.

## Private reproducibility

Owner-only files under `.solix-private/firmware-analysis/` retain:

- `extract_dsp.py`, `dsp-extraction-results.json`: validated sparse extraction.
- `disassemble_dsp.py`, `dsp-disassembly-results.json`: exact-word TI workflow.
- `replay_dsp_readiness.py`, `dsp-readiness-replay-results.json`: bounded replay.
- `dspACDC-sparse/`, `dspDCDC-sparse/`: record manifests, words and disassembly.
- `dsp-investigation-artifacts-sha256.json`: artifact hashes.

Extraction and replay use Python's standard library. Disassembly additionally
requires the retained TI tools under `/tmp/solix-ti-c2000-toolchain/`. Firmware,
raw disassembly and phone data remain outside Git.
