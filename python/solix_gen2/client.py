"""Async Bleak client for local SOLIX Gen 2 telemetry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import inspect
from typing import Any

from .protocol import COMMAND_UUID, TELEMETRY_UUID, Model, Session

UpdateCallback = Callable[[dict[str, int | str]], Any]


async def discover(timeout: float = 5.0) -> list[Any]:
    """Return nearby C1000 Gen 2 and C2000 Gen 2 BLE devices."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout)
    devices = []
    for device in found:
        try:
            Model.from_name(device.name)
        except ValueError:
            continue
        devices.append(device)
    return devices


class SolixMonitor:
    """Keep a BLE connection and publish telemetry updates.

    Pass a Bleak ``BLEDevice`` from :func:`discover`, or pass its address and
    specify ``model=Model.C1000_GEN2`` or ``Model.C2000_GEN2``. The callback
    receives a fresh copy of the latest decoded metrics on each update.
    """

    def __init__(
        self,
        device: Any,
        *,
        model: Model | None = None,
        owner_user_id: str | None = None,
        protocol: str = "prime",
        timezone_name: str | None = None,
        on_update: UpdateCallback | None = None,
    ) -> None:
        self.device = device
        self.model = model or Model.from_name(getattr(device, "name", None))
        self.protocol = protocol
        self.timezone_name = timezone_name
        self.metrics: dict[str, int | str] = {}
        self.raw_tlvs: dict[int, bytes] = {}
        self._session = Session(self.model, owner_user_id, protocol=self.protocol,
                                timezone_name=self.timezone_name)
        self.owner_user_id = self._session.owner_user_id
        self._client: Any = None
        self._ready = asyncio.Event()
        self.pairing_required = asyncio.Event()
        self._negotiation_done = asyncio.Event()
        self._connect_error: Exception | None = None
        self._lock = asyncio.Lock()
        self._callbacks: list[UpdateCallback] = [on_update] if on_update else []
        self._updates: asyncio.Queue[dict[str, int | str]] = asyncio.Queue(maxsize=10)
        self._responses: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=20)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected and self._ready.is_set())

    def add_callback(self, callback: UpdateCallback) -> Callable[[], None]:
        """Register an update callback; call the returned function to remove it."""
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    async def connect(self, timeout: float = 90.0) -> None:
        from bleak import BleakClient

        if self._client is not None:
            raise RuntimeError("Monitor is already connected")
        self._client = BleakClient(self.device)
        try:
            await self._client.connect()
            await self._client.start_notify(TELEMETRY_UUID, self._notification)
            await self._client.write_gatt_char(COMMAND_UUID, self._session.start(), response=False)
            await asyncio.wait_for(self._negotiation_done.wait(), timeout)
            if self._connect_error:
                raise self._connect_error
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._ready.clear()
        self.pairing_required.clear()
        self._negotiation_done.clear()
        self._connect_error = None
        while not self._responses.empty():
            self._responses.get_nowait()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if client is not None:
            if client.is_connected:
                await client.stop_notify(TELEMETRY_UUID)
                await client.disconnect()
        self._session = Session(self.model, self.owner_user_id, protocol=self.protocol,
                                timezone_name=self.timezone_name)

    async def __aenter__(self) -> SolixMonitor:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    async def wait_for_update(self, timeout: float | None = None) -> dict[str, int | str]:
        """Wait for the next telemetry update, useful in polling integrations."""
        return await asyncio.wait_for(self._updates.get(), timeout)

    async def request_status(self) -> None:
        """Refresh the Gen 2 telemetry subscription."""
        if not self.connected:
            raise RuntimeError("Monitor is not connected")
        await self._client.write_gatt_char(
            COMMAND_UUID, self._session.send_command("4100", b"\xa1\x01\x21"), response=False
        )

    async def _wait_for_response(self, command: str, timeout: float = 20) -> bytes:
        async with asyncio.timeout(timeout):
            while True:
                received, payload = await self._responses.get()
                if received == command:
                    return payload

    async def join_wifi(self, *, ssid: str, passphrase: str, account_id: str) -> str:
        """Send the observed C1000/C2000 Wi-Fi credentials write and return its BLE reply.

        A reply of ``00`` was followed by WPA2 association and DHCP on the
        tested units. Joining an AP does not complete cloud setup or activate
        Time-of-Use on the tested C2000.
        """
        if not self.connected:
            raise RuntimeError('Monitor is not connected')
        packet = self._session.wifi_credentials_packet(ssid, passphrase, account_id)
        while not self._responses.empty():
            self._responses.get_nowait()
        await self._client.write_gatt_char(COMMAND_UUID, packet, response=False)
        try:
            response = await self._wait_for_response('4824', timeout=12)
        except TimeoutError:
            return 'timeout'
        return response.hex()

    async def send_wifi_provisioning(
        self, *, ssid: str, passphrase: str, account_id: str,
        api_url: str, posix_timezone: str, iana_timezone: str,
        c3_value: str = 'A2', allow_http: bool = False,
    ) -> dict[str, str]:
        """Send experimental C1000 Wi-Fi writes; return raw BLE acknowledgements.

        Acknowledgements do not establish cloud registration or control.
        Credentials are never included in the returned value.
        """
        if not self.connected:
            raise RuntimeError('Monitor is not connected')
        cloud = self._session.wifi_cloud_config_packet(
            account_id, api_url, posix_timezone, iana_timezone,
            c3_value=c3_value, allow_http=allow_http,
        )
        first = await self.join_wifi(ssid=ssid, passphrase=passphrase, account_id=account_id)
        if first not in ('00', 'timeout'):
            return {'4824': first}
        await self._client.write_gatt_char(COMMAND_UUID, cloud, response=False)
        try:
            second = await self._wait_for_response('4825', timeout=12)
        except TimeoutError:
            second = None
        return {'4824': first,
                '4825': second.hex() if second is not None else 'timeout'}

    async def _write_setting(self, packet: bytes, expected: dict[str, int]) -> dict[str, int | str]:
        if not self.connected:
            raise RuntimeError("Monitor is not connected")
        while not self._updates.empty():
            self._updates.get_nowait()
        await self._client.write_gatt_char(COMMAND_UUID, packet, response=False)
        for _ in range(4):
            await asyncio.sleep(0.5)
            await self.request_status()
            try:
                await self.wait_for_update(timeout=5)
            except TimeoutError:
                continue
            if all(self.metrics.get(name) == value for name, value in expected.items()):
                return self.metrics.copy()
        raise TimeoutError(f"Station did not report the requested setting: {expected}")

    async def set_charge_limits(self, upper: int, lower: int) -> dict[str, int | str]:
        """Set C1000 Gen 2 Prime charging and discharge limits, then confirm telemetry."""
        packet = self._session.charge_limits_packet(upper, lower)
        return await self._write_setting(packet, {
            "max_charge_percentage": upper,
            "min_charge_percentage": lower,
        })

    async def set_charge_cap(self, upper: int) -> dict[str, int | str]:
        """Set the C2000 upper charging limit and verify its lower limit and AC state."""
        packet = self._session.charge_cap_packet(upper)
        if not self.connected:
            raise RuntimeError('Monitor is not connected')
        if 'min_charge_percentage' not in self.metrics or 'ac_output_enabled' not in self.metrics:
            await self.request_status()
            await self.wait_for_update(timeout=10)
        lower = self.metrics.get('min_charge_percentage')
        ac_output = self.metrics.get('ac_output_enabled')
        if type(lower) is not int or ac_output not in (0, 1):
            raise RuntimeError('C2000 baseline is missing the lower limit or AC output state')
        return await self._write_setting(packet, {
            'max_charge_percentage': upper,
            'min_charge_percentage': lower,
            'ac_output_enabled': ac_output,
        })

    async def set_ac_charging_power(self, watts: int) -> dict[str, int | str]:
        """Set Gen 2 Prime AC charging power, then confirm telemetry."""
        packet = self._session.ac_charging_power_packet(watts)
        return await self._write_setting(packet, {"ac_charging_power_limit_w": watts})

    async def set_display_timeout(self, seconds: int) -> dict[str, int | str]:
        """Set Gen 2 display timeout and confirm telemetry (C2000: 30/60 s only)."""
        packet = self._session.display_timeout_packet(seconds)
        return await self._write_setting(packet, {"display_timeout_seconds": seconds})

    async def set_fast_charge_enabled(self, enabled: bool) -> dict[str, int | str]:
        """Set C1000 fast charge switch and confirm the reported value."""
        packet = self._session.fast_charge_packet(enabled)
        return await self._write_setting(packet, {"ac_fast_charge_enabled": int(enabled)})

    async def confirm_pairing(self) -> None:
        """Retry Prime registration after one short main power button press."""
        if not self._client or not self._client.is_connected or not self.pairing_required.is_set():
            raise RuntimeError("Station is not awaiting pairing confirmation")
        self.pairing_required.clear()
        await self._client.write_gatt_char(COMMAND_UUID, self._session.retry_registration(), response=False)

    def _notification(self, _sender: Any, data: bytearray) -> None:
        task = asyncio.create_task(self._handle(bytes(data)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, data: bytes) -> None:
        async with self._lock:
            try:
                update = self._session.feed(data)
            except Exception as error:
                self._connect_error = error
                self._negotiation_done.set()
                return
            for packet in update.outgoing:
                if self._client is not None:
                    await self._client.write_gatt_char(COMMAND_UUID, packet, response=False)
            if update.ready:
                self._ready.set()
                self._negotiation_done.set()
            if update.pairing_required:
                self.pairing_required.set()
            if update.telemetry is not None:
                self.metrics.update(update.telemetry)
                self.raw_tlvs = update.raw_tlvs or {}
                snapshot = self.metrics.copy()
                if self._updates.full():
                    self._updates.get_nowait()
                self._updates.put_nowait(snapshot)
                for callback in tuple(self._callbacks):
                    result = callback(snapshot)
                    if inspect.isawaitable(result):
                        await result
            if update.response is not None:
                if self._responses.full():
                    self._responses.get_nowait()
                self._responses.put_nowait(update.response)
