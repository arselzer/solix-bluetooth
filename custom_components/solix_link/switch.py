"""C1000 Gen 2 native AC off-grid alert preference; no output switches."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import binary_state, native_gen2
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
COMMAND = "set-off-grid-alert"
METRIC = "ac_off_grid_alert_enabled"


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        if not coordinator.client.token:
            return
        for name, snapshot in (coordinator.data or {}).items():
            if (name not in added and snapshot.get("model") == "c1000_gen2"
                    and native_gen2(snapshot) and COMMAND in snapshot.get("controls", [])
                    and binary_state(snapshot.get("metrics", {}).get(METRIC)) is not None):
                added.add(name)
                entities.append(OffGridAlertSwitch(coordinator, name, METRIC))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class OffGridAlertSwitch(SolixEntity, SwitchEntity):
    _attr_translation_key = "off_grid_alert"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        return binary_state(self.snapshot.get("metrics", {}).get(METRIC))

    @property
    def available(self) -> bool:
        return (self.snapshot.get("model") == "c1000_gen2" and native_gen2(self.snapshot)
                and self.control_available(COMMAND) and self.is_on is not None)

    async def _set_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise HomeAssistantError("Use an explicit enabled or disabled alert setting")
        if not self.available:
            raise HomeAssistantError("Fresh connected telemetry and an enabled gateway control are required")
        await self.coordinator.async_command(self.station_name, {"command": COMMAND, "enabled": enabled})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_enabled(False)
