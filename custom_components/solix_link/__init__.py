"""Home Assistant integration for a separately running SOLIX Link gateway."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import GatewayClient, device_id
from .const import CONF_TOKEN, CONF_URL, DOMAIN
from .coordinator import SolixConfigEntry, SolixCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def set_tou_plan(call: ServiceCall) -> None:
        device = dr.async_get(hass).async_get(call.data["device_id"])
        if device is not None:
            for entry in hass.config_entries.async_entries(DOMAIN):
                coordinator = getattr(entry, "runtime_data", None)
                if (entry.state is not ConfigEntryState.LOADED
                        or entry.entry_id not in device.config_entries
                        or not isinstance(coordinator, SolixCoordinator)):
                    continue
                for name in coordinator.data or {}:
                    if (DOMAIN, device_id(coordinator.endpoint_id, name)) in device.identifiers:
                        await coordinator.async_command(name, {
                            "command": "set-tou-plan", "periods": call.data["periods"],
                            "enabled": call.data["enabled"],
                        })
                        return
        raise HomeAssistantError("Select a loaded SOLIX Link device")

    hass.services.async_register(DOMAIN, "set_tou_plan", set_tou_plan, schema=vol.Schema({
        vol.Required("device_id"): str,
        vol.Required("periods"): [dict],
        vol.Required("enabled"): bool,
    }))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SolixConfigEntry) -> bool:
    client = GatewayClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_TOKEN, ""))
    coordinator = SolixCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolixConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
