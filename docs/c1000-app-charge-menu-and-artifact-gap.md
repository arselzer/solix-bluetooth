# Original C1000: charge-power menu and retained update gap

Offline follow-up dated **2026-10-03**. The retained app's ARM64 `libapp.so`
has SHA-256
`8537b4f8a4da327f9bf298d99e90ad969b89f52ae7d6d68c454e5bf0ec25c070`.
No phone, cloud, station, firmware update or setting access occurred.

## An original-model app menu, not a new charging control

`A1753settingLogic.getAcrpList` at **`0478a8a4`** explicitly compares product
codes including **A1761/A1762**. For those products, every examined
serial-derived country branch calls `generateListByFixedStep` with the same
unboxed integer arguments: **200 and 1000**. The four calls are at
`0478ab20`, `0478ab38`, `0478ab74` and `0478ab8c`.

The shared helper **`024295a0`** stores the lower endpoint, generates
intermediate multiples of **100** (`02429740..02429748`), then stores the
upper endpoint. Its integer-to-Smi boxing instructions distinguish actual
power values from tagged integers. The resulting original-model choices are:

```text
200, 300, 400, 500, 600, 700, 800, 900, 1000 W
```

`A1753settingLogic.initData` (`047c3b98`) calls the builder at `047c3c1c`
after an A1753-family type check, then stores the list at member offset `8f`.
Original `A1761AnkerDevice`, class ID `212a`, passes that family check.
`A1781settingLogic.showACRPPicker` (`03c6888c`) reads this member at
`03c688dc`, maps it to picker strings and passes it to `showBottomPicker`.
The examined consumer adds no zero-power or pause choice. Its normal setter
selects the family device method; the existing original watt builder is
documented in the [HTTP-wrapper audit](c1000-ota-http-wrapper.md).

This is a further **app UI boundary**, not device admissibility, saved-value
retention, input-budget enforcement or physical charging behavior. The
library's separately verified **100 W** support remains unchanged. No new
original charging-disable or battery-only command was found. Identical menu
values across country branches also do not identify a firmware voltage
variant. The loaded, below-full [native test plan](c1000-native-charging-test-plan.md)
remains the useful physical follow-up.

## No overlooked installed-version artifact in the retained update set

The bounded audit covered the retained original update phone/AP directories
and original firmware download directory:

- Four phone bugreport ZIPs and eleven AP result TARs contained no firmware
  candidates with `.bin`, `.fw`, `.pkg` or `.hex` filenames, or accessible
  internal Anker app-document paths.
- **471 distinct text files / 564,427,223 bytes** were searched after content
  deduplication, including archive members. No `lastPackage`, `full_package`,
  `product_component`, app-download marker or selected TLS key-log record
  was found. Both update-URL/path matches refer to the already retained
  public **1.5.9** download metadata.
- Sixteen bounded main/original-package candidates across public/private
  binary files were hashed. The original main copies match **1.5.9**
  (`b295ee8613f5c96e70dcc905896df516621cab4dc590bb580eac6b84519911a6`);
  the other main copies match the known **Gen 2 1.1.4.9** image. No new main
  candidate surfaced. This is not a search of every possible encoded format.
- A retained vendor-bucket listing response contains `AccessDenied`.
  Anonymous enumeration was not established; no new request was made.

The already documented [official update capture](c1000-original-update-network.md)
remains TLS ciphertext. This audit found neither plaintext package metadata
nor session-key records that would unlock those captured records.

## Acquisition boundary and reproduction

Installed original **main 1.7.1** remains missing. Existing private data do not
supply its exact URL, component hash or bytes, so acquisition cannot proceed
from these retained files alone. A discovered public artifact URL could permit
an account-free download, but no such 1.7.1 URL is established here. The
[app metadata capture plan](c1000-ota-http-wrapper.md) remains a prospective
source when the phone or another authorized plaintext metadata source becomes
available; the BLE owner ID does not provide app HTTP authentication.

Owner-only helpers and manifests are retained in
`.solix-private/original-update-gap-20261003/`:

```sh
python3 .solix-private/original-update-gap-20261003/audit_update_artifacts.py
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  .solix-private/original-update-gap-20261003/trace_charge_menu.py
```

The menu audit checks twelve selected functions, ten exact list-builder call
arguments and the 100-step instructions. Independent AOT extraction confirms
the selected instruction/object-pool data; decompiler pseudocode is navigation
only. These are static checks, **not additional CPU emulation cases**.
Private raw matches, app source and phone identifiers remain unpublished with
directories 700/files 600. No runtime domain, shared index or deployment changed.
