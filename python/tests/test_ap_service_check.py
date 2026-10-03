"""Offline setup checks reject inconsistent private data without exposing it."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from solix_link.ap_service_check import CONFIG_LIMIT, _certificate_dates, check_ap_service
from solix_link.ap_service_config import APServiceConfig, add_ap_service_device, initialize_ap_service, private_write
from solix_link.cli import parser
from solix_link.config import DeviceConfig, save_config
from solix_link.mqtt_credentials import encrypt_device_credential
from solix_link.protocol import Model


@pytest.fixture
def setup(tmp_path):
    primary = APServiceConfig("private_target_01", "wlan_unused", "phy9", "AT",
        "A1763SYNTHETIC001", "a"*40, model=Model.C1000_GEN2,
        ssid="PRIVATE-SYNTHETIC-SSID", passphrase="private-synthetic-passphrase")
    directory = tmp_path/"private"
    initialize_ap_service(directory, primary)
    paired = tmp_path/"ble.json"
    save_config([DeviceConfig(primary.name, "02:11:22:33:44:55", primary.model,
                            primary.account_id, "prime")], paired)
    return primary, directory, paired


def codes(result):
    return {row["code"] for row in result["findings"]}


def test_certificate_dates_use_modern_utc_properties_and_lazy_legacy_fallback():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2027, 1, 1, tzinfo=timezone.utc)
    class ModernCertificate:
        not_valid_before_utc = start
        not_valid_after_utc = end
        @property
        def not_valid_before(self):
            raise AssertionError("Deprecated date must not be read when UTC property exists")
        @property
        def not_valid_after(self):
            raise AssertionError("Deprecated date must not be read when UTC property exists")
    class LegacyCertificate:
        not_valid_before = start.replace(tzinfo=None)
        not_valid_after = end.replace(tzinfo=None)
    assert _certificate_dates(ModernCertificate()) == (start, end)
    assert _certificate_dates(LegacyCertificate()) == (start, end)


def assert_redacted(result, primary, directory, paired):
    text = json.dumps(result)
    for private in (primary.device_serial, primary.account_id, primary.passphrase,
                    primary.ssid, primary.name, primary.broker_host,
                    "02:11:22:33:44:55", str(directory), str(paired),
                    (directory/"client.pem").read_text(),
                    (directory/"client-key.pem").read_text()):
        assert private not in text
        assert json.dumps(private) not in text
    assert "BEGIN CERTIFICATE" not in text and "PRIVATE KEY" not in text


def test_good_setup_is_redacted_read_only_and_does_not_claim_live_binding(setup):
    primary, directory, paired = setup
    before = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
              for p in directory.iterdir() if p.is_file()}
    result = check_ap_service(directory, paired)
    assert result["ok"] and result["read_only"] and result["live_confirmation_required"]
    assert result["findings"] == []
    assert result["profiles"][0] == {
        "profile": "primary", "configuration_valid": True, "model": "c1000_gen2",
        "identity_origin": "unknown", "native_binding": "not_checked",
        "generated_native_identity_evidence": "verified_only_for_main_1.1.4.9_radio_0.3.3.0",
        "saved_ble_pairing": "prime_identity_present", "native_identity_matches_ble": True,
    }
    assert_redacted(result, primary, directory, paired)
    assert before == {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in before}


def test_optional_pairing_and_ca_enrollment_key_do_not_break_runtime_check(setup):
    _, directory, paired = setup
    (directory/"ca-key.pem").unlink()
    for path in (None, paired.with_name("missing.json")):
        result = check_ap_service(directory, path)
        assert result["ok"]
        assert any(row["severity"] == "warning" for row in result["findings"])
        assert result["profiles"][0]["saved_ble_pairing"] == "not_checked"


@pytest.mark.parametrize("mutation,expected", [
    ("wrong_type", "profile_schema_invalid"),
    ("missing_default", "profile_schema_invalid"),
    ("unknown_model", "profile_schema_invalid"),
    ("invalid_identity", "profile_schema_invalid"),
    ("duplicate_key", "profile_json_invalid"),
    ("invalid_json", "profile_json_invalid"),
    ("deep_json", "profile_json_invalid"),
    ("oversized", "private_file_invalid"),
    ("public_permissions", "private_file_invalid"),
    ("symlink", "private_file_invalid"),
    ("fifo", "private_file_invalid"),
])
def test_malformed_profile_inputs_are_bounded_and_never_print_private_values(setup, mutation, expected):
    primary, directory, paired = setup
    path = directory/"ap_service.json"
    data = asdict(primary)
    if mutation == "wrong_type":
        data["account_id"] = {"secret": primary.account_id}
    elif mutation == "missing_default":
        del data["passphrase"]
    elif mutation == "unknown_model":
        data["model"] = primary.account_id
    elif mutation == "invalid_identity":
        data["device_serial"] = primary.passphrase
    if mutation == "duplicate_key":
        private_write(path, '{"account_id":"'+primary.account_id+'","account_id":"duplicate"}')
    elif mutation == "invalid_json":
        private_write(path, primary.account_id)
    elif mutation == "deep_json":
        private_write(path, '{"nested":'+"["*2000+"0"+"]"*2000+"}")
    elif mutation == "oversized":
        private_write(path, b"x"*(CONFIG_LIMIT+1))
    else:
        private_write(path, json.dumps(data))
    if mutation == "public_permissions":
        path.chmod(0o644)
    elif mutation == "symlink":
        target = directory/"external.json"
        path.rename(target); path.symlink_to(target)
    elif mutation == "fifo":
        path.unlink(); os.mkfifo(path, 0o600)
    result = check_ap_service(directory, paired)
    assert not result["ok"] and expected in codes(result)
    assert_redacted(result, primary, directory, paired)


@pytest.mark.parametrize("filename,mutation,expected", [
    ("client-key.pem", "other_key", "certificate_key_mismatch"),
    ("server.pem", "invalid", "certificate_invalid"),
    ("client-key.pem", "invalid", "private_key_invalid"),
    ("server-key.pem", "public", "private_file_invalid"),
    ("mqtt-response.json", "invalid", "credential_response_json_invalid"),
    ("client.pem", "missing", "required_file_missing"),
])
def test_invalid_certificate_material_fails_with_fixed_messages(setup, filename, mutation, expected):
    primary, directory, paired = setup
    path = directory/filename
    if mutation == "other_key":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_write(path, key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    elif mutation == "invalid":
        private_write(path, primary.account_id)
    elif mutation == "public":
        path.chmod(0o644)
    else:
        path.unlink()
    result = check_ap_service(directory, paired)
    assert not result["ok"] and expected in codes(result)
    # Compare only retained fields; this test can deliberately remove client.pem.
    text = json.dumps(result)
    assert primary.account_id not in text and primary.device_serial not in text and primary.passphrase not in text


@pytest.mark.parametrize("field", ["device_sn", "thing_name", "endpoint_addr", "certificate_pem", "private_key", "aws_root_ca1_pem"])
def test_encrypted_response_must_match_saved_station_and_tls_material(setup, field):
    primary, directory, paired = setup
    path = directory/"mqtt-response.json"
    response = json.loads(path.read_text())
    response["data"][field] = primary.passphrase
    private_write(path, json.dumps(response))
    result = check_ap_service(directory, paired)
    assert not result["ok"] and "credential_response_mismatch" in codes(result)
    assert_redacted(result, primary, directory, paired)


@pytest.mark.parametrize("field", ["msg", "certificate_id"])
def test_response_metadata_required_by_radio_parser_is_checked(setup, field):
    primary, directory, paired = setup
    path = directory/"mqtt-response.json"
    response = json.loads(path.read_text())
    del (response if field == "msg" else response["data"])[field]
    private_write(path, json.dumps(response))
    result = check_ap_service(directory, paired)
    assert not result["ok"] and "credential_response_mismatch" in codes(result)
    assert_redacted(result, primary, directory, paired)


def test_dates_signature_tls_role_and_endpoint_are_real_certificate_checks(setup):
    primary, directory, paired = setup
    now = datetime.now(timezone.utc)
    assert "certificate_date_invalid" in codes(check_ap_service(directory, paired, now=now+timedelta(days=900)))
    assert "certificate_expiring" in codes(check_ap_service(directory, paired, now=now+timedelta(days=810)))
    foreign = directory.parent/"other"
    initialize_ap_service(foreign, replace(primary, name="other"))
    private_write(directory/"server.pem", (foreign/"server.pem").read_bytes())
    private_write(directory/"server-key.pem", (foreign/"server-key.pem").read_bytes())
    assert "certificate_signature_invalid" in codes(check_ap_service(directory, paired))
    private_write(directory/"server.pem", (directory/"client.pem").read_bytes())
    private_write(directory/"server-key.pem", (directory/"client-key.pem").read_bytes())
    result = check_ap_service(directory, paired)
    assert {"certificate_role_invalid", "server_endpoint_mismatch"} <= codes(result)
    assert_redacted(result, primary, directory, paired)


def test_shared_profiles_validate_identity_network_and_certificate_routing(setup):
    primary, directory, paired = setup
    original = replace(primary, name="original", model=Model.C1000,
                       device_serial="SYNTHETICGEN1001", account_id="b"*40)
    child = add_ap_service_device(directory, original).parent
    result = check_ap_service(directory, paired)
    assert result["ok"] and len(result["profiles"]) == 2
    assert result["profiles"][1]["generated_native_identity_evidence"] == "unverified"
    assert "generated_native_identity_unverified" in codes(result)
    data = asdict(original);data.update(device_serial=primary.device_serial, model=primary.model.value,
                                      ssid="different-private-network")
    private_write(child/"ap_service.json", json.dumps(data))
    private_write(child/"client.pem", (directory/"client.pem").read_bytes())
    result = check_ap_service(directory, paired)
    assert {"routing_identity_conflict", "shared_network_mismatch", "client_certificate_conflict"} <= codes(result)
    assert not result["ok"]
    assert_redacted(result, primary, directory, paired)


def test_pairing_readiness_distinguishes_models_protocols_and_id_origins(setup):
    primary, directory, paired = setup
    for model, protocol, identity, code, good in (
        (Model.C1000, "prime", primary.account_id, "provisioning_model_mismatch", False),
        (primary.model, "legacy", None, "provisioning_pairing_incomplete", True),
        (primary.model, "prime", "b"*40, "native_ble_identity_differs", True),
    ):
        save_config([DeviceConfig(primary.name, "02:11:22:33:44:55", model, identity, protocol)], paired)
        result = check_ap_service(directory, paired)
        assert result["ok"] is good and code in codes(result)
        assert result["profiles"][0]["identity_origin"] == "unknown"
        assert_redacted(result, primary, directory, paired)
    private_write(paired, json.dumps({"devices": [{"name": primary.name, "address": primary.account_id}]}))
    result = check_ap_service(directory, paired)
    assert not result["ok"] and "pairing_config_invalid" in codes(result)
    assert primary.account_id not in json.dumps(result)


def test_c2000_native_generated_identity_is_not_marked_verified(setup):
    primary, directory, paired = setup
    data = asdict(replace(primary, model=Model.C2000_GEN2))
    private_write(directory/"ap_service.json", json.dumps(data))
    result = check_ap_service(directory)
    assert result["ok"] and result["profiles"][0]["generated_native_identity_evidence"] == "unverified"
    assert "generated_native_identity_unverified" in codes(result)


def test_root_or_devices_symlinks_and_public_directories_fail_without_following(setup):
    _, directory, _ = setup
    link = directory.parent/"link";link.symlink_to(directory, target_is_directory=True)
    assert not check_ap_service(link)["ok"]
    directory.chmod(0o755)
    assert not check_ap_service(directory)["ok"]
    directory.chmod(0o700)
    (directory/"devices").symlink_to(directory.parent, target_is_directory=True)
    assert "private_directory_invalid" in codes(check_ap_service(directory))


def test_unrelated_live_artifacts_are_not_read_or_validated(setup):
    _, directory, paired = setup
    (directory/"service.log").write_text("UNRELATED PRIVATE LOG")
    (directory/"status.json").write_text("invalid JSON is unrelated")
    os.mkfifo(directory/"control.sock", 0o644)
    result = check_ap_service(directory, paired)
    assert result["ok"] and result["findings"] == []


def test_station_directory_enumeration_has_a_fixed_bound(setup):
    _, directory, paired = setup
    children = directory/"devices"
    children.mkdir(mode=0o700)
    for index in range(65):
        (children/f"unused-{index}").touch(mode=0o600)
    result = check_ap_service(directory, paired)
    assert not result["ok"] and "profile_directory_invalid" in codes(result)


def test_cli_check_requires_no_device_request_and_errors_exit_nonzero(setup, monkeypatch, capsys):
    from solix_link import ap_service_cli
    from solix_link.cli import main
    primary, directory, paired = setup
    def forbidden(*args, **kwargs):
        raise AssertionError("No runtime or device operation is allowed")
    monkeypatch.setattr(ap_service_cli, "ap_service_request", forbidden)
    monkeypatch.setattr(ap_service_cli, "run_ap_service", forbidden)
    monkeypatch.setattr(ap_service_cli, "SolixMonitor", forbidden)
    monkeypatch.setattr(ap_service_cli, "IsolatedAP", forbidden)
    args = parser().parse_args(["ap-service-check", "--directory", str(directory)])
    assert args.config is None
    assert main(["ap-service-check", "--directory", str(directory), "--config", str(paired)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    (directory/"client-key.pem").chmod(0o644)
    assert main(["ap-service-check", "--directory", str(directory)]) == 1
    captured = capsys.readouterr()
    assert not json.loads(captured.out)["ok"]
    assert primary.account_id not in captured.out+captured.err
    assert str(directory) not in captured.out+captured.err
