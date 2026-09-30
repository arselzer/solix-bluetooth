"""Prime A1761 controls protect settings and unknown flags using fake GATT."""

import asyncio

import pytest

from solix_link.client import SolixMonitor
from solix_link.protocol import DATA_RESPONSE, Model, Session, build_packet, parse_packet, parse_tlvs, tlv


BASELINE = {
    "ac_output_enabled": 1, "dc_output_enabled": 0, "ac_charging_power_limit_w": 1000,
    "device_timeout_minutes": 720, "display_timeout_seconds": 30, "display_brightness": 2,
    "light_mode": 0, "temperature_unit_fahrenheit": 0, "ac_fast_charge_enabled": 0,
    "ac_power_saving_mode_enabled": 1, "dc_power_saving_mode_enabled": 1,
}
FLAGS = b"\x04\x02\x02" + bytes(range(18))
CONTROLS = (
    ("display_brightness", "404c", "0101", "display_brightness", 1),
    ("ac_charging_power", "4044", "028403", "ac_charging_power_limit_w", 900),
    ("device_timeout", "4045", "020000", "device_timeout_minutes", 0),
)


def payload_for(metrics, flags=FLAGS):
    fields = ((0xD7, "ac_output_enabled", 1), (0xD8, "dc_output_enabled", 1),
              (0xD1, "ac_charging_power_limit_w", 2), (0xD2, "device_timeout_minutes", 2),
              (0xD3, "display_timeout_seconds", 2), (0xD9, "display_brightness", 1),
              (0xDC, "light_mode", 1), (0xDD, "temperature_unit_fahrenheit", 1),
              (0xE5, "ac_fast_charge_enabled", 1))
    return b"".join(tlv(tag, bytes((width,)) + metrics[key].to_bytes(width, "little"))
                    for tag, key, width in fields) + tlv(0xF8, flags)


@pytest.mark.parametrize("setting,command,typed,metric,value", CONTROLS)
def test_prime_wire_uses_verified_original_body_and_gcm_timestamp(monkeypatch, setting, command, typed, metric, value):
    monkeypatch.setattr(Session, "_timestamp", staticmethod(lambda: b"\x00\x01\x02\x03"))
    prime, legacy = Session(Model.C1000, protocol="prime"), Session(Model.C1000)
    for session in (prime, legacy):
        session.ready, session._secret = True, bytes(range(32))
    packet = parse_packet(prime.c1000_control_packet(setting, value))
    plain = prime._crypt(packet.payload, False)
    fields = parse_tlvs(plain)
    assert packet.command.hex() == command
    assert set(fields) == {0xA1, 0xA2, 0xFE}
    assert fields[0xA1] == b"\x21" and fields[0xA2].hex() == typed
    assert fields[0xFE] == b"\x03\x00\x01\x02\x03"
    legacy_packet = parse_packet(legacy.c1000_control_packet(setting, value))
    assert plain == legacy._crypt(legacy_packet.payload, False)
    assert packet.payload != legacy_packet.payload


@pytest.mark.parametrize("setting,value", [
    ("ac_output_enabled", False), ("dc_output_enabled", True), ("display_timeout", 60),
    ("light_mode", 1), ("temperature_unit_fahrenheit", True), ("fast_charge_enabled", True),
    ("ac_power_saving_mode_enabled", False), ("dc_power_saving_mode_enabled", False),
])
def test_original_prime_unverified_controls_are_rejected(setting, value):
    session = Session(Model.C1000, protocol="prime")
    session.ready, session._secret = True, bytes(range(32))
    with pytest.raises(RuntimeError, match="verified only with a legacy"):
        session.c1000_control_packet(setting, value)


@pytest.mark.parametrize("setting,value", [
    ("display_brightness", 0), ("display_brightness", True), ("display_brightness", 4),
    ("ac_charging_power", True), ("ac_charging_power", 150), ("device_timeout", False),
    ("device_timeout", 90),
])
def test_prime_invalid_control_values_fail_before_transport(setting, value):
    monitor = SolixMonitor("AA:BB:CC:DD:EE:04", model=Model.C1000, protocol="prime")
    monitor._session.ready, monitor._session._secret = True, bytes(range(32))
    with pytest.raises(ValueError):
        asyncio.run(monitor.set_c1000_setting(setting, value))


@pytest.mark.parametrize("setting,command,typed,metric,value", CONTROLS)
@pytest.mark.parametrize("problem", ["matching", "stale_baseline", "legacy_flags", "protected_changed",
                                    "unknown_flag_changed", "stale_final", "reverted_final", "ignored"])
def test_prime_sdk_requires_fresh_protected_settings_and_complete_flags(
    monkeypatch, setting, command, typed, metric, value, problem,
):
    async def run():
        monitor = SolixMonitor("AA:BB:CC:DD:EE:04", model=Model.C1000,
                               protocol="prime", owner_user_id="a" * 40)
        monitor._session.ready, monitor._session._secret = True, bytes(range(32))
        monitor._ready.set()
        monitor.metrics = BASELINE.copy()  # Cached values cannot satisfy the guard.
        monitor.raw_tlvs = {0xF8: FLAGS}
        state = BASELINE.copy()
        writes, status_after_write = [], 0

        class Client:
            is_connected = True

            async def write_gatt_char(self, _uuid, packet, **_kwargs):
                nonlocal status_after_write
                parsed = parse_packet(packet)
                received = parsed.command.hex()
                writes.append(received)
                if received == command:
                    fields = parse_tlvs(monitor._session._crypt(parsed.payload, False))
                    assert fields[0xA2].hex() == typed
                    if problem != "ignored":
                        state[metric] = value
                    monitor._responses.put_nowait((f"48{command[2:]}", b"\x00"))
                elif received == "4040":
                    changed = command in writes
                    status_after_write += int(changed)
                    flags = FLAGS
                    if problem == "legacy_flags":
                        flags = b"\x01\x02\x02"
                    elif changed and problem == "unknown_flag_changed":
                        flags = FLAGS[:-1] + bytes((FLAGS[-1] ^ 1,))
                    if changed and problem == "protected_changed":
                        state["light_mode"] = 1
                    if changed and problem == "reverted_final" and status_after_write >= 2:
                        state[metric] = BASELINE[metric]
                    if problem == "stale_baseline" or (changed and problem == "stale_final" and status_after_write >= 2):
                        plain = tlv(0xC1, b"\x01\x62")
                    else:
                        plain = payload_for(state, flags)
                    encrypted = monitor._session._crypt(plain, True)
                    middle = len(encrypted) // 2
                    # Full status is fragmented, as on the updated original.
                    for fragment in (b"\x12" + encrypted[:middle], b"\x22" + encrypted[middle:]):
                        await monitor._handle(build_packet(DATA_RESPONSE, bytes.fromhex("c840"), fragment))

        async def no_delay(_seconds):
            pass

        async def update(timeout=None):
            if monitor._updates.empty():
                raise TimeoutError
            return monitor._updates.get_nowait()

        monkeypatch.setattr("solix_link.client.asyncio.sleep", no_delay)
        monitor.wait_for_update, monitor._client = update, Client()
        if setting == "display_brightness":
            apply = monitor.set_c1000_setting(setting, value)
        elif setting == "ac_charging_power":
            apply = monitor.set_ac_charging_power(value)
        else:
            apply = monitor.set_device_timeout(value)
        if problem == "matching":
            result = await apply
            assert all(result[key] == expected for key, expected in {**BASELINE, metric: value}.items())
            assert monitor.raw_tlvs[0xF8] == FLAGS
        elif problem in ("unknown_flag_changed", "reverted_final"):
            with pytest.raises(RuntimeError, match="setting may have changed"):
                await apply
        elif problem == "legacy_flags":
            with pytest.raises(RuntimeError, match="complete.*F8.*no write sent"):
                await apply
        else:
            with pytest.raises(TimeoutError) as error:
                await apply
            if problem == "stale_final":
                assert "setting may have changed" in str(error.value)
        assert writes.count(command) == (0 if problem in ("stale_baseline", "legacy_flags") else 1)
        assert set(writes) <= {"4040", command}  # No output command or write retry.
    asyncio.run(run())
