"""Offline AP profiles: no adapter, socket, subprocess, or station access."""

import asyncio
import base64
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from solix_link import ap_service_cli, interactive
from solix_link.ap_service_config import APServiceConfig, initialize_ap_service, load_ap_service, private_write
from solix_link.config import DeviceConfig, save_config
from solix_link.mqtt_intercept import LocalMqttServer, native_response
from solix_link.ap_service import api_response, http_reply
from solix_link.protocol import DATA_RESPONSE, Model, build_packet


def profile(model=Model.C2000_GEN2):
    product = "A1763" if model == Model.C1000_GEN2 else "A1783"
    return APServiceConfig("ups", "wlan_ap", "phy9", "AT", product + "SYNTHETIC001", "a" * 40,
                           model=model, timezone_name="Europe/Vienna")


@pytest.mark.parametrize("model,product", [(Model.C1000_GEN2, "A1763"), (Model.C2000_GEN2, "A1783")])
def test_profile_bootstrap_model_roundtrip_and_identity(tmp_path, model, product):
    config = profile(model)
    path = initialize_ap_service(tmp_path / "private", config)
    document = json.loads(path.read_text())
    assert document["model"] == model.value
    loaded = load_ap_service(path)
    assert loaded == config and loaded.model is model and loaded.product == product
    server = LocalMqttServer(loaded, path.parent)
    assert server.commands.model is model
    assert server.topic == f"cmd/anker_power/{product}/{config.device_serial}/req"
    assert server.snapshot()["model"] == model.value
    frame = base64.b64encode(build_packet(DATA_RESPONSE, bytes.fromhex("0889"), b"\x00")).decode()
    def response(pn):
        return json.dumps({"payload": json.dumps({"sn": config.device_serial, "pn": pn, "data": frame})}).encode()
    assert native_response(response(product), config).command.hex() == "0889"
    assert native_response(response("A1783" if product == "A1763" else "A1763"), config) is None


def test_older_profile_without_model_retains_c2000_default(tmp_path):
    document = asdict(profile())
    document.pop("model")
    path = tmp_path / "ap_service.json"
    private_write(path, json.dumps(document))
    config = load_ap_service(path)
    assert config.model is Model.C2000_GEN2 and config.product == "A1783"


@pytest.mark.parametrize("model,chunked", [(Model.C1000_GEN2, False), (Model.C2000_GEN2, True)])
def test_credential_reply_uses_observed_model_framing(model, chunked):
    config = profile(model)
    credentials = b'{"code":0,"msg":"success","data":{}}'
    body, actual = api_response("//equipment/devicemanage/get_mqtt_info",
                               {"device_sn": config.device_serial}, config, credentials)
    wire = http_reply(body, credentials=actual)
    assert actual is chunked
    assert (b"Transfer-Encoding: chunked" in wire) is chunked
    if not chunked:
        headers, received = wire.split(b"\r\n\r\n", 1)
        assert f"Content-Length: {len(credentials)}".encode() in headers
        assert received == credentials


@pytest.mark.parametrize("model", [Model.C1000, Model.C300, "unknown", None, True])
def test_profile_rejects_unsupported_models_in_memory_and_json(tmp_path, model):
    document = asdict(profile())
    document["model"] = model
    with pytest.raises(ValueError):
        APServiceConfig(**document)
    path = tmp_path / "ap_service.json"
    private_write(path, json.dumps(document))
    with pytest.raises(ValueError):
        load_ap_service(path)


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_cli_init_uses_saved_paired_model_without_hardware(monkeypatch, tmp_path, model):
    config_path = tmp_path / "config.json"
    device = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, "a" * 40)
    save_config([device], config_path)
    serial = tmp_path / "serial"
    private_write(serial, profile(model).device_serial)
    captured = []
    monkeypatch.setattr(ap_service_cli, "initialize_ap_service", lambda directory, config: captured.append(config))
    args = SimpleNamespace(command="ap-service-init", config=config_path, name="ups", interface="wlan_ap",
                           phy="phy9", country="AT", serial_file=serial, account_id_file=None,
                           directory=tmp_path / "private")
    ap_service_cli.dispatch(args)
    assert len(captured) == 1 and captured[0].model is model
    assert captured[0].account_id == device.client_id


@pytest.mark.parametrize("model,client_id", [(Model.C1000, None), (Model.C300, None),
                                            (Model.C1000_GEN2, None), (Model.C2000_GEN2, None)])
def test_cli_and_guided_setup_reject_legacy_or_unpaired_models(tmp_path, model, client_id):
    path = tmp_path / "config.json"
    device = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, client_id)
    save_config([device], path)
    with pytest.raises(ValueError, match="pair"):
        ap_service_cli._device(SimpleNamespace(config=path), "ups")
    with pytest.raises(ValueError, match="paired"):
        interactive.setup_ap_service(device, tmp_path / "private")


def test_provision_rejects_mismatched_model_before_monitor_or_ap_start(monkeypatch, tmp_path):
    path = tmp_path / "ap_service.json"
    private_write(path, json.dumps(asdict(profile(Model.C1000_GEN2))))
    paired = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C2000_GEN2, "a" * 40)
    monkeypatch.setattr(ap_service_cli, "_device", lambda *_: paired)
    stopped = []
    class FakeAP:
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            raise AssertionError("mismatched profile must not start an AP")
        def stop(self):
            stopped.append(True)
    def monitor(*_args, **_kwargs):
        raise AssertionError("mismatched profile must not create a monitor")
    monkeypatch.setattr(ap_service_cli, "IsolatedAP", FakeAP)
    monkeypatch.setattr(ap_service_cli, "SolixMonitor", monitor)
    args = SimpleNamespace(duration=1, directory=tmp_path, hostapd="hostapd", dnsmasq="dnsmasq", provision=True)
    with pytest.raises(ValueError, match="model does not match"):
        asyncio.run(ap_service_cli.run_ap_service(args))
    assert stopped == [True]


@pytest.mark.parametrize("model,expected_count", [(Model.C1000, 1), (Model.C300, 1),
                                                  (Model.C1000_GEN2, 6), (Model.C2000_GEN2, 6)])
def test_guided_native_menu_only_offers_gen2_profiles(monkeypatch, tmp_path, model, expected_count):
    device = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model)
    captured = []
    def choose(_title, options):
        captured.append(options)
        return None
    monkeypatch.setattr(interactive, "choose", choose)
    interactive.mqtt_menu(device, tmp_path / "config.json", tmp_path / "private")
    assert len(captured[0]) == expected_count


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_guided_setup_saves_selected_model_with_mocked_serial(monkeypatch, tmp_path, model):
    device = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, "a" * 40)
    captured = []
    async def read_serial(_device):
        return profile(model).device_serial
    monkeypatch.setattr(interactive, "wifi_adapters", lambda: [("wlan_ap", "phy9")])
    monkeypatch.setattr(interactive, "choose", lambda *_: 0)
    monkeypatch.setattr(interactive, "prompt", lambda *_: "AT")
    monkeypatch.setattr(interactive, "read_serial", read_serial)
    monkeypatch.setattr("getpass.getpass", lambda *_: "")
    monkeypatch.setattr(interactive, "initialize_ap_service", lambda directory, config: captured.append(config))
    interactive.setup_ap_service(device, tmp_path / "private")
    assert len(captured) == 1 and captured[0].model is model
