# Optional inactive clock brightness selects

The HA integration provides **First clock window brightness** and **Second
clock window brightness** for **C1000 Gen 2 main 1.1.4.9 over native MQTT**.
Both are configuration entities, disabled by default. Enable the desired
entity from the station's HA entity settings after updating the integration.

`Normal` maps to raw flag 0; `High` maps to raw flag 1. Selecting an option
sends only `set-clock-brightness` with `window: 1` or `2` and a boolean `high`.
It does not enable clock, set a clock schedule/theme, upload screen data or
toggle any output. The other window's flag is preserved.

Availability requires:

- Fresh connected telemetry, a gateway control token and advertised capability.
- Exact supported firmware and native C1000 Gen 2 model/transport.
- Standard mode with no active tariff, clock disabled, transfer idle and both
  output countdowns inactive.
- Complete first/second brightness flags, each exactly integer 0 or 1.

Missing, unknown or active-clock state disables both options. C2000 and the
original C1000 do not discover these selects. Current safety gates are checked
again when selecting an option; the gateway performs its own confirmation.

Entity roles/unique-ID suffixes retain the telemetry keys
`clock_screen_first_brightness_flag_raw` and
`clock_screen_second_brightness_flag_raw`. Translation keys are
`clock_first_brightness` and `clock_second_brightness`. Existing scope warnings
are merged with the shared role/protocol attributes.

Forty synthetic HA entity/API cases verify discovery, window mapping, peer
preservation, strict gates, staleness, revoked capability and labels. These
execute the actual select class with scoped HA doubles, without hardware or
HA runtime access. They do not establish visible screen brightness or physical
output behavior. A subsequent [hardware trial](c1000-gen2-clock-ac-smart-validation.md)
confirmed both saved flags and restoration; visible brightness remains untested.
Actual HA 2026.7.4 registered both selects disabled by default. Its select-service
write path has synthetic coverage; the live round trip used the native SDK.

```sh
PYTHONPATH=/tmp/solix-test-deps python3 -m pytest \
  home_assistant_tests/test_clock_brightness_select.py -q
```
