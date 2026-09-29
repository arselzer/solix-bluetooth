import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import ssl
import time

from cryptography import x509
import pytest

from solix_gen2 import LabConfig, LocalMqttServer, initialize_lab, load_lab
from solix_gen2.isolated_ap import IsolatedAP
from solix_gen2.lab_config import private_write
from solix_gen2.lab_monitor import LabMonitorService
from solix_gen2.lab_service import api_response, http_reply, ntp_reply
from solix_gen2.mqtt_credentials import decrypt_device_credential
from solix_gen2.mqtt_intercept import mqtt_packet, native_response, read_mqtt
from solix_gen2.protocol import DATA_RESPONSE, build_packet, parse_packet, parse_tlvs, tlv


@pytest.fixture
def lab(tmp_path):
    config = LabConfig("ups", "wlan_lab", "phy9", "AT", "A1783SYNTHETIC001", "a" * 40,
                       timezone_name="Europe/Vienna")
    directory = tmp_path / "private"
    initialize_lab(directory, config)
    return config, directory


def test_private_bootstrap_certificates_and_no_overwrite(lab):
    config, directory = lab
    assert load_lab(directory / "lab.json") == config
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    response = json.loads((directory / "mqtt-response.json").read_text())["data"]
    assert response["aws_root_ca1_pem"].encode() == (directory / "ca.pem").read_bytes()
    assert decrypt_device_credential(config.device_serial, response["private_key"]) == (directory / "client-key.pem").read_bytes()
    cert = x509.load_pem_x509_certificate((directory / "server.pem").read_bytes())
    assert config.broker_host in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert config.account_id not in repr(config) and config.passphrase not in repr(config)
    before = (directory / "client-key.pem").read_bytes()
    with pytest.raises(FileExistsError):
        initialize_lab(directory, config)
    assert (directory / "client-key.pem").read_bytes() == before


@pytest.mark.parametrize("changes", [{"namespace": "bad; command"}, {"ssid": "x\nssid=other"},
                                     {"passphrase": "password\ncommand"}, {"gateway": "8.8.8.1"},
                                     {"gateway": "127.0.0.1"}, {"broker_host": "evil/host"},
                                     {"device_serial": "short"}, {"account_id": "bad"}])
def test_reject_unsafe_lab_configuration(lab, changes):
    with pytest.raises(ValueError):
        replace(lab[0], **changes)


def test_refuse_public_config_and_symlink(lab, tmp_path):
    config, directory = lab
    path = directory / "lab.json"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        load_lab(path)
    target = tmp_path / "target"
    target.write_text("unchanged")
    link = directory / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        private_write(link, "secret")
    assert target.read_text() == "unchanged"


def test_api_framing_and_ntp_without_forwarding(lab):
    config, _ = lab
    credentials = b'{"certificate_pem":"synthetic"}'
    body, chunked = api_response("/equipment/devicemanage/get_mqtt_info", {"device_sn": config.device_serial}, config, credentials)
    wire = http_reply(body, credentials=chunked)
    assert b"Transfer-Encoding: chunked" in wire
    assert wire.endswith(b"0\r\n\r\n")
    # Parse the exact one-byte chunks, rather than accepting only a header match.
    framed = wire.split(b"\r\n\r\n", 1)[1]
    assert framed == b"".join(b"1\r\n" + bytes([v]) + b"\r\n" for v in credentials) + b"0\r\n\r\n"
    assert api_response("//equipment/devicemanage/get_mqtt_info", {}, config, credentials) == (credentials, True)
    body, chunked = api_response("/equipment/devicerelation/bind_device", {}, config, credentials)
    assert not chunked and json.loads(body)["data"]["is_bind"]
    assert f"Content-Length: {len(body)}".encode() in http_reply(body)
    with pytest.raises(ValueError):
        api_response("/equipment/devicemanage/get_mqtt_info", {"device_sn": "other"}, config, credentials)
    with pytest.raises(ValueError):
        api_response("https://example.invalid/unknown", {}, config, credentials)
    request = bytes([0x23]) + bytes(39) + b"12345678"
    reply = ntp_reply(request, 1800000000.5)
    assert len(reply) == 48 and reply[24:32] == b"12345678"
    assert reply[0] & 7 == 4
    assert int.from_bytes(reply[40:44], "big") == 1800000000 + 2208988800
    assert ntp_reply(b"short", time.time()) is None


def response(config, command, fields=b"", *, serial=None):
    frame = build_packet(DATA_RESPONSE, bytes.fromhex(command), b"\x00" + fields)
    return json.dumps({"payload": json.dumps({"sn": serial or config.device_serial, "pn": "A1783",
                                               "data": base64.b64encode(frame).decode()})}).encode()


def test_native_response_filters_identity_and_malformed_envelopes(lab):
    config, _ = lab
    assert native_response(response(config, "0901"), config).command.hex() == "0901"
    assert native_response(response(config, "0901", serial="other"), config) is None
    for message in (b"[]", b'{"payload":"[]"}', b"bad json"):
        with pytest.raises(ValueError):
            native_response(message, config)


async def fake_station(config, directory, port, *, retained_first=False):
    context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
    context.load_cert_chain(directory / "client.pem", directory / "client-key.pem")
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname=config.broker_host)
    client_id = b"synthetic-station"
    writer.write(mqtt_packet(0x10, b"\x00\x04MQTT\x04\x02\x00\x3c" + len(client_id).to_bytes(2, "big") + client_id))
    await writer.drain()
    assert await read_mqtt(reader) == (0x20, b"\x00\x00")
    topic = f"cmd/anker_power/A1783/{config.device_serial}/req".encode()
    writer.write(mqtt_packet(0x82, b"\x00\x01" + len(topic).to_bytes(2, "big") + topic + b"\x01"))
    await writer.drain()
    assert await read_mqtt(reader) == (0x90, b"\x00\x01\x01")
    captured = []
    async def respond():
        watts = 1800
        first_status = True
        try:
            while True:
                first, body = await read_mqtt(reader)
                if first == 0x40:
                    continue
                assert first == 0x30  # No retained or QoS2 commands.
                size = int.from_bytes(body[:2], "big")
                envelope = json.loads(body[2 + size:])
                frame = parse_packet(base64.b64decode(json.loads(envelope["payload"])["data"]))
                command = frame.command.hex()
                captured.append(frame)
                fields = b""
                if command == "0101":
                    tags = parse_tlvs(frame.payload)
                    assert set(tags) == {0xA1, 0xA4, 0xFD}
                    watts = int.from_bytes(tags[0xA4][1:], "little")
                elif command == "0100":
                    fields = tlv(0xA5, bytes([4, 25, 0, 90, 100])) + tlv(0xA7, bytes.fromhex("04015600015600")) + tlv(0xA4, bytes(5) + watts.to_bytes(2, "little"))
                elif command == "0089":
                    fields = tlv(0xA1, b"\x34")
                else:
                    raise AssertionError(command)
                message = response(config, f"{int(command, 16) | 0x800:04x}", fields)
                topic = f"dt/anker_power/A1783/{config.device_serial}/param_info".encode()
                prefix = len(topic).to_bytes(2, "big") + topic
                if retained_first and first_status and command == "0100":
                    writer.write(mqtt_packet(0x31, prefix + message))
                    await writer.drain()
                    await asyncio.sleep(0.1)
                first_status = False
                writer.write(mqtt_packet(0x32, prefix + b"\x00\x02" + message))
                await writer.drain()
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            writer.close()
    return asyncio.create_task(respond()), captured, writer


def test_tls_mqtt_native_controls_freshness_and_cleanup(lab):
    async def run():
        config, directory = lab
        server = LocalMqttServer(config, directory)
        await server.start(host="127.0.0.1", port=0)
        port = server._server.sockets[0].getsockname()[1]
        task, captured, writer = await fake_station(config, directory, port, retained_first=True)
        try:
            await asyncio.sleep(0.05)
            assert server.last_seen is None  # Retained reading cannot establish freshness.
            async with asyncio.timeout(2):
                while not server.snapshot()["available"]:
                    await asyncio.sleep(0.01)
            assert config.device_serial not in json.dumps(server.snapshot())
            with pytest.raises(PermissionError):
                await server.set_ac_charging_power(1700)
            assert not any(frame.command.hex() == "0101" for frame in captured)
            server.allow_control = True
            result = await server.set_ac_charging_power(1700)
            assert result["metrics"]["ac_charging_power_limit_w"] == 1700
            assert result["metrics"]["ac_output_enabled"] == 1
            result = await server.set_ac_charging_power(1800)
            assert result["metrics"]["ac_charging_power_limit_w"] == 1800
            await server.connection.request(server.commands.readiness())
            server.last_seen = time.time() - 31
            assert not server.snapshot()["available"]
            writer.write(mqtt_packet(0xE0, b""))
            await writer.drain()
            await asyncio.wait_for(task, 3)
            await writer.wait_closed()
            await asyncio.sleep(0.05)
            assert not server.snapshot()["available"]
        finally:
            await asyncio.wait_for(server.stop(), 5)
            await asyncio.wait_for(task, 5)
        assert set(frame.command.hex() for frame in captured) <= {"0100", "0101", "0089"}
        assert (directory / "mqtt-events.jsonl").stat().st_mode & 0o777 == 0o600
    asyncio.run(run())


def test_mqtt_oversize_length_rejected_before_reading_payload():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"\x30\x81\x80\x08")  # 131073 bytes.
        with pytest.raises(ValueError, match="too large"):
            await read_mqtt(reader)
    asyncio.run(run())


def test_namespace_cleanup_returns_adapter_before_deleting_namespace(lab, monkeypatch):
    config, directory = lab
    ap = IsolatedAP(config, directory)
    ap.created = ap.moved = True
    calls = []
    monkeypatch.setattr(ap, "_run", lambda *args: calls.append(args) or "")
    ap.stop()
    assert any(args[-5:] == ("ip", "link", "set", config.interface, "down") for args in calls)
    move = next(i for i, args in enumerate(calls) if "phy" in args)
    delete = next(i for i, args in enumerate(calls) if args[:3] == ("ip", "netns", "del"))
    assert move < delete
    assert not ap.created and not ap.moved


def test_namespace_retained_if_adapter_cannot_return(lab, monkeypatch):
    ap = IsolatedAP(*lab)
    ap.created = ap.moved = True
    calls = []
    def fail(*args):
        calls.append(args)
        if "phy" in args:
            raise OSError("simulated")
        return ""
    monkeypatch.setattr(ap, "_run", fail)
    with pytest.raises(RuntimeError, match="retained"):
        ap.stop()
    assert not any(args[:3] == ("ip", "netns", "del") for args in calls)


@pytest.mark.parametrize("existing_namespace", [False, True])
def test_ap_refuses_existing_namespace_or_active_adapter_before_mutation(lab, monkeypatch, existing_namespace):
    ap = IsolatedAP(*lab)
    monkeypatch.setattr("solix_gen2.isolated_ap.os.geteuid", lambda: 0)
    monkeypatch.setattr("solix_gen2.isolated_ap.shutil.which", lambda value: value)
    calls = []
    def run(*args):
        calls.append(args)
        if args == ("ip", "netns", "list"):
            return ap.config.namespace + "\n" if existing_namespace else ""
        if args[:3] == ("ip", "-j", "link"):
            return '[{"flags":["UP"]}]'
        raise AssertionError("Must not reach any mutation")
    monkeypatch.setattr(ap, "_run", run)
    with pytest.raises(RuntimeError, match="existing" if existing_namespace else "DOWN"):
        ap.start()
    assert not ap.created and not ap.moved
    assert not any(args[:3] == ("ip", "netns", "add") for args in calls)


def test_http_adapter_marks_stale_worker_and_readings_unavailable(lab, monkeypatch):
    config, directory = lab
    service = LabMonitorService(config, directory)
    private_write(directory / "status.json", json.dumps({"name": config.name, "connected": True,
                  "last_seen_timestamp": time.time() - 40, "metrics": {}}))
    assert not service.snapshot(config.name)["available"]
    assert not service.snapshot(config.name)["metrics"]


def test_native_http_auth_and_freshness(lab):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer
    from solix_gen2.server import create_app
    async def run():
        config, directory = lab
        service = LabMonitorService(config, directory)
        private_write(directory / "status.json", json.dumps({"name": config.name, "connected": True,
                      "last_seen_timestamp": time.time(), "metrics": {"battery_percentage": 90}}))
        async with TestServer(create_app(service, token="test-token")) as server:
            async with TestClient(server) as client:
                assert (await client.get("/devices")).status == 401
                headers = {"Authorization": "Bearer test-token"}
                value = await (await client.get("/devices/ups", headers=headers)).json()
                assert value["available"] and value["metrics"]["battery_percentage"] == 90
                assert config.device_serial not in json.dumps(value)
                private_write(directory / "status.json", json.dumps({"name": config.name, "connected": False,
                              "last_seen_timestamp": time.time(), "metrics": {"battery_percentage": 90}}))
                assert (await client.get("/health", headers=headers)).status == 503
    asyncio.run(run())


def test_request_timeout_closes_connection_and_prevents_late_ack_reuse(lab):
    from solix_gen2.mqtt_intercept import _Connection
    class Writer:
        closed = False
        def is_closing(self):
            return self.closed
        def write(self, data):
            pass
        async def drain(self):
            pass
        def close(self):
            self.closed = True
    async def run():
        config, directory = lab
        server = LocalMqttServer(config, directory)
        writer = Writer()
        connection = _Connection(server, asyncio.StreamReader(), writer)
        connection.subscribed = True
        with pytest.raises(TimeoutError):
            await connection.request(server.commands.ac_charging_power(1700), timeout=0.01)
        assert writer.closed and connection.pending is None
        with pytest.raises(ConnectionError):
            await connection.request(server.commands.ac_charging_power(1800), timeout=0.01)
    asyncio.run(run())
