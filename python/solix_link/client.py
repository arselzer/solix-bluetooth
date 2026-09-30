"""Async Bleak client for local SOLIX telemetry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import inspect
from typing import Any

from .protocol import COMMAND_UUID, TELEMETRY_UUID, Model, Session, decode_telemetry, parse_packet, tlv
from .diagnostics import decode_network_diagnostics

UpdateCallback = Callable[[dict[str, int | str]], Any]


async def discover(timeout: float = 5.0) -> list[Any]:
    """Return supported stations, including original C1000."""
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
    specify a supported ``Model``. C300/C300X AC and original C1000 default
    to legacy without an owner ID; Gen 2 defaults to Prime. The callback
    receives a fresh copy of the latest decoded metrics on each update.
    """

    def __init__(
        self,
        device: Any,
        *,
        model: Model | None = None,
        owner_user_id: str | None = None,
        protocol: str | None = None,
        timezone_name: str | None = None,
        on_update: UpdateCallback | None = None,
    ) -> None:
        self.device = device
        self.model = model or Model.from_name(getattr(device, "name", None))
        self.protocol = self.model.resolve_protocol(protocol)
        self.timezone_name = timezone_name
        self.metrics: dict[str, int | str] = {}
        self._telemetry_revision = 0
        self._field_revision: dict[str, int] = {}
        self.raw_tlvs: dict[int, bytes] = {}
        self._raw_tlv_revision: dict[int, int] = {}
        self._session = Session(self.model, owner_user_id, protocol=self.protocol,
                                timezone_name=self.timezone_name)
        self.owner_user_id = self._session.owner_user_id
        self._client: Any = None
        self._ready = asyncio.Event()
        self.pairing_required = asyncio.Event()
        self._negotiation_done = asyncio.Event()
        self._connect_error: Exception | None = None
        self._lock = asyncio.Lock()
        self._diagnostic_lock = asyncio.Lock()
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
        """Request fresh telemetry using the model's status command."""
        if not self.connected:
            raise RuntimeError("Monitor is not connected")
        await self._client.write_gatt_char(
            COMMAND_UUID, self._session.status_packet(), response=False
        )

    async def _wait_for_response(self, command: str, timeout: float = 20) -> bytes:
        async with asyncio.timeout(timeout):
            while True:
                received, payload = await self._responses.get()
                if received == command:
                    return payload

    async def network_diagnostics(self, timeout: float = 20) -> dict[str, int]:
        """Read radio error codes without changing power or network settings.

        Do not call during Wi-Fi provisioning, which shares the reply queue.
        Reset codes can become 255 after a read; zero errors do not prove a
        successful MQTT connection. These are not battery/inverter fault codes.
        """
        async with self._diagnostic_lock:
            if not self.connected:
                raise RuntimeError("Monitor is not connected")
            packet = self._session.network_diagnostics_packet()
            while not self._responses.empty():
                self._responses.get_nowait()
            await self._client.write_gatt_char(COMMAND_UUID, packet, response=False)
            payload = await self._wait_for_response("4820", timeout=timeout)
            return decode_network_diagnostics(payload)

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
        c3_value: str = 'A2', allow_http: bool = False, country_code: str = 'US',
    ) -> dict[str, str]:
        """Send model-specific Wi-Fi writes; return raw BLE acknowledgements.

        Acknowledgements do not establish cloud registration or control.
        Credentials are never included in the returned value.
        """
        if not self.connected:
            raise RuntimeError('Monitor is not connected')
        cloud = self._session.wifi_cloud_config_packet(
            account_id, api_url, posix_timezone, iana_timezone,
            c3_value=c3_value, allow_http=allow_http, country_code=country_code,
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

    async def _write_setting(self, packet: bytes, expected: dict[str, int | str]) -> dict[str, int | str]:
        if not self.connected:
            raise RuntimeError("Monitor is not connected")
        while not self._updates.empty():
            self._updates.get_nowait()
        while not self._responses.empty():
            self._responses.get_nowait()
        command = parse_packet(packet).command
        reply_command = bytes((command[0] | 0x08, command[1])).hex()
        baseline_revision = self._telemetry_revision

        def check_reply() -> None:
            while not self._responses.empty():
                received, payload = self._responses.get_nowait()
                if received == reply_command and (not payload or payload[0] != 0):
                    code = payload[:1].hex() if payload else "empty"
                    raise RuntimeError(f"Station rejected setting ({received}, status {code})")

        await self._client.write_gatt_char(COMMAND_UUID, packet, response=False)
        for _ in range(4):
            await asyncio.sleep(0.5)
            check_reply()
            await self.request_status()
            try:
                await self.wait_for_update(timeout=5)
            except TimeoutError:
                check_reply()
                continue
            check_reply()
            if all(self._field_revision.get(name, 0) > baseline_revision
                   and self.metrics.get(name) == value for name, value in expected.items()):
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
        """Set the AC charging-power limit, then confirm fresh telemetry."""
        packet = self._session.ac_charging_power_packet(watts)
        if self.model == Model.C1000_GEN2:
            return await self._set_gen2_configuration(
                packet, "ac_charging_power_limit_w", watts, 5, allow_display_activity=True)
        return await self._write_setting(packet, {"ac_charging_power_limit_w": watts})

    async def set_ac_output_enabled(self, enabled: bool) -> dict[str, int | str]:
        """Set C300/original C1000 AC output and confirm; unavailable on C2000."""
        packet = self._session.ac_output_packet(enabled)
        return await self._write_setting(packet, {"ac_output_enabled": int(enabled)})

    async def set_light_mode(self, mode: int) -> dict[str, int | str]:
        """Set C300/original C1000 light mode (0..4), then confirm telemetry."""
        packet = self._session.light_mode_packet(mode)
        return await self._write_setting(packet, {"light_mode": mode})

    async def set_device_timeout(self, minutes: int) -> dict[str, int | str]:
        """Set C1000 idle timeout, preserving outputs and charging configuration.

        Zero means Never for this setting. Independent sleep behavior and
        previously queued events may still affect access. Require fresh baseline
        and readback even for an idempotent write.
        """
        packet = self._session.device_timeout_packet(minutes)  # Validate before I/O.
        if not self.connected:
            raise RuntimeError("Monitor is not connected")
        if self.model == Model.C1000_GEN2:
            return await self._set_gen2_configuration(packet, "device_timeout_minutes", minutes, 14)
        required = ("device_timeout_minutes", "ac_output_enabled", "dc_output_enabled",
                    "ac_charging_power_limit_w")
        revision = self._telemetry_revision
        await self.request_status()
        async with asyncio.timeout(10):
            while not all(self._field_revision.get(key, 0) > revision for key in required):
                await self.wait_for_update(timeout=10)
        if (any(type(self.metrics.get(key)) is not int for key in required)
                or any(self.metrics[key] not in (0, 1) for key in required[1:3])):
            raise RuntimeError("Missing valid fresh Device Timeout baseline; no write sent")
        protected = ("ac_output_enabled", "dc_output_enabled", "ac_charging_power_limit_w",
                     "ac_fast_charge_enabled", "max_charge_percentage", "min_charge_percentage",
                     "backup_reserve_percentage", "port_memory_enabled", "display_timeout_seconds",
                     "usage_mode", "tou_schedule_slot_count")
        expected = {key: self.metrics[key] for key in protected
                    if type(self.metrics.get(key)) in (int, str) and self._field_revision.get(key, 0) > revision}
        expected["device_timeout_minutes"] = minutes
        return await self._write_setting(packet, expected)

    async def _set_gen2_configuration(
        self, packet: bytes, metric: str, value: int, offset: int, *, allow_display_activity: bool = False,
    ) -> dict[str, int | str]:
        if not self.connected:
            raise RuntimeError("Monitor is not connected")
        before_a4, before_d9, before = await self._fresh_gen2_configuration()
        protected = ("ac_output_enabled", "dc_output_enabled", "ac_input_connected")
        expected = {key: before[key] for key in protected}
        expected[metric] = value
        await self._write_setting(packet, expected)
        a4, d9, metrics = await self._fresh_gen2_configuration()
        expected_a4 = bytearray(before_a4)
        expected_a4[offset:offset + 2] = value.to_bytes(2, "little")
        if allow_display_activity:
            expected_a4[22] = a4[22]
        for start in (1, 9):
            if int.from_bytes(a4[start:start + 4], "little") > int.from_bytes(before_a4[start:start + 4], "little"):
                raise RuntimeError("Output timer changed during setting confirmation")
            expected_a4[start:start + 4] = a4[start:start + 4]
        if metrics[metric] != value:
            raise RuntimeError("Setting not confirmed; settings may have changed")
        if (a4 != bytes(expected_a4) or d9[2:] != before_d9[2:]
                or any(metrics[key] != before[key] for key in protected)):
            raise RuntimeError("Protected setting changed; settings may have changed")
        return self.metrics.copy()

    async def _fresh_gen2_configuration(self) -> tuple[bytes, bytes, dict[str, int | str]]:
        """Require one complete fresh C1000 Gen 2 configuration, not cached TLVs."""
        from .tou import periods_from_d9
        revision = self._telemetry_revision
        await self.request_status()
        tags = (0xA4, 0xD9, 0xA7, 0xB2)
        async with asyncio.timeout(10):
            while not (all(self._raw_tlv_revision.get(tag, 0) > revision for tag in tags)
                       and all(tag in self.raw_tlvs for tag in tags)):
                await self.wait_for_update(timeout=10)
        values = self.raw_tlvs.copy()
        a4, d9 = values[0xA4], values[0xD9]
        if len(a4) != 34 or a4[0] != 4:
            raise RuntimeError("Missing complete C1000 configuration baseline")
        periods_from_d9(d9)
        if any(len(values[tag]) < size or values[tag][0] != 4 for tag, size in ((0xA7, 5), (0xB2, 4))):
            raise RuntimeError("Missing valid C1000 output baseline")
        metrics, _ = decode_telemetry(b"".join(tlv(tag, value) for tag, value in values.items()), self.model)
        for key in ("ac_output_enabled", "dc_output_enabled", "ac_input_connected", "display_enabled"):
            if type(metrics.get(key)) is not int or metrics[key] not in (0, 1):
                raise RuntimeError("Missing valid C1000 output baseline")
        return a4, d9, metrics

    async def set_display_timeout(self, seconds: int) -> dict[str, int | str]:
        """Set display timeout and confirm telemetry (C300/C2000: 30/60 s only)."""
        packet = self._session.display_timeout_packet(seconds)
        return await self._write_setting(packet, {"display_timeout_seconds": seconds})

    async def set_c1000_setting(self, setting: str, value: int | bool) -> dict[str, int | str]:
        """Apply an original C1000 control and require fresh readback.

        Display, brightness, timeout, light, power and AC/DC switches were
        verified on A1761 version code 151. Record and restore baselines when
        testing; a timeout does not mean the write was ignored.
        """
        if setting == "device_timeout":
            return await self.set_device_timeout(value)
        if setting in ("temperature_unit_fahrenheit", "fast_charge_enabled",
                       "ac_power_saving_mode_enabled", "dc_power_saving_mode_enabled"):
            return await self._set_original_configuration(setting, value)
        from .c1000 import c1000_setting
        packet = self._session.c1000_control_packet(setting, value)
        _command, _payload, expected = c1000_setting(setting, value)
        return await self._write_setting(packet, expected)

    async def set_fast_charge_enabled(self, enabled: bool) -> dict[str, int | str]:
        """Set C1000 fast charge switch and confirm the reported value."""
        if self.model == Model.C1000:
            return await self._set_original_configuration("fast_charge_enabled", enabled)
        packet = self._session.fast_charge_packet(enabled)
        return await self._write_setting(packet, {"ac_fast_charge_enabled": int(enabled)})

    async def set_temperature_unit(self, fahrenheit: bool) -> dict[str, int | str]:
        return await self._set_original_configuration("temperature_unit_fahrenheit", fahrenheit)

    async def set_ac_power_saving_enabled(self, enabled: bool) -> dict[str, int | str]:
        """Select original C1000 Normal/Smart; Smart may turn outputs off at low load."""
        return await self._set_original_configuration("ac_power_saving_mode_enabled", enabled)

    async def set_dc_power_saving_enabled(self, enabled: bool) -> dict[str, int | str]:
        return await self._set_original_configuration("dc_power_saving_mode_enabled", enabled)

    async def _set_original_configuration(self, setting: str, value: bool) -> dict[str, int | str]:
        from .c1000 import c1000_setting
        packet = self._session.c1000_control_packet(setting, value)
        _command, _body, target = c1000_setting(setting, value)
        required = ("ac_output_enabled", "dc_output_enabled", "ac_charging_power_limit_w",
                    "device_timeout_minutes", "display_timeout_seconds", "display_brightness", "light_mode",
                    "temperature_unit_fahrenheit", "ac_fast_charge_enabled", "ac_power_saving_mode_enabled",
                    "dc_power_saving_mode_enabled")
        revision = self._telemetry_revision
        await self.request_status()
        async with asyncio.timeout(10):
            while not all(self._field_revision.get(key, 0) > revision for key in required):
                await self.wait_for_update(timeout=10)
        before = {key: self.metrics[key] for key in required}
        if (any(type(before[key]) is not int for key in required)
                or any(before[key] not in (0, 1) for key in required if key.endswith("_enabled") or key == "temperature_unit_fahrenheit")):
            raise RuntimeError("Invalid original C1000 settings baseline; no write sent")
        expected = {**before, **target}
        return await self._write_setting(packet, expected)

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
                self._telemetry_revision += 1
                self._field_revision.update({name: self._telemetry_revision for name in update.telemetry})
                self.metrics.update(update.telemetry)
                self.raw_tlvs = update.raw_tlvs or {}
                self._raw_tlv_revision.update({tag: self._telemetry_revision for tag in self.raw_tlvs})
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
