# Anker App Reverse Engineering Notes

Decompiled from `com.anker.charging` (Flutter/Dart app, ARM64 native binary).

## App Structure

- **Package**: `com.anker.charging`
- **Framework**: Flutter (Dart compiled to `libapp.so`)
- **BLE Module**: `ble_zx/` (custom BLE protocol)
- **Device Models**: Each device has its own command/controller classes

## Device Files

### C1000 (A1761, part of A1753 family)
- `ble/device/A1753/a1753_device_commands.dart`
- `ble/device/A1753/a1753_device_controller.dart`
- `ble/device/A1753/a1761_anker_device.dart`

### Solarbank 3 Pro (A17C5)
- `ble/device/A17C5/a17c5_device_commands.dart`
- `ble/device/A17C5/a17c5_anker_device.dart`

### BLE Protocol Core
- `ble_zx/ble_confer/ecdh_confer.dart` — ECDH key exchange
- `ble_zx/ble_confer/secure_ecdh_confer.dart` — Secure ECDH
- `ble_zx/helper/aes_helper.dart` — AES encryption
- `ble_zx/base/assemble_command_util.dart` — Command assembly
- `ble_zx/helper/ble_write_payload_helper.dart` — Payload construction

## Discovered Command Methods

### Send Methods (commands the app sends to devices)
```
sendBleCmd                              — Main BLE command sender
sendDeviceCommand                       — Generic device command
sendDeviceCommandOfAIEmsMode            — AI energy management mode
sendDeviceCommandOfConservepercent      — Conservation percentage (min SoC?)
sendDeviceCommandOfGridRecharging       — Grid recharging control
sendDeviceCommandOfManualBackupPower    — Manual backup power mode
sendDeviceCommandOfMode                 — Mode selection
sendDeviceCommandOfPeckShaving          — Peak shaving
sendDeviceCommandOfTimeslot             — Time slot scheduling
sendDeviceCommandOfTou                  — Time-of-Use pricing
sendDeviceCommandOfTouAndMode           — TOU + mode combined
sendDeviceCommandOfUtilityRatePlan      — Utility rate plan
sendDeviceHomeLoadsData                 — Home load settings
sendDeviceMicroPower                    — Micro inverter power
sendDeviceSelfTest                      — Self test trigger
sendDeviceTacticsTime                   — Tactics/strategy timing
sendEmsStrategyPatternCommand           — EMS strategy pattern
sendHomeLoadData                        — Home load data
sendHomeLoadLoopMode                    — Home load loop mode
sendPeakAndValleyCmd                    — Peak & valley pricing
sendPeakValleyData                      — Peak valley data
sendScreenOffTimeSchedule               — Screen off schedule
sendClockFormatSchedule                 — Clock format
sendDeleteTaskSchedule                  — Delete scheduled task
sendHeatPumpTimePlanCmd                 — Heat pump time plan
sendScreensaverCycleSchedule            — Screensaver cycle
sendScreensaverSwitchSchedule           — Screensaver switch
sendDeviceInfoCmd                       — Device info request
sendReportHeartCmd                      — Heartbeat
sendAutoDisasterData                    — Auto disaster preparedness
sendCmdRequest                          — Generic command request
sendCommandWithParam                    — Command with parameters
```

### Set Methods (writable parameters)
```
# Power & Output Control
setAcOutput                    — AC output on/off
setACOutputSwitch              — AC output switch
setACOutputCountDown           — AC output countdown timer
setDCOutputSwitch              — DC output switch
setDCOutputCountDown           — DC output countdown timer
setACControlSwitch             — AC control switch
setDCRelaySwitch               — DC relay switch
setACRelaySwitch               — AC relay switch
setAcSwitch                    — AC switch
setAcCountdown                 — AC countdown
setAcCountDownTime             — AC countdown time
setCarSwitch                   — Car charger switch
setCarCountdown                — Car charger countdown
setCarCountDownTime            — Car charger countdown time
setACPortCountdownTask         — AC port countdown task
setACPortTimedTask             — AC port timed task
setDCPortCountdownTask         — DC port countdown task
setDCPortTimedTask             — DC port timed task
setPowerLimit                  — Power limit
setSiteDevicePowerLimit        — Site device power limit
setDevicePowerLimit            — Device power limit
setDeviceMicroPowerLimit       — Micro power limit
setDevicePvPowerLimit          — PV power limit
setPeakPowerLimit              — Peak power limit
setMaxOutputPowerRequest       — Max output power
setPowerOffTimer               — Power off timer
setPowerOnTimer                — Power on timer

# Battery & Charging
setBatterySoc                  — Battery SoC
setBatterySOC                  — Battery SOC
setBatteryReserve              — Battery reserve percentage
setBatteryDischarge            — Battery discharge control
setSoc                         — Set SoC
setCloseSOC                    — Close SoC threshold
setNormalStartSoc              — Normal start SoC
setQuietStartSoc               — Quiet mode start SoC
setSafetySocParams             — Safety SoC parameters
setChargeMode                  — Charge mode
setChargeModeParams            — Charge mode parameters
setChargingMode                — Charging mode
setChargingSchedule            — Charging schedule
setChargingScheduleList        — Charging schedule list
setSuperCharge                 — Super charge mode
setAcChargePower               — AC charge power
setAcChargingPower             — AC charging power
setCycleChargingPower          — Cycle charging power
setGridCharging                — Grid charging on/off
setOnlySmartCharging           — Smart charging only
setAllowHesToCharge            — Allow HES to charge

# Display & UI
setScreenBrightness            — Screen brightness
setScreenOffTime               — Screen off timeout
setScreenTimeout               — Screen timeout
setScreenTime                  — Screen time
setScreenAlwaysOn              — Screen always on
setScreenSaver                 — Screen saver
setScreensaverSwitch           — Screensaver switch
setLcdBrightness               — LCD brightness
setLcdLight                    — LCD light
setBrightness                  — Brightness
setClockDisplayTime            — Clock display time
setClockFormat                 — Clock format (12/24h)
setDeviceScreenTime            — Device screen time
setAcLightMode                 — AC light mode
setLightSwitch                 — Light switch
setAmbientLight                — Ambient light
setAmbientLightSwitch          — Ambient light switch
setAtmosphereLightsHrightness  — Atmosphere lights brightness

# Grid & Energy
setFeedGridSwitch              — Feed-in to grid switch
setFeedGridValue               — Feed-in grid value
setFeedNetworkLimitValue       — Feed network limit value
setGridConnectedInput          — Grid connected input
setGridSensitivity             — Grid sensitivity
setOffGridSwitch               — Off-grid switch
setGreenEnergyPriority         — Green energy priority
setSelfConsumption              — Self consumption mode
setEnergyMode                  — Energy mode
setEMSTactics                  — EMS tactics
setEmsModeN                    — EMS mode
setTOU                         — Time of Use
setTimeOfUse                   — Time of Use
setPeakShaving                 — Peak shaving
setPeakAndValleyPeriods        — Peak & valley periods
setPeakValley                  — Peak valley
setElectricityPrice            — Electricity price
setFixedPrice                  — Fixed price
setDynamicPrice                — Dynamic price
setHomeLoadValue               — Home load value
setHomeLoadWeek                — Home load weekly schedule
setDeviceHomeLoadData          — Device home load data
setSiteHomeLoadDevicePower     — Site home load device power
setBle17c0HomeLoads            — BLE home loads (17C0)
setA17C1PowerLimit800W         — A17C1 power limit 800W
setDeviceFeedGridSwitch        — Device feed grid switch

# Backup & Disaster
setBackPower                   — Backup power
setBackPowerMode               — Backup power mode
setBackPowerTime               — Backup power time
setBackupStrategy              — Backup strategy
setManualBackupPower           — Manual backup power
setDisasterSwitch              — Disaster preparedness switch
setManualDisasterSwitch        — Manual disaster switch
setAutoStrategy                — Auto strategy

# Schedules & Timers
setSchedule                    — Schedule
setTimedShutdownAC             — Timed shutdown AC
setTimedShutdownCancelAC       — Cancel timed shutdown AC
setTimerTurnOff                — Timer turn off
setTimerTurnOn                 — Timer turn on
setTimingCmd                   — Timing command

# Device Settings
setLanguage                    — Device language
setTemperatureUnit             — Temperature unit (C/F)
setDeviceHourFormat            — Hour format
setWorkMode                    — Work mode
setSleepMode                   — Sleep mode
setSleepTime                   — Sleep time
setAutoUpgrade                 — Auto upgrade
setResetRes                    — Factory reset
setBuzzerSwitchReq             — Buzzer switch
setMemoryPortValue             — Memory port value
setPortPriority                — Port priority
setPortRemark                  — Port remark
setDeviceStartup               — Device startup config
```

### Parse Methods (response types the device sends)
```
parseDeviceAllInfo             — Full device info
parseDeviceInfoData            — Device info data
parseBatteryStateData          — Battery state
parseBatteryTemperatureInfo    — Battery temperature
parseBatteryValueData          — Battery values
parseChargeStateInfo           — Charge state
parseChargeModeInfo            — Charge mode
parseChargeOptimizationInfo    — Charge optimization
parseACTimedTaskInfo           — AC timed task
parseCountDownInfo             — Countdown info
parseCountDownTime             — Countdown time
parseCountDownTimeAC           — AC countdown time
parseCountDownTimeDC           — DC countdown time
parseSwitchStateInfo           — Switch state
parseSwitchStateInfoAC         — AC switch state
parseSwitchStateInfoDC         — DC switch state
parseScreenBrightnessInfo      — Screen brightness
parseScreenOffTimeInfo         — Screen off time
parseScreensaverInformationInfo — Screensaver info
parseAlwaysOnDisplayInfo       — Always-on display
parseInputPowerData            — Input power
parseOutputPowerData           — Output power
parseErrorCodeData             — Error codes
parseFaultInfo                 — Fault info
parseVoltageProtectionData     — Voltage protection
parseAntiBackFlowData          — Anti-backflow data
parsePeakAndValleyRes          — Peak & valley response
parseUtilityPlanData           — Utility plan data
parseTlvData                   — TLV data parser
parseCommonData                — Common data parser
parseCommonDataWithDataType    — Data with type byte
parseCapabilitiesNegotiationResponse — Capabilities negotiation
```

## Key Solarbank 3 (A17C5) Features
Based on asset/image names and methods:
- Grid feed-in control (`setFeedGridSwitch`, `setFeedGridValue`, `setFeedNetworkLimitValue`)
- Off-grid mode (`setOffGridSwitch`, `imgA17C5SettingsOffGrid`)
- AI EMS mode (`sendDeviceCommandOfAIEmsMode`, `ai_mode_condition`)
- Home load management (`setHomeLoadValue`, `setHomeLoadWeek`)
- Peak shaving (`setPeakShaving`, `sendDeviceCommandOfPeckShaving`)
- Time-of-Use scheduling (`setTOU`, `sendDeviceCommandOfTou`)
- Power export limit (`Export Limit`, `softwarePowerExportLimitInput`)
- Self-consumption mode (`setSelfConsumption`)
- Third-party PV support (`setThreePvInstallSwitch`)
- Sleep time control (`sleep_time_ic.png`)
- Remaining power management (`remaining_add_device.png`)
- Smart mode (`icl_station_settings_mode_smart.png`)

## Key C1000 (A1753/A1761) Features
- AC/DC output toggle (`setAcSwitch`, `setDCOutputSwitch`)
- Countdown timers (`setAcCountDownTime`, `setDCPortCountdownTask`)
- Timed tasks (`setACPortTimedTask`, `setDCPortTimedTask`)
- Screen settings (`setScreenBrightness`, `setScreenTimeout`)
- Light modes (`setAcLightMode`, `setAmbientLight`)
- Boost mode (`a1753SetBoost`)
- Ultra-fast charge (`a1753UltraFastCharge`)
- Unit timeout (`a1753SetUnitTimeout`)
- Memory port (`setMemoryPortValue`)
- Port priority (`setPortPriority`)
- Car charger mode (`setCarSwitch`, `setCarCountdown`)

## Protocol Details
- BLE write via: `akiot.ble.write_characteristic`
- Payload helper: `BleWritePayloadHelper`
- Command assembly: `assemble_command_util.dart`
- TLV parsing: `parseTlvData`, `parseCommonDataWithDataType`
- Debug logging: `[decodeResponse] decryptedPayload ===`
