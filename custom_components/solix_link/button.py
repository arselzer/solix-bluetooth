"""Return to grid, with the gateway checking actual power flow."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback

from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        if not coordinator.client.token:
            return
        for name, snapshot in (coordinator.data or {}).items():
            if (name not in added and "return-grid" in snapshot["controls"]
                    and snapshot["model"] == "c2000_gen2" and snapshot["protocol"] == "native_mqtt"):
                added.add(name)
                entities.append(ReturnGridButton(coordinator, name, "return_grid"))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class ReturnGridButton(SolixEntity, ButtonEntity):
    _attr_translation_key = "return_grid"

    @property
    def available(self) -> bool:
        return self.control_available("return-grid")

    async def async_press(self) -> None:
        await self.coordinator.async_command(self.station_name, {"command": "return-grid", "timeout": 30})
