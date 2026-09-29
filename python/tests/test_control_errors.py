"""Control failures must surface even when a previous value looks correct."""

import asyncio

import pytest

from solix_gen2.client import SolixMonitor
from solix_gen2.protocol import DATA_RESPONSE, Model, Session, build_packet, parse_packet, tlv


@pytest.mark.parametrize("status", [b"\x09", b""])
def test_rejected_setting_raises_before_telemetry_can_confirm(monkeypatch, status):
    async def scenario():
        monitor = SolixMonitor("AA:BB:CC:DD:EE:04", model=Model.C1000)
        monitor._session.ready = True
        monitor._session._secret = bytes(range(32))
        monitor._ready.set()
        monitor.metrics = {"display_timeout_seconds": 60}

        class FakeClient:
            is_connected = True

            async def write_gatt_char(self, _uuid, packet, **_kwargs):
                assert parse_packet(packet).command.hex() == "4046"
                monitor._responses.put_nowait(("4846", status))

        async def no_delay(_seconds):
            pass

        monkeypatch.setattr("solix_gen2.client.asyncio.sleep", no_delay)
        monitor._client = FakeClient()
        with pytest.raises(RuntimeError, match="rejected setting"):
            await monitor.set_c1000_setting("display_timeout", 60)

    asyncio.run(scenario())


@pytest.mark.parametrize("fresh", ["none", "unrelated", "matching"])
def test_success_ack_alone_or_old_telemetry_cannot_confirm(monkeypatch, fresh):
    async def scenario():
        monitor = SolixMonitor("AA:BB:CC:DD:EE:04", model=Model.C1000)
        monitor._session.ready = True
        monitor._session._secret = bytes(range(32))
        monitor._ready.set()
        monitor.metrics = {"display_timeout_seconds": 60}
        monitor._updates.put_nowait(monitor.metrics.copy())  # Stale pre-write reading.

        class FakeClient:
            is_connected = True

            async def write_gatt_char(self, _uuid, packet, **_kwargs):
                command = parse_packet(packet).command.hex()
                if command == "4046":
                    monitor._responses.put_nowait(("4846", b"\x00"))
                elif command == "4040" and fresh != "none":
                    plain = tlv(0xD3, b"\x02\x3c\x00") if fresh == "matching" else tlv(0xC1, b"\x01\x32")
                    await monitor._handle(build_packet(DATA_RESPONSE, bytes.fromhex("4840"),
                                                      monitor._session._crypt(plain, True)))

        async def no_delay(_seconds):
            pass

        async def immediate_update(timeout=None):
            if monitor._updates.empty():
                raise TimeoutError
            return monitor._updates.get_nowait()

        monkeypatch.setattr("solix_gen2.client.asyncio.sleep", no_delay)
        monitor.wait_for_update = immediate_update
        monitor._client = FakeClient()
        if fresh == "matching":
            assert (await monitor.set_display_timeout(60))["display_timeout_seconds"] == 60
        else:
            with pytest.raises(TimeoutError, match="did not report"):
                await monitor.set_display_timeout(60)

    asyncio.run(scenario())


def test_gen2_control_rejection_is_an_acknowledgement_not_telemetry():
    session = Session(Model.C2000_GEN2)
    session._secret = bytes(range(32))
    update = session.feed(build_packet(DATA_RESPONSE, bytes.fromhex("4903"), session._crypt(b"\x09", True)))
    assert update.response == ("4903", b"\x09")
    assert update.telemetry is None
