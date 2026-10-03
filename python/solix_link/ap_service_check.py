"""Bounded, redacted inspection of saved local AP setup; no device or services.

Results contain fixed codes/messages and ordinal profile labels only. Local
files cannot establish radio binding, identity origin, firmware, connectivity,
adapter availability or uninterrupted output. This module never repairs files.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import stat

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from .ap_service_config import APServiceConfig, MAX_AP_DEVICES, SHARED_AP_FIELDS
from .config import DeviceConfig
from .mqtt_credentials import decrypt_device_credential
from .protocol import Model


CONFIG_LIMIT = 65_536
PEM_LIMIT = 65_536
RESPONSE_LIMIT = 262_144
DIRECTORY_ENTRY_LIMIT = 64
BLE_DEVICE_LIMIT = 64
JSON_DEPTH_LIMIT = 32


def _certificate_dates(certificate) -> tuple[datetime, datetime]:
    """Use modern UTC properties without evaluating deprecated fallbacks."""
    try:
        start = certificate.not_valid_before_utc
    except AttributeError:
        start = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    try:
        end = certificate.not_valid_after_utc
    except AttributeError:
        end = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    return start, end


class _Inspector:
    def __init__(self, now: datetime):
        self.now = now
        self.findings: list[dict[str, str]] = []

    def add(self, scope: str, severity: str, code: str, message: str) -> None:
        self.findings.append({"profile": scope, "severity": severity,
                              "code": code, "message": message})

    def directory(self, path: Path, scope: str) -> bool:
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                raise ValueError
        except (OSError, ValueError):
            self.add(scope, "error", "private_directory_invalid",
                     "Use an accessible owner-only directory without symlinks (mode 700).")
            return False
        return True

    def read(self, path: Path, scope: str, limit: int, *, optional: bool = False) -> bytes | None:
        descriptor = None
        filename = path.name if path.name in {
            "ap_service.json", "ca.pem", "ca-key.pem", "server.pem", "server-key.pem",
            "client.pem", "client-key.pem", "mqtt-response.json",
        } else "saved BLE config"
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > limit:
                raise ValueError
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                content = stream.read(limit+1)
            if len(content) > limit:
                raise ValueError
            return content
        except FileNotFoundError:
            self.add(scope, "warning" if optional else "error", "optional_file_missing" if optional else "required_file_missing",
                     f"{filename} is missing; retain or restore the matching saved setup.")
        except (OSError, ValueError):
            self.add(scope, "error", "private_file_invalid",
                     f"Check {filename}: it must be a bounded, readable owner-only regular file without symlinks (mode 600).")
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return None

    def document(self, content: bytes | None, scope: str, kind: str) -> dict | None:
        if content is None:
            return None
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        try:
            text = content.decode("utf-8")
            depth, quoted, escaped = 0, False, False
            for character in text:
                if quoted:
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == '"':
                        quoted = False
                elif character == '"':
                    quoted = True
                elif character in "[{":
                    depth += 1
                    if depth > JSON_DEPTH_LIMIT:
                        raise ValueError
                elif character in "]}":
                    depth -= 1
            document = json.loads(text, object_pairs_hook=unique_object)
            if not isinstance(document, dict):
                raise ValueError
            return document
        except (ValueError, UnicodeError, RecursionError):
            self.add(scope, "error", f"{kind}_json_invalid",
                     f"Restore valid {kind} JSON with one object and no duplicate keys.")
            return None

    def certificate(self, content: bytes | None, scope: str, role: str):
        if content is None:
            return None
        try:
            certificate = x509.load_pem_x509_certificate(content)
            start, end = _certificate_dates(certificate)
            if not start <= self.now <= end:
                self.add(scope, "error", "certificate_date_invalid",
                         f"The {role} certificate is not currently valid; restore the correct certificate set or plan reprovisioning.")
            elif end-self.now <= timedelta(days=30):
                self.add(scope, "warning", "certificate_expiring",
                         f"The {role} certificate expires within 30 days; plan certificate renewal and reprovisioning.")
            return certificate
        except Exception:
            self.add(scope, "error", "certificate_invalid", f"Restore a valid PEM {role} certificate from the matching setup.")
            return None

    def key(self, content: bytes | None, scope: str, role: str):
        if content is None:
            return None
        try:
            return serialization.load_pem_private_key(content, password=None)
        except Exception:
            self.add(scope, "error", "private_key_invalid", f"Restore the unencrypted PEM {role} key from the matching setup.")
            return None

    @staticmethod
    def public_key(key) -> bytes:
        return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def pair(self, certificate, key, scope: str, role: str) -> None:
        if certificate is not None and key is not None:
            if self.public_key(certificate.public_key()) != self.public_key(key.public_key()):
                self.add(scope, "error", "certificate_key_mismatch", f"Restore the {role} certificate and its matching private key together.")

    def signature(self, certificate, ca, scope: str, role: str) -> None:
        if certificate is None or ca is None:
            return
        try:
            if certificate.issuer != ca.subject:
                raise ValueError
            key = ca.public_key()
            if isinstance(key, rsa.RSAPublicKey):
                key.verify(certificate.signature, certificate.tbs_certificate_bytes,
                           padding.PKCS1v15(), certificate.signature_hash_algorithm)
            elif isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(certificate.signature, certificate.tbs_certificate_bytes,
                           ec.ECDSA(certificate.signature_hash_algorithm))
            else:
                raise ValueError
        except Exception:
            self.add(scope, "error", "certificate_signature_invalid", f"Restore the {role} certificate signed by this AP's local CA.")

    def materials(self, path: Path, scope: str, config: APServiceConfig | None):
        ca = self.certificate(self.read(path/"ca.pem", scope, PEM_LIMIT), scope, "CA")
        certs, keys = {}, {}
        for role in ("server", "client"):
            certs[role] = self.certificate(self.read(path/f"{role}.pem", scope, PEM_LIMIT), scope, role)
            keys[role] = self.key(self.read(path/f"{role}-key.pem", scope, PEM_LIMIT), scope, role)
            self.pair(certs[role], keys[role], scope, role)
            self.signature(certs[role], ca, scope, role)
            if certs[role] is not None:
                try:
                    usage = certs[role].extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                    expected = ExtendedKeyUsageOID.SERVER_AUTH if role == "server" else ExtendedKeyUsageOID.CLIENT_AUTH
                    if expected not in usage:
                        raise ValueError
                    if certs[role].extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                        raise ValueError
                except Exception:
                    self.add(scope, "error", "certificate_role_invalid", f"Restore a non-CA {role} certificate with the correct TLS usage.")
        if ca is not None:
            self.signature(ca, ca, scope, "CA")
            try:
                if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                    raise ValueError
                if not ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
                    raise ValueError
            except Exception:
                self.add(scope, "error", "ca_role_invalid", "Restore the local CA certificate with certificate-signing usage.")
        if scope == "primary":
            ca_key = self.key(self.read(path/"ca-key.pem", scope, PEM_LIMIT, optional=True), scope, "CA")
            self.pair(ca, ca_key, scope, "CA")
        if config is not None and certs["server"] is not None:
            try:
                names = certs["server"].extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                if config.broker_host not in names.get_values_for_type(x509.DNSName):
                    raise ValueError
                if ipaddress.ip_address(config.gateway) not in names.get_values_for_type(x509.IPAddress):
                    raise ValueError
            except Exception:
                self.add(scope, "error", "server_endpoint_mismatch", "Restore the server certificate matching the configured broker hostname and gateway.")
        response = self.document(self.read(path/"mqtt-response.json", scope, RESPONSE_LIMIT), scope, "credential_response")
        if config is not None and response is not None:
            try:
                data = response["data"]
                if type(response["code"]) is not int or response["code"] != 0 or not isinstance(data, dict):
                    raise ValueError
                if not isinstance(response.get("msg"), str) or not response["msg"]:
                    raise ValueError
                certificate_id = data.get("certificate_id")
                if not isinstance(certificate_id, str) or not 1 <= len(certificate_id) <= 256 or not certificate_id.isascii() or any(ord(char) < 32 for char in certificate_id):
                    raise ValueError
                if data["device_sn"] != config.device_serial or data["thing_name"] != config.device_serial or data["endpoint_addr"] != config.broker_host:
                    raise ValueError
                wrapped_cert = x509.load_pem_x509_certificate(decrypt_device_credential(config.device_serial, data["certificate_pem"]))
                wrapped_key = serialization.load_pem_private_key(decrypt_device_credential(config.device_serial, data["private_key"]), password=None)
                response_ca = x509.load_pem_x509_certificate(data["aws_root_ca1_pem"].encode())
                if ca is None or certs["client"] is None or keys["client"] is None:
                    raise ValueError
                if wrapped_cert.fingerprint(hashes.SHA256()) != certs["client"].fingerprint(hashes.SHA256()) or response_ca.fingerprint(hashes.SHA256()) != ca.fingerprint(hashes.SHA256()):
                    raise ValueError
                if self.public_key(wrapped_key.public_key()) != self.public_key(keys["client"].public_key()):
                    raise ValueError
            except Exception:
                self.add(scope, "error", "credential_response_mismatch",
                         "Restore the saved MQTT response matching this profile's serial, endpoint, CA and client certificate/key; do not substitute another station's response.")
        return ca, certs["server"], certs["client"]


def _paired_devices(check: _Inspector, path: Path | None) -> list[DeviceConfig] | None:
    if path is None:
        check.add("shared", "warning", "pairing_not_checked", "Supply --config with the saved BLE config to check provisioning prerequisites offline.")
        return None
    content = check.read(path, "shared", CONFIG_LIMIT, optional=True)
    if content is None:
        return None
    document = check.document(content, "shared", "pairing")
    if document is None:
        return None
    try:
        entries = document["devices"]
        if not isinstance(entries, list) or len(entries) > BLE_DEVICE_LIMIT:
            raise ValueError
        devices = []
        for entry in entries:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("name", "address", "model")):
                raise ValueError
            for key in ("client_id", "owner_user_id", "protocol", "timezone_name"):
                if entry.get(key) is not None and not isinstance(entry[key], str):
                    raise ValueError
            if "client_id" in entry and "owner_user_id" in entry and entry["client_id"] != entry["owner_user_id"]:
                raise ValueError
            devices.append(DeviceConfig(entry["name"], entry["address"], Model(entry["model"]),
                entry.get("client_id", entry.get("owner_user_id")), entry.get("protocol"), entry.get("timezone_name")))
        if len({device.name for device in devices}) != len(devices):
            raise ValueError
        return devices
    except Exception:
        check.add("shared", "error", "pairing_config_invalid", "Restore a bounded valid BLE devices list with unique names and correctly shaped identifiers.")
        return None


def check_ap_service(directory: Path, paired_config: Path | None = None, *, now: datetime | None = None) -> dict:
    """Inspect saved setup without writes, services, OS probes or network access.

    ``ok`` means no local-file errors. Warnings and ``live_confirmation_required``
    remain: it does not establish that any station is currently bound/connected.
    Optional paired configuration is needed only to inspect future provisioning.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Setup-check time must be timezone aware")
    check = _Inspector(now.astimezone(timezone.utc))
    directory = Path(directory).absolute()
    reports = []
    if not check.directory(directory, "primary"):
        return {"schema": 1, "ok": False, "read_only": True, "live_confirmation_required": True,
                "profiles": [], "findings": check.findings}
    paths = [("primary", directory)]
    children = directory/"devices"
    if children.exists() or children.is_symlink():
        if check.directory(children, "shared"):
            try:
                with os.scandir(children) as iterator:
                    entries = []
                    for entry in iterator:
                        entries.append(entry)
                        if len(entries) > DIRECTORY_ENTRY_LIMIT:
                            raise ValueError
                for entry in sorted(entries, key=lambda item: item.name):
                    if entry.is_symlink():
                        check.add("shared", "error", "profile_symlink", "Remove symlinked station entries; use private saved profile directories.")
                    elif entry.is_dir(follow_symlinks=False):
                        label = f"device-{len(paths)}"
                        path = Path(entry.path)
                        if check.directory(path, label):
                            if (path/"ap_service.json").exists() or (path/"ap_service.json").is_symlink():
                                if len(paths) >= MAX_AP_DEVICES:
                                    check.add("shared", "error", "too_many_profiles", "Keep at most eight station profiles on one AP.")
                                    break
                                paths.append((label, path))
                            else:
                                check.add(label, "warning", "profile_config_missing", "A station directory has no ap_service.json and will not be routed.")
            except (OSError, ValueError):
                check.add("shared", "error", "profile_directory_invalid", "Check the station directory; the offline scan accepts at most 64 entries.")
    paired = _paired_devices(check, Path(paired_config) if paired_config is not None else None)
    configs, certificates = [], []
    expected_fields = {field.name for field in fields(APServiceConfig)}
    for scope, path in paths:
        config = None
        document = check.document(check.read(path/"ap_service.json", scope, CONFIG_LIMIT), scope, "profile")
        if document is not None:
            try:
                if set(document) != expected_fields or not all(isinstance(value, str) for value in document.values()):
                    raise ValueError
                config = APServiceConfig(**document)
            except Exception:
                check.add(scope, "error", "profile_schema_invalid", "Restore the complete saved AP configuration with a supported model and valid network/identity fields.")
        material = check.materials(path, scope, config)
        certificates.append((scope, material))
        if config is None:
            reports.append({"profile": scope, "configuration_valid": False})
            continue
        configs.append((scope, config))
        report = {"profile": scope, "configuration_valid": True, "model": config.model.value,
                  "identity_origin": "unknown", "native_binding": "not_checked",
                  "generated_native_identity_evidence": "unverified",
                  "saved_ble_pairing": "not_checked"}
        if config.model == Model.C1000_GEN2:
            report["generated_native_identity_evidence"] = "verified_only_for_main_1.1.4.9_radio_0.3.3.0"
        else:
            check.add(scope, "warning", "generated_native_identity_unverified", "Generated native identities remain unverified on this model; retain its known-working identity and establish independent recovery before an identity trial.")
        if paired is not None:
            device = next((item for item in paired if item.name == config.name), None)
            if device is None:
                report["saved_ble_pairing"] = "missing"
                check.add(scope, "warning", "provisioning_pairing_missing", "No matching saved BLE profile exists; provisioning needs the station's paired Prime profile and independent recovery.")
            elif device.model != config.model:
                report["saved_ble_pairing"] = "model_mismatch"
                check.add(scope, "error", "provisioning_model_mismatch", "The matching BLE profile has a different model; correct the saved selection before provisioning.")
            elif device.protocol != "prime" or not device.client_id:
                report["saved_ble_pairing"] = "not_prime_paired"
                check.add(scope, "warning", "provisioning_pairing_incomplete", "Provisioning requires a retained Prime pairing identifier; legacy monitoring alone is insufficient.")
            else:
                report["saved_ble_pairing"] = "prime_identity_present"
                report["native_identity_matches_ble"] = device.client_id == config.account_id
                if device.client_id != config.account_id:
                    check.add(scope, "warning", "native_ble_identity_differs", "Native and saved BLE identities differ; this can be intentional, but retain both and confirm model-specific recovery before provisioning.")
        reports.append(report)
    primary = next((config for scope, config in configs if scope == "primary"), None)
    names, serials, fingerprints = set(), set(), set()
    for scope, config in configs:
        if config.name in names or config.device_serial in serials:
            check.add(scope, "error", "routing_identity_conflict", "Station names and serials must be unique to route status and commands independently.")
        names.add(config.name)
        serials.add(config.device_serial)
        if scope != "primary":
            path = next(path for label, path in paths if label == scope)
            if config.name != path.name:
                check.add(scope, "error", "profile_name_mismatch", "The station directory name must match its saved profile name.")
            if primary is not None and any(getattr(config, key) != getattr(primary, key) for key in SHARED_AP_FIELDS):
                check.add(scope, "error", "shared_network_mismatch", "All station profiles must use the same AP network, adapter and broker settings.")
    primary_material = next((material for scope, material in certificates if scope == "primary"), None)
    for scope, (ca, server, client) in certificates:
        if client is not None:
            fingerprint = client.fingerprint(hashes.SHA256())
            if fingerprint in fingerprints:
                check.add(scope, "error", "client_certificate_conflict", "Each station needs a distinct client certificate; restore its own saved credentials.")
            fingerprints.add(fingerprint)
        if scope != "primary" and primary_material is not None:
            for index, certificate in enumerate((ca, server)):
                root = primary_material[index]
                if certificate is not None and root is not None and certificate.fingerprint(hashes.SHA256()) != root.fingerprint(hashes.SHA256()):
                    check.add(scope, "error", "shared_certificate_mismatch", "All profiles on this AP must retain its shared CA and server certificate.")
    return {"schema": 1, "ok": not any(row["severity"] == "error" for row in check.findings),
            "read_only": True, "live_confirmation_required": True,
            "profiles": reports, "findings": check.findings}
