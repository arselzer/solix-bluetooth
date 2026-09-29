"""Private configuration and certificate bootstrap for the isolated MQTT lab."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .mqtt_credentials import encrypt_device_credential
from .protocol import Model, timezone_confer


@dataclass(frozen=True)
class LabConfig:
    """One C2000 and a dedicated Linux Wi-Fi adapter; secrets never appear in repr."""

    name: str
    interface: str
    phy: str
    country: str
    device_serial: str = field(repr=False)
    account_id: str = field(repr=False)
    ssid: str = field(default_factory=lambda: f"SOLIX-Local-{secrets.token_hex(3)}")
    passphrase: str = field(default_factory=lambda: secrets.token_urlsafe(24), repr=False)
    timezone_name: str = "Etc/UTC"
    namespace: str = "solix_lab"
    gateway: str = "192.168.77.1"
    broker_host: str = "mqtt.solix.test"

    def __post_init__(self) -> None:
        for value in (self.name, self.interface, self.phy, self.namespace):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
                raise ValueError("Lab names must contain only letters, digits, _ or -")
        if not re.fullmatch(r"[A-Z]{2}", self.country):
            raise ValueError("Country must be a two-letter regulatory code")
        if not re.fullmatch(r"[A-Za-z0-9]{17}", self.device_serial):
            raise ValueError("Lab requires the observed 17-character device serial")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", self.account_id):
            raise ValueError("Lab account ID must be 40 hexadecimal characters")
        if not self.ssid.isascii() or not 1 <= len(self.ssid) <= 32 or any(c in self.ssid for c in "\r\n\x00"):
            raise ValueError("SSID must be 1–32 single-line ASCII characters")
        if not self.passphrase.isascii() or not 8 <= len(self.passphrase) <= 63 or any(ord(c) < 32 or ord(c) > 126 for c in self.passphrase):
            raise ValueError("WPA2 passphrase must contain 8–63 printable ASCII characters")
        address = ipaddress.IPv4Address(self.gateway)
        if not address.is_private or address.is_loopback or address.is_link_local or int(address) & 255 != 1:
            raise ValueError("Lab gateway must be a private IPv4 /24 address ending in .1")
        if not re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", self.broker_host):
            raise ValueError("Invalid local broker hostname")
        timezone_confer(self.timezone_name)

    @property
    def product(self) -> str:
        return "A1783"

    @property
    def model(self) -> Model:
        return Model.C2000_GEN2

    @property
    def api_url(self) -> str:
        return f"http://{self.gateway}/"

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(f"{self.gateway}/24", strict=False)


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Private directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def private_write(path: Path, data: str | bytes) -> None:
    """Atomic owner-only replacement, without following a destination symlink."""
    if path.is_symlink():
        raise ValueError("Private file must not be a symlink")
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_lab(path: Path) -> LabConfig:
    mode = path.stat().st_mode
    if path.is_symlink() or not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError("Lab configuration must be an owner-only regular file")
    try:
        return LabConfig(**json.loads(path.read_text()))
    except (TypeError, KeyError, json.JSONDecodeError):
        raise ValueError("Invalid lab configuration") from None


def initialize_lab(directory: Path, config: LabConfig) -> Path:
    """Generate a local CA, server/client certificates and encrypted API fields.

    No Anker account request is made. Refuse an existing directory so keys and
    credentials cannot be accidentally replaced while a station uses them.
    """
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SOLIX local lab CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    private_write(directory / "ca.pem", ca.public_bytes(pem))
    private_write(directory / "ca-key.pem", ca_key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    for role in ("server", "client"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, config.broker_host if role == "server" else "SOLIX local station")])
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=825))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if role == "server" else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False))
        if role == "server":
            builder = builder.add_extension(x509.SubjectAlternativeName([
                x509.DNSName(config.broker_host), x509.IPAddress(ipaddress.ip_address(config.gateway)),
            ]), critical=False)
        private_write(directory / f"{role}.pem", builder.sign(ca_key, hashes.SHA256()).public_bytes(pem))
        private_write(directory / f"{role}-key.pem", key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    response = {"code": 0, "msg": "success", "trace_id": "", "data": {
        "device_sn": config.device_serial, "thing_name": config.device_serial,
        "certificate_id": secrets.token_hex(19),
        "certificate_pem": encrypt_device_credential(config.device_serial, (directory / "client.pem").read_bytes()),
        "private_key": encrypt_device_credential(config.device_serial, (directory / "client-key.pem").read_bytes()),
        "public_key": "", "endpoint_addr": config.broker_host,
        # Only the client certificate/key are wrapped; the CA field is plain PEM.
        "aws_root_ca1_pem": (directory / "ca.pem").read_text(),
        "origin": "", "country_code": "",
    }}
    private_write(directory / "mqtt-response.json", json.dumps(response))
    private_write(directory / "lab.json", json.dumps(asdict(config), indent=2) + "\n")
    return directory / "lab.json"
