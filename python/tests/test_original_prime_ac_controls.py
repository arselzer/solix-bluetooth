"""Original Prime AC controls protect fresh timer and output state."""

import asyncio

import pytest

from solix_link import Model, SolixMonitor
from solix_link.protocol import DATA_RESPONSE, build_packet, parse_packet, parse_tlvs, tlv
from test_original_prime_controls import BASELINE, FLAGS, payload_for


def monitor_with_responses(monkeypatch, setting, *, timer=b"\x03\x00\x00\x00\x00",
                           after_timer=None, ac_on=False):
    monitor = SolixMonitor("AA:BB:CC:DD:EE:04", model=Model.C1000, protocol="prime")
    monitor._session.ready, monitor._session._secret = True, bytes(range(32))
    monitor._ready.set()
    monitor._telemetry_revision = 10
    monitor.metrics = {**BASELINE, "ac_output_enabled": 0}
    monitor.raw_tlvs = {0xA2: b"\x03\x00\x00\x00\x00"}
    monitor._raw_tlv_revision[0xA2] = 10
    state = {**BASELINE, "ac_output_enabled": int(ac_on)}
    writes = []
    command = "404a" if setting == "ac_output_enabled" else "4077"

    class Client:
        is_connected = True

        async def write_gatt_char(self, _uuid, packet, **_kwargs):
            parsed = parse_packet(packet)
            received = parsed.command.hex()
            writes.append(received)
            if received == command:
                fields = parse_tlvs(monitor._session._crypt(parsed.payload, False))
                state[setting] = fields[0xA2][1]
                monitor._responses.put_nowait(("48" + command[2:], b"\x00"))
            elif received == "4040":
                flags = FLAGS[:2] + bytes((state["ac_power_saving_mode_enabled"] + 1,)) + FLAGS[3:]
                fields = parse_tlvs(payload_for(state, flags))
                value = after_timer if command in writes and after_timer is not None else timer
                if value is None:
                    del fields[0xA2]
                else:
                    fields[0xA2] = value
                plain = b"".join(tlv(tag, value) for tag, value in fields.items())
                encrypted = monitor._session._crypt(plain, True)
                await monitor._handle(build_packet(DATA_RESPONSE, bytes.fromhex("c840"), b"\x11" + encrypted))
            else:
                pytest.fail("Unexpected control command")

    async def no_delay(_seconds):
        pass

    async def update(timeout=None):
        if monitor._updates.empty():
            raise TimeoutError
        return monitor._updates.get_nowait()

    monitor._client, monitor.wait_for_update = Client(), update
    monkeypatch.setattr("solix_link.client.asyncio.sleep", no_delay)
    return monitor, writes, command


@pytest.mark.parametrize("setting", ["ac_output_enabled", "ac_power_saving_mode_enabled"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("timer", [None, b"\x01\x00", b"\x02\x00\x00", b"\x03\x00\x00\x00",
                                  b"\x03\x01\x00\x00\x00"])
def test_ac_controls_reject_cached_missing_invalid_or_active_timer(monkeypatch, setting, enabled, timer):
    async def run():
        monitor, writes, command = monitor_with_responses(monkeypatch, setting, timer=timer)
        with pytest.raises(RuntimeError, match="fresh inactive AC timer; no write sent"):
            await monitor.set_c1000_setting(setting, enabled)
        assert writes == ["4040"] and command not in writes
    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_ac_smart_refuses_fresh_enabled_ac_despite_cached_off(monkeypatch, enabled):
    async def run():
        monitor, writes, command = monitor_with_responses(monkeypatch, "ac_power_saving_mode_enabled", ac_on=True)
        with pytest.raises(RuntimeError, match="AC output to be off; no write sent"):
            await monitor.set_ac_power_saving_enabled(enabled)
        assert writes == ["4040"] and command not in writes
    asyncio.run(run())


@pytest.mark.parametrize("setting", ["ac_output_enabled", "ac_power_saving_mode_enabled"])
@pytest.mark.parametrize("enabled", [False, True])
def test_new_timer_after_write_fails_without_retry(monkeypatch, setting, enabled):
    async def run():
        monitor, writes, command = monitor_with_responses(
            monkeypatch, setting, after_timer=b"\x03\x3c\x00\x00\x00")
        with pytest.raises(RuntimeError, match="fresh inactive AC timer; the setting may have changed"):
            await monitor.set_c1000_setting(setting, enabled)
        assert writes.count(command) == 1 and set(writes) == {"4040", command}
    asyncio.run(run())
