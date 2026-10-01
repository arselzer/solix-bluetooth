# Original C1000: native MQTT AC Smart validation

Live investigation on **2026-10-01**, original **A1761 main 1.7.1 / radio
0.3.3.0**. Its retained app identity and existing isolated AP profile were
reused. The upstream **C1000 Gen 2 main 1.1.4.9** supplied it; the original
powered the expendable HP switch. C2000 was not accessed. No cloud request,
firmware change or charging-power change was made.

## Command and confirmation

Native `0077` uses source A1=`22`, typed A2=`01 00` for Normal or `01 01`
for Smart, and FE=`03` plus UTC seconds LE32. Its optional response is `0877`.
Fresh status requests use `0040`, accepting the original's deferred `0405`
report. An acknowledgement alone does not establish that a setting changed.

Both directions require AC output **off**, a fresh typed zero AC countdown,
all eleven protected preferences and the complete 21-byte F8 flags. Only F8
AC mode byte 2 may change: Normal=`1`, Smart=`2`. Unknown tail bytes and every
other preference remain exact. The SDK sends once, then requires two explicit
complete status confirmations; it does not retry an ambiguous write.

| Stage | AC Smart writes | Explicit complete snapshots | Result |
| --- | ---: | ---: | --- |
| Independent packet prototype | 2 | 23 | Smart → Normal → Smart with AC off; full mode baseline restored |
| Public SDK repeat | 2 | 35 | Same mode sequence, plus setup/restoration scopes; whole initial baseline restored |

Each mode had three held samples. Every native snapshot also checked all
seventeen protected upstream settings, D9 and A4, allowing only dynamic LCD
activity. Its AC output stayed on, with mains connected and Standard/no active
tariff. Neither upstream configuration nor its output was written.

## Restoration and transport observations

The prototype used the verified Prime SDK to turn original AC off. After
Wi-Fi provisioning, the original stopped advertising over Bluetooth, so the
planned BLE restoration failed before sending an output command. Reopening
the isolated AP allowed one experimental native `004a` ON request; eight
complete readbacks confirmed the whole original baseline and upstream state.

The public repeat consequently used private native `004a` OFF/ON requests
only for setup/restoration. Twelve of its 35 snapshots cover the initial AC-on
baseline, those output changes and final restoration; 23 cover the mode trial.
The native output command was observed in both directions, but no public
native output-switch API or capability was added by this investigation.

The station eventually rejoined the existing AP during the SDK repeat. A
parallel BLE attempt could not find it and sent no provisioning request.
These observations do not establish a universal BLE/Wi-Fi exclusivity rule
or a reliable reconnect delay.

Both final AP runs removed their owned namespace and returned the dedicated
adapter addressless. Host routes, firewall and forwarding state matched their
recorded baselines. The AP had no default internet route throughout.

## Tool support and limits

Original native MQTT now exposes **nine verified preferences**, matching the
Prime gateway; Prime additionally has its direct AC-output SDK control.
Native support includes `NativeMqttCommands.ac_power_saving(bool)`,
`LocalMqttServer.set_ac_power_saving_enabled(bool)`, CLI, AP service, terminal,
browser and Home Assistant gateway routes:

```sh
solix-link ap-service-set-ac-power-saving \
  --directory /path/to/private/ap_service --name original --enabled off
```

Fresh AC off and an exact zero countdown are required in either direction.
Record and restore the original preference. Smart can inherit inactivity
history; enabling it does not promise a new grace period. The
[older firmware blocker investigation](c1000-smart-blocker-followup.md)
explains additional sampling conditions, without establishing installed
1.7.1 shutdown timing. Physical thresholds, waveform behavior, reboot
persistence, C1000X and generated-identity pairing remain unverified.

## Retained evidence

Private profiles, certificates, scripts, SDK snapshots, BLE notifications,
MQTT journals, network captures and restoration results remain owner-only in
ignored `.solix-private/native-ac-smart-20261001/`. Archive SHA-256 values:

- Prototype and separate AC restoration:
  `850dbcf86b1736904c3c2b0064ba935fc11b72c3f33f11e7934df62601e2458a`.
- Complete final evidence and SDK repeat:
  `a72bb228134a6e32e8bca34a0d78e2ff9615ebf5a8b71f23661cfe547edc7e48`.

Published fixtures and screenshots contain synthetic data.
