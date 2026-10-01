"""Wi-Fi CLI contracts use synthetic credentials and fake Bluetooth only."""

import asyncio
import json

import pytest

from solix_link import cli
from solix_link.config import DeviceConfig, load_config, save_config
from solix_link.protocol import Model


class FakeMonitor:
    def __init__(self, *_args, fail=False, **kwargs):
        self.fail = fail
        self.calls = []
        self.connected = False

    async def connect(self, **_kwargs):
        self.connected = True

    async def send_wifi_provisioning(self, **values):
        self.calls.append(values)
        if self.fail:
            raise RuntimeError("Synthetic provisioning failure")
        return {"4824": "00", "4825": "00"}

    async def disconnect(self):
        self.connected = False


def arguments(tmp_path, *, account_id=None, country_code=None):
    password = tmp_path / "wifi-password.txt"
    password.write_text("demo-not-secret\n")
    flags = ["wifi-setup", "--name", "ups", "--ssid", "synthetic-lab", "--password-file", str(password),
             "--api-url", "http://192.0.2.1", "--allow-http", "--config", str(tmp_path / "devices.json")]
    if account_id is not None:
        flags.extend(["--account-id", account_id])
    if country_code is not None:
        flags.extend(["--country-code", country_code])
    return cli.parser().parse_args(flags)


def test_original_generates_persistent_local_id_and_reuses_after_failure(tmp_path, monkeypatch, capsys):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000)], path)
    monitors = []
    def monitor(*args, **kwargs):
        instance = FakeMonitor(*args, fail=not monitors, **kwargs)
        monitors.append(instance)
        return instance
    monkeypatch.setattr(cli, "SolixMonitor", monitor)
    args = arguments(tmp_path)
    with pytest.raises(RuntimeError, match="Synthetic provisioning failure"):
        asyncio.run(cli._wifi_setup(args))
    saved = load_config(path)[0]
    generated = saved.client_id
    assert generated is not None and len(generated) == 40 and int(generated, 16) >= 0
    assert saved.protocol == "legacy" and path.stat().st_mode & 0o777 == 0o600
    assert monitors[0].calls[0]["account_id"] == generated and not monitors[0].connected
    asyncio.run(cli._wifi_setup(args))
    assert load_config(path)[0].client_id == generated
    assert monitors[1].calls[0]["account_id"] == generated
    assert monitors[1].calls[0]["country_code"] == "US"
    assert not monitors[1].connected
    output = capsys.readouterr()
    assert output.err.count("Generated and saved a local provisioning ID") == 1
    assert generated not in output.out + output.err
    assert "demo-not-secret" not in output.out + output.err
    assert json.loads(output.out)["ble_replies"] == {"4824": "00", "4825": "00"}


@pytest.mark.parametrize("saved_id", [None, "a" * 40])
def test_explicit_original_id_is_used_without_overwriting_config_and_country_forwarded(tmp_path, monkeypatch, capsys, saved_id):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C1000, saved_id)], path)
    monitor = FakeMonitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    explicit = "e" * 40
    asyncio.run(cli._wifi_setup(arguments(tmp_path, account_id=explicit, country_code="AT")))
    assert load_config(path)[0].client_id == saved_id
    assert monitor.calls[0]["account_id"] == explicit and monitor.calls[0]["country_code"] == "AT"
    output = capsys.readouterr()
    assert "Generated" not in output.err and explicit not in output.out + output.err


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_gen2_uses_existing_paired_id_without_regeneration(tmp_path, monkeypatch, capsys, model):
    path = tmp_path / "devices.json"
    paired = "b" * 40
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, paired)], path)
    monitor = FakeMonitor()
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: monitor)
    asyncio.run(cli._wifi_setup(arguments(tmp_path)))
    assert monitor.calls[0]["account_id"] == paired and monitor.calls[0]["country_code"] == "US"
    assert load_config(path)[0].client_id == paired
    output = capsys.readouterr()
    assert "Generated" not in output.err and paired not in output.out + output.err


@pytest.mark.parametrize("model", [Model.C1000_GEN2, Model.C2000_GEN2])
def test_gen2_missing_id_is_rejected_without_connecting_or_generating(tmp_path, monkeypatch, model):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model)], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: pytest.fail("No connection without pairing ID"))
    with pytest.raises(ValueError, match="paired client ID"):
        asyncio.run(cli._wifi_setup(arguments(tmp_path)))
    assert load_config(path)[0].client_id is None


@pytest.mark.parametrize("model,protocol", [(Model.C300, "legacy"), (Model.C1000_GEN2, "legacy")])
def test_wifi_setup_rejects_other_profiles_before_connecting(tmp_path, monkeypatch, model, protocol):
    path = tmp_path / "devices.json"
    save_config([DeviceConfig("ups", "AA:BB:CC:DD:EE:01", model, protocol=protocol)], path)
    monkeypatch.setattr(cli, "SolixMonitor", lambda *_args, **_kwargs: pytest.fail("No connection for unsupported profile"))
    with pytest.raises(ValueError, match="original C1000 legacy/Prime or Gen 2 Prime"):
        asyncio.run(cli._wifi_setup(arguments(tmp_path)))
    assert load_config(path)[0].client_id is None
