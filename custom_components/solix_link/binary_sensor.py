"""Mains presence and output state from fresh gateway telemetry."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import callback
from homeassistant.const import EntityCategory

from .api import binary_state
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
DESCRIPTIONS = (
    BinarySensorEntityDescription(key="ac_input_connected", translation_key="mains_present",
                                  device_class=BinarySensorDeviceClass.POWER),
    BinarySensorEntityDescription(key="ac_output_enabled", translation_key="ac_output_enabled",
                                  device_class=BinarySensorDeviceClass.POWER),
    BinarySensorEntityDescription(key="dc_input_active", translation_key="dc_input_active",
                                  entity_category=EntityCategory.DIAGNOSTIC,
                                  entity_registry_enabled_default=False),
)


async def async_setup_entry(hass, entry: SolixConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover() -> None:
        entities = []
        for name, snapshot in (coordinator.data or {}).items():
            for description in DESCRIPTIONS:
                key = description.key
                if key in snapshot["metrics"] and (name, key) not in added:
                    added.add((name, key))
                    entities.append(SolixBinarySensor(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixBinarySensor(SolixEntity, BinarySensorEntity):
    def __init__(self, coordinator, name, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return binary_state(self.snapshot.get("metrics", {}).get(self.entity_description.key))

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None
