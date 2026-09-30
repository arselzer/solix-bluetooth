"""Discrete C1000 settings confirmed through the gateway."""

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import DEVICE_TIMEOUT_OPTIONS, binary_state, device_timeout_options, discharge_floor_options, native_gen2
from .coordinator import SolixConfigEntry
from .entity import SolixEntity

PARALLEL_UPDATES = 0
SETTINGS = {
    "temperature_unit_fahrenheit": "set-temperature-unit",
    "min_charge_percentage": "set-discharge-floor",
    "device_timeout_minutes": "set-device-timeout",
}
DESCRIPTIONS = (
    SelectEntityDescription(key="temperature_unit_fahrenheit", translation_key="temperature_unit",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="min_charge_percentage", translation_key="discharge_floor",
                            entity_category=EntityCategory.CONFIG),
    SelectEntityDescription(key="device_timeout_minutes", translation_key="device_timeout",
                            entity_category=EntityCategory.CONFIG),
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
                if key != "device_timeout_minutes" and (snapshot.get("model") != "c1000_gen2" or not native_gen2(snapshot)):
                    continue
                if (name, key) in added or SETTINGS[key] not in snapshot.get("controls", []):
                    continue
                if key == "device_timeout_minutes":
                    reported = bool(device_timeout_options(snapshot))
                else:
                    reported = (binary_state(snapshot.get("metrics", {}).get(key)) is not None
                                if key == "temperature_unit_fahrenheit" else bool(discharge_floor_options(snapshot)))
                if reported:
                    added.add((name, key))
                    entities.append(SolixSelect(coordinator, name, description))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class SolixSelect(SolixEntity, SelectEntity):
    def __init__(self, coordinator, name, description: SelectEntityDescription) -> None:
        super().__init__(coordinator, name, description.key)
        self.entity_description = description
        self.command = SETTINGS[description.key]
        if description.key == "device_timeout_minutes":
            self._attr_extra_state_attributes = {
                "timeout_behavior": "Never disables this timeout; other sleep behavior may still interrupt remote access.",
                "finite_timeout_behavior": "The station may turn off when idle, interrupting remote access.",
            }

    @property
    def options(self) -> list[str]:
        if self.entity_description.key == "device_timeout_minutes":
            return device_timeout_options(self.snapshot)
        if self.entity_description.key == "temperature_unit_fahrenheit":
            return ["celsius", "fahrenheit"]
        return discharge_floor_options(self.snapshot)

    @property
    def current_option(self) -> str | None:
        key = self.entity_description.key
        value = self.snapshot.get("metrics", {}).get(key)
        if key == "device_timeout_minutes":
            return next((option for option in self.options if DEVICE_TIMEOUT_OPTIONS[option] == value), None)
        if key == "temperature_unit_fahrenheit":
            state = binary_state(value)
            return None if state is None else "fahrenheit" if state else "celsius"
        option = f"{value}%" if type(value) is int else None
        return option if option in self.options else None

    @property
    def available(self) -> bool:
        supported = (bool(device_timeout_options(self.snapshot)) if self.entity_description.key == "device_timeout_minutes"
                     else self.snapshot.get("model") == "c1000_gen2" and native_gen2(self.snapshot))
        return supported and self.control_available(self.command) and self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        if not isinstance(option, str) or option not in self.options:
            raise HomeAssistantError("Choose one of the currently available setting options")
        if not self.available:
            raise HomeAssistantError("Fresh connected telemetry and an enabled gateway control are required")
        if self.entity_description.key == "device_timeout_minutes":
            payload = {"command": self.command, "minutes": DEVICE_TIMEOUT_OPTIONS[option]}
        elif self.entity_description.key == "temperature_unit_fahrenheit":
            payload = {"command": self.command, "fahrenheit": option == "fahrenheit"}
        else:
            payload = {"command": self.command, "lower": int(option.removesuffix("%"))}
        await self.coordinator.async_command(self.station_name, payload)
