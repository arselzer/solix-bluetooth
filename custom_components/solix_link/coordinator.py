"""One coordinated local HTTP poll for all station entities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GatewayAuthError, GatewayClient, GatewayError, gateway_id
from .const import DOMAIN, POLL_SECONDS

_LOGGER = logging.getLogger(__name__)


class SolixCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: GatewayClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=POLL_SECONDS), always_update=False)
        self.client = client
        self.endpoint_id = entry.unique_id or gateway_id(client.url)
        self._io_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            async with self._io_lock:
                return await self.client.async_devices()
        except GatewayAuthError as err:
            raise ConfigEntryAuthFailed("Gateway authentication failed") from err
        except GatewayError as err:
            raise UpdateFailed(str(err)) from err

    async def async_command(self, name: str, payload: dict) -> None:
        """Serialize commands and polling, then publish confirmed or refreshed data."""
        error = None
        async with self._io_lock:
            data = dict(self.data or {})
            try:
                data[name] = await self.client.async_command(name, payload)
            except (GatewayError, ValueError) as err:
                error = err
                try:
                    data[name] = await self.client.async_device(name)
                except GatewayError:
                    if name in data:
                        data[name] = {**data[name], "connected": False, "available": False}
            self.async_set_updated_data(data)
        if error is not None:
            raise HomeAssistantError(str(error)) from error


SolixConfigEntry = ConfigEntry[SolixCoordinator]
