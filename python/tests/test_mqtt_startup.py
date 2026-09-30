"""Exercise native startup timing without sockets, devices, or wall-clock sleeps."""

import asyncio
import base64
import json

import pytest

from solix_link import mqtt_intercept
from solix_link.ap_service_config import APServiceConfig
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, tlv


class Writer:
    def __init__(self):
        self.closed = False
        self.frames = []
        self.outgoing = asyncio.Queue()

    def write(self, frame):
        self.frames.append(frame)
        self.outgoing.put_nowait(frame)

    async def drain(self):
        pass

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class ControlledAsyncio:
    """Replace only the module's clock/sleep; task scheduling stays real."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = asyncio.Queue()

    def __getattr__(self, name):
        return getattr(asyncio, name)

    def get_running_loop(self):
        parent = self
        loop = asyncio.get_running_loop()

        class Clock:
            def time(self):
                return parent.now

            def create_future(self):
                return loop.create_future()

        return Clock()

    async def sleep(self, delay):
        release = asyncio.Event()
        self.sleeps.put_nowait((delay, release))
        await release.wait()


def mqtt_string(value):
    encoded = value.encode()
    return len(encoded).to_bytes(2, "big") + encoded


class Session:
    def __init__(self, server):
        self.reader = asyncio.StreamReader()
        self.writer = Writer()
        self.connection = mqtt_intercept._Connection(server, self.reader, self.writer)
        self.runner = asyncio.create_task(self.connection.run())
        self.requests = []

    async def packet(self):
        return await asyncio.wait_for(self.writer.outgoing.get(), timeout=1)

    async def connect(self, topic=None):
        # MQTT 3.1.1, clean session, 60-second keepalive, synthetic client ID.
        body = mqtt_string("MQTT") + b"\x04\x02\x00\x3c" + mqtt_string("synthetic")
        self.reader.feed_data(mqtt_intercept.mqtt_packet(0x10, body))
        assert await self.packet() == b"\x20\x02\x00\x00"
        await self.subscribe(topic)

    async def subscribe(self, topic=None):
        already_subscribed = self.connection.subscribed
        topic = self.connection.server.topic if topic is None else topic
        body = b"\x00\x01" + mqtt_string(topic) + b"\x00"
        self.reader.feed_data(mqtt_intercept.mqtt_packet(0x82, body))
        response = await self.packet()
        assert response[:4] == b"\x90\x03\x00\x01"
        if response[-1] == 0 and not already_subscribed:
            active = await asyncio.wait_for(self.connection.server.activations.get(), timeout=1)
            assert active is self.connection
        return response[-1]

    def request(self, request):
        task = asyncio.create_task(self.connection.request(request))
        self.requests.append(task)
        return task

    def acknowledge(self, request):
        config = self.connection.server.config
        frame = build_packet(DATA_RESPONSE, bytes.fromhex(request.response_command),
                             b"\x00" + tlv(0xA1, b"\x34"))
        message = json.dumps({"payload": json.dumps({
            "pn": config.product, "sn": config.device_serial,
            "data": base64.b64encode(frame).decode(),
        })}).encode()
        topic = f"dt/anker_power/{config.product}/{config.device_serial}/param_info"
        self.reader.feed_data(mqtt_intercept.mqtt_packet(0x30, mqtt_string(topic) + message))

    async def close(self):
        for task in self.requests:
            task.cancel()
        await asyncio.gather(*self.requests, return_exceptions=True)
        await self.connection.close()
        self.runner.cancel()
        await asyncio.gather(self.runner, return_exceptions=True)


def make_server(tmp_path, monkeypatch, model):
    clock = ControlledAsyncio()
    monkeypatch.setattr(mqtt_intercept, "asyncio", clock)

    async def parked_poller(_connection):
        # Tests explicitly issue requests, so background polling cannot race them.
        await asyncio.Future()

    monkeypatch.setattr(mqtt_intercept._Connection, "poll", parked_poller)
    config = APServiceConfig(name="test", interface="wlan0", phy="phy0", country="AT",
                             device_serial="A1763SYNTHETIC001", account_id="a" * 40,
                             model=model)
    server = mqtt_intercept.LocalMqttServer(config, tmp_path)
    server.activations = asyncio.Queue()
    server.callback = lambda _snapshot: server.activations.put_nowait(server.connection)
    return clock, server


@pytest.mark.parametrize("model,delay", [(Model.C1000_GEN2, 15), (Model.C2000_GEN2, 0)])
def test_accepted_subscription_sets_model_specific_deadline(tmp_path, monkeypatch, model, delay):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, model)
        session = Session(server)
        try:
            await session.connect()
            assert session.connection.subscribed
            assert server.connection is session.connection
            assert session.connection._command_ready_at == clock.now + delay
            assert len(session.writer.frames) == 2
        finally:
            await session.close()
    asyncio.run(run())


def test_rejected_subscription_cannot_start_commands(tmp_path, monkeypatch):
    async def run():
        _, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect("cmd/anker_power/#")
            assert session.writer.frames[-1][-1] == 0x80
            assert not session.connection.subscribed
            assert session.connection._command_ready_at == 0
            with pytest.raises(ConnectionError, match="subscription"):
                await session.connection.request(server.commands.status())
            assert len(session.writer.frames) == 2
        finally:
            await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["status", "readiness", "ac_charging_power"])
def test_requests_wait_for_c1000_grace_before_pending_or_publish(tmp_path, monkeypatch, operation):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            method = getattr(server.commands, operation)
            request = method(300) if operation == "ac_charging_power" else method()
            task = session.request(request)
            delay, release = await asyncio.wait_for(clock.sleeps.get(), timeout=1)
            assert delay == 15
            assert not task.done()
            assert session.connection.pending is None
            assert len(session.writer.frames) == 2
            clock.now += delay
            release.set()
            assert (await session.packet())[0] == 0x30
            session.acknowledge(request)
            assert (await asyncio.wait_for(task, timeout=1))[0] == 0
            assert session.connection.pending is None
            # Settling is per connection, not an extra delay for every request.
            second = session.request(request)
            assert (await session.packet())[0] == 0x30
            assert clock.sleeps.empty()
            session.acknowledge(request)
            await asyncio.wait_for(second, timeout=1)
        finally:
            await session.close()
    asyncio.run(run())


def test_cancel_during_grace_never_publishes(tmp_path, monkeypatch):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            task = session.request(server.commands.ac_charging_power(300))
            _, release = await asyncio.wait_for(clock.sleeps.get(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            assert session.connection.pending is None
            assert len(session.writer.frames) == 2
            assert not session.connection.lock.locked()
        finally:
            await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("disconnect", ["writer_closed", "subscription_lost"])
def test_disconnect_during_grace_never_publishes(tmp_path, monkeypatch, disconnect):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            task = session.request(server.commands.ac_charging_power(300))
            delay, release = await asyncio.wait_for(clock.sleeps.get(), timeout=1)
            if disconnect == "writer_closed":
                session.writer.close()
            else:
                session.connection.subscribed = False
            clock.now += delay
            release.set()
            with pytest.raises(ConnectionError, match="disconnected during startup"):
                await asyncio.wait_for(task, timeout=1)
            assert session.connection.pending is None
            assert len(session.writer.frames) == 2
        finally:
            await session.close()
    asyncio.run(run())


def test_c2000_can_request_immediately_after_subscription(tmp_path, monkeypatch):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C2000_GEN2)
        session = Session(server)
        try:
            await session.connect()
            request = server.commands.status()
            task = session.request(request)
            assert (await session.packet())[0] == 0x30
            assert clock.sleeps.empty()
            session.acknowledge(request)
            assert (await asyncio.wait_for(task, timeout=1))[0] == 0
        finally:
            await session.close()
    asyncio.run(run())


def test_reconnect_gets_new_grace_but_duplicate_subscription_does_not(tmp_path, monkeypatch):
    async def run():
        clock, server = make_server(tmp_path, monkeypatch, Model.C1000_GEN2)
        first, second = Session(server), Session(server)
        try:
            await first.connect()
            original_deadline = first.connection._command_ready_at
            clock.now += 5
            assert await first.subscribe() == 0
            assert first.connection._command_ready_at == original_deadline
            clock.now += 30
            await second.connect()
            assert first.writer.is_closing()
            assert server.connection is second.connection
            assert second.connection._command_ready_at == clock.now + 15
            task = second.request(server.commands.status())
            delay, _ = await asyncio.wait_for(clock.sleeps.get(), timeout=1)
            assert delay == 15 and not task.done()
            assert len(second.writer.frames) == 2
        finally:
            await first.close()
            await second.close()
    asyncio.run(run())
