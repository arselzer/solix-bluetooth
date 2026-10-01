"""Real local mutual-TLS tests for original C1000's deferred status route."""

import asyncio
import base64
import json
import ssl

import pytest

from solix_link.ap_service_config import APServiceConfig, initialize_ap_service
from solix_link.mqtt_intercept import LocalMqttServer, read_mqtt, mqtt_packet
from solix_link.protocol import DATA_RESPONSE, Model, build_packet, parse_packet, parse_tlvs, tlv
from test_original_prime_controls import BASELINE, FLAGS, payload_for


# Native validation is independent of the growing BLE Prime whitelist.
NATIVE_CONTROLS = (
    ("display_brightness", "404c", "0101", "display_brightness", 1),
    ("ac_charging_power", "4044", "028403", "ac_charging_power_limit_w", 900),
    ("device_timeout", "4045", "020000", "device_timeout_minutes", 0),
    ("display_timeout", "4046", "023c00", "display_timeout_seconds", 60),
    ("light_mode", "404f", "0101", "light_mode", 1),
    ("temperature_unit_fahrenheit", "4050", "0101", "temperature_unit_fahrenheit", True),
)


def test_native_control_coverage_does_not_follow_ble_only_additions():
    from solix_link.c1000_capabilities import C1000_NATIVE_SETTINGS
    assert {row[0] for row in NATIVE_CONTROLS} == C1000_NATIVE_SETTINGS
    assert "dc_power_saving_mode_enabled" not in C1000_NATIVE_SETTINGS


@pytest.mark.parametrize("setting,ble_command,typed,metric,value", NATIVE_CONTROLS)
@pytest.mark.parametrize("problem", ["matching", "ignored", "protected_changed", "flags_changed",
                                    "negative_ack", "negative_then_success"])
def test_original_tls_settings_send_once_and_confirm_full_status(
    tmp_path, setting, ble_command, typed, metric, value, problem,
):
    async def run():
        config = APServiceConfig("original", "wlan_ap", "phy9", "AT", "A1761TEST0000001", "a" * 40,
                                 model=Model.C1000)
        directory = tmp_path / "private"
        initialize_ap_service(directory, config)
        server = LocalMqttServer(config, directory, allow_control=True)
        await server.start(host="127.0.0.1", port=0)
        port = server._server.sockets[0].getsockname()[1]
        context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
        context.load_cert_chain(directory / "client.pem", directory / "client-key.pem")
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context,
                                                       server_hostname=config.broker_host)
        client_id = b"synthetic-original"
        writer.write(mqtt_packet(0x10, b"\x00\x04MQTT\x04\x02\x00\x3c"
                                 + len(client_id).to_bytes(2, "big") + client_id))
        await writer.drain()
        assert await read_mqtt(reader) == (0x20, b"\x00\x00")
        topic = server.topic.encode()
        writer.write(mqtt_packet(0x82, b"\x00\x01" + len(topic).to_bytes(2, "big") + topic + b"\x00"))
        await writer.drain()
        assert await read_mqtt(reader) == (0x90, b"\x00\x01\x00")
        state, captured = BASELINE.copy(), []
        changed = False

        async def publish(command, payload, *, retained=False, serial=None):
            frame = build_packet(DATA_RESPONSE, bytes.fromhex(command), payload)
            message = json.dumps({"payload": json.dumps({"sn": serial or config.device_serial,
                "pn": "A1761", "data": base64.b64encode(frame).decode()})}).encode()
            topic = f"dt/anker_power/A1761/{config.device_serial}/param_info".encode()
            writer.write(mqtt_packet(0x31 if retained else 0x30,
                                     len(topic).to_bytes(2, "big") + topic + message))
            await writer.drain()

        async def respond():
            nonlocal changed
            try:
                while True:
                    first, body = await read_mqtt(reader)
                    assert first == 0x30
                    n = int.from_bytes(body[:2], "big")
                    envelope = json.loads(body[2 + n:])
                    frame = parse_packet(base64.b64decode(json.loads(envelope["payload"])["data"]))
                    command = frame.command.hex()
                    captured.append(command)
                    if command == "0040":
                        flags = FLAGS[:-1] + bytes((FLAGS[-1] ^ 1,)) if changed and problem == "flags_changed" else FLAGS
                        complete = payload_for(state, flags)
                        # These cannot satisfy a status request: retained, other
                        # identity, incomplete report, and network-only command.
                        await publish("0405", complete, retained=True)
                        await publish("0405", complete, serial="OTHER00000000000")
                        await publish("0405", tlv(0xC1, b"\x01\x64"))
                        await publish("0407", complete)
                        await publish("0405", complete)
                    else:
                        assert command == "00" + ble_command[2:]
                        tags = parse_tlvs(frame.payload)
                        assert set(tags) == {0xA1, 0xA2, 0xFE}
                        assert tags[0xA1] == b"\x22" and tags[0xA2].hex() == typed
                        changed = True
                        if problem != "ignored":
                            state[metric] = int(value)
                        if problem == "protected_changed":
                            protected = "display_brightness" if metric == "light_mode" else "light_mode"
                            state[protected] += 1
                        if problem in ("negative_ack", "negative_then_success"):
                            await publish(f"{int(command, 16) | 0x800:04x}", b"\x01")
                            if problem == "negative_then_success":
                                await publish(f"{int(command, 16) | 0x800:04x}", b"\x00")
                        # Successful setters deliberately produce no ACK.
            except (OSError, asyncio.IncompleteReadError):
                pass

        task = asyncio.create_task(respond())
        try:
            async with asyncio.timeout(3):
                while not server.snapshot()["available"]:
                    await asyncio.sleep(.005)
            assert server.snapshot()["power_flow"] == "unknown"
            method = {"display_brightness": server.set_display_brightness,
                      "ac_charging_power": server.set_ac_charging_power,
                      "device_timeout": server.set_device_timeout,
                      "display_timeout": server.set_display_timeout,
                      "light_mode": server.set_light_mode,
                      "temperature_unit_fahrenheit": server.set_temperature_unit}[setting]
            async with asyncio.timeout(3):
                if problem == "matching":
                    snapshot = await method(value)
                    assert snapshot["metrics"][metric] == int(value)
                    assert snapshot["metrics"]["ac_output_enabled"] == 1
                    assert config.device_serial not in json.dumps(snapshot)
                    assert captured.count("0040") >= 4  # poll, baseline, two confirmations
                else:
                    with pytest.raises(RuntimeError, match="setting may have changed"):
                        await method(value)
            assert captured.count("00" + ble_command[2:]) == 1
            assert set(captured) <= {"0040", "00" + ble_command[2:]}
            assert server.connection._original_ack is None
        finally:
            writer.close()
            await server.stop()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("problem", ["incomplete_baseline", "short_baseline_flags", "incomplete_confirmation"])
def test_original_write_ignores_cached_settings_when_reply_is_incomplete(tmp_path, problem):
    async def run():
        config = APServiceConfig("original", "wlan_ap", "phy9", "AT", "A1761TEST0000001", "a" * 40,
                                 model=Model.C1000)
        server = LocalMqttServer(config, tmp_path, allow_control=True)
        server.metrics = BASELINE.copy()

        class Connection:
            writes = 0
            requests = 0
            finished = False

            async def request(self, request):
                assert request.response_command == "0840"
                self.requests += 1
                if problem == "incomplete_baseline" or (self.writes and problem == "incomplete_confirmation"):
                    return b"\x00" + tlv(0xC1, b"\x01\x64")
                flags = b"\x01\x02\x02" if problem == "short_baseline_flags" else FLAGS
                return b"\x00" + payload_for(BASELINE, flags)

            async def send_original_setting(self, request):
                assert request.response_command == "0844"
                self.writes += 1
                server.metrics["ac_charging_power_limit_w"] = 900

            def check_original_setting_response(self):
                pass

            def finish_original_setting(self):
                self.finished = True

        connection = Connection()
        server.connection = connection
        with pytest.raises(RuntimeError):
            await server.set_ac_charging_power(900)
        assert connection.writes == int(problem == "incomplete_confirmation")
        assert connection.finished == (problem == "incomplete_confirmation")
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["cancel", "send_error"])
def test_original_interrupted_write_closes_transport_without_retry(tmp_path, failure):
    from solix_link.mqtt_intercept import _Connection

    async def run():
        config = APServiceConfig("original", "wlan_ap", "phy9", "AT", "A1761TEST0000001", "a" * 40,
                                 model=Model.C1000)
        server = LocalMqttServer(config, tmp_path, allow_control=True)
        written = asyncio.Event()

        class Writer:
            closed = False
            writes = 0

            def is_closing(self):
                return self.closed

            def write(self, data):
                self.writes += 1
                written.set()

            async def drain(self):
                if failure == "send_error":
                    raise OSError("synthetic send failure")
                await asyncio.Event().wait()

            def close(self):
                self.closed = True

        writer = Writer()
        connection = _Connection(server, asyncio.StreamReader(), writer)
        connection.subscribed = True
        server.connection = connection

        async def baseline(request):
            assert request.response_command == "0840"
            return b"\x00" + payload_for(BASELINE)

        connection.request = baseline
        task = asyncio.create_task(server.set_ac_charging_power(900))
        await asyncio.wait_for(written.wait(), .5)
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="setting may have changed"):
                await task
        assert writer.closed and writer.writes == 1
        assert connection._original_ack is None
    asyncio.run(run())
