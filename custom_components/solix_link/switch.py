"""Capability-gated C1000 configuration switches confirmed by the gateway."""

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import binary_state, boolean_setting_supported, fast_charge_enable_allowed, native_gen2
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
SETTINGS = {"ac_off_grid_alert_enabled": "set-off-grid-alert", "ac_fast_charge_enabled": "set-fast-charge",
            "ac_power_saving_mode_enabled": "set-ac-power-saving", "dc_power_saving_mode_enabled": "set-dc-power-saving",
            "port_memory_enabled": "set-port-memory"}
DESCRIPTIONS = (
    SwitchEntityDescription(key="ac_off_grid_alert_enabled", translation_key="off_grid_alert", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="ac_fast_charge_enabled", translation_key="fast_charge", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="ac_power_saving_mode_enabled", translation_key="ac_power_saving", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="dc_power_saving_mode_enabled", translation_key="dc_power_saving", entity_category=EntityCategory.CONFIG),
    SwitchEntityDescription(key="port_memory_enabled", translation_key="port_memory", entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        if not coordinator.client.token:
            return
        for name, snapshot in (coordinator.data or {}).items():
            for description in DESCRIPTIONS:
                key = description.key
                if (name, key) not in added and boolean_setting_supported(snapshot, SETTINGS[key]):
                    added.add((name, key))
                    entities.append(SolixSettingSwitch(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixSettingSwitch(SolixEntity, SwitchEntity):
    def __init__(self, coordinator, name, description: SwitchEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        self.command = SETTINGS[description.key]
        if self.command in ("set-ac-power-saving", "set-dc-power-saving"):
            self._attr_extra_state_attributes = {"output_behavior": "Power saving may automatically turn the output off at low load."}
        elif self.command == "set-port-memory":
            self._attr_extra_state_attributes = {"recovery_behavior": "Off clears output-recovery bookkeeping; turning On does not restore that transient state."}
        elif self.command == "set-fast-charge" and self.snapshot.get("model") == "c1000_gen2":
            self._attr_extra_state_attributes = {"enable_requirement": "C1000 Gen 2 requires Standard mode with no active tariff; native MQTT also requires connected mains."}

    @property
    def is_on(self) -> bool | None:
        return binary_state(self.snapshot.get("metrics", {}).get(self.entity_description.key))

    @property
    def available(self) -> bool:
        if self.command == "set-fast-charge" and native_gen2(self.snapshot):
            if binary_state(self.snapshot.get("metrics", {}).get("ac_input_connected")) is not True:
                return False
        return boolean_setting_supported(self.snapshot, self.command) and self.control_available(self.command)

    async def _set_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise HomeAssistantError("Use an explicit enabled or disabled setting")
        if not self.available:
            raise HomeAssistantError("Fresh connected telemetry and an enabled gateway control are required")
        if self.command == "set-fast-charge" and enabled and not fast_charge_enable_allowed(self.snapshot):
            raise HomeAssistantError("Fast charge enable requires Standard mode with no active tariff")
        await self.coordinator.async_command(self.station_name, {"command": self.command, "enabled": enabled})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_enabled(False)
