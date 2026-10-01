# C1000 Gen 2 radio readback validation

## Device, versions and scope

On **2026-10-01**, the HA node queried the same **A1763 C1000 Gen 2** previously
verified with main **1.1.4.9 / radio 0.3.3.0**. This capture did not independently
read those version strings. Discovery matched the saved device address and
model before connecting with its existing local Prime pairing ID.

The C2000 was not connected or commanded. No output, charging, provisioning,
tariff, update or timeout control was sent. Raw packets, identity, pairing ID
and full local captures remain in restricted ignored storage.

## Fresh baseline and post-query comparison

Both connections requested fresh telemetry for these protected fields before
and after the radio queries:

| Setting | Baseline and final value |
| --- | ---: |
| AC output enabled | 1 |
| DC output enabled | 0 |
| Charging upper / lower limits | 100% / 1% |
| Configured AC charging power | 1,200 W |
| Device Timeout | 0, Never |

Each field's telemetry revision had to advance after the status request.
Both comparisons matched exactly. These are before/after station reports;
they are not electrical measurements of output continuity. No restoration
write was needed.

## RSSI: live unavailable response, strict public API

The first connection sent three encrypted function **`10` / `4022`** requests,
each with raw A1 source `21`. All returned function `10` / **`4822`**, with
decrypted body **`01`**. The firmware's RSSI handler uses that result when no
nonzero RSSI observation is available. It is not a reading of zero dBm or 100%
quality, and does not by itself distinguish service failure from no association.

The built wheel was then unpacked into a private scratch directory on the HA
node. A second connection called the new public method three times:

```python
rssi_dbm = await monitor.wifi_rssi(timeout=20)
# int when reported; None when unavailable
```

All three returned **`None`**, exercising the packaged request builder,
function-aware feed, independent radio response queue and strict decoder.
The CLI exposes the same query:

```sh
solix-link wifi-rssi --name test-station
# {"wifi_rssi_dbm": null} when unavailable
```

Only **C1000 Gen 2 Prime** is enabled. Other models, Legacy mode and incomplete
negotiation fail before query construction; nonpositive/nonfinite timeouts fail
before a write. The decoder accepts exact raw A1 length-4 signed-byte readings,
recognizes only the exact unavailable body, and rejects malformed or unexpected
responses. Radio replies never enter the controller response queue or battery
telemetry decoder. There is no automatic RSSI polling or new HA entity.

Signed readings and authentication/malformed-reply handling have synthetic
tests. **A successful live dBm observation has not yet been captured.**
See the [firmware routing proof](radio-rssi-routing.md) and
[cached-quality limitation](gen2-normal-feature-audit.md).

## Additional read-only radio observations

The second connection also sent one BLE function-10 query for each newly
traced route. These private probes are not new runtime methods:

| Query / reply | Observation |
| --- | --- |
| `404b` / `484b` | Status `00`; raw A1 binding = 1, A2 BLE predicate = 1, A3 cached MQTT flag = 0 |
| `404a` / `484a` | Status `00`; radio epoch within four seconds of the HA host at capture completion; raw signed offset = 0 |

The MQTT flag is cached software state, not a broker round trip. The radio
clock query can refresh volatile timezone-transition cache and does not prove
the main controller's RTC or tariff clock. These are **BLE** observations:
the native raw MQTT route forwards these higher opcodes elsewhere.
The [42-case route audit](radio-status-routing.md) and
[106-case semantics audit](radio-status-features.md) explain those limits.

## Verification and remaining work

```sh
PYTHONPATH=python python3 -m pytest python/tests home_assistant_tests -q
```

**2,040 tests passed**: 1,541 Python tests and 499 HA contract tests. New RSSI
cases cover signed widths, unavailable/malformed bodies, authenticated frames,
namespace isolation, unsupported models, stale queued replies, concurrent
queries, timeouts and CLI preconnection checks. Existing controller behavior
and gateway contracts still pass.

The tested wheel SHA-256 is
`a62d08041398f808dd22f61a1684e29f037a7187a8562eae93f34045fd6d32eb`.
Its four changed runtime modules were compared byte-for-byte with source;
the HA scratch copy's hash was verified before import. The verified wheel was
also installed in the existing laptop CLI and HA research environments, and
their installed modules matched it exactly. Application services and HA
configuration were not restarted for these checks.

A future positive RSSI observation needs a station already associated with an
appropriate AP. Other models, a native MQTT hardware RSSI round trip and HA
runtime compatibility remain separate validations.
