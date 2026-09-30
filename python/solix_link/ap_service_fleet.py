"""Route a shared TLS MQTT listener to independent station protocol owners."""
from __future__ import annotations

import asyncio
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .mqtt_intercept import LocalMqttServer


class MqttDeviceRouter:
    """Pin each local client certificate to its own station and command queue.

    Shared Wi-Fi does not imply shared telemetry or acknowledgements. A peer
    cannot select another station merely by changing its MQTT client ID/topic.
    """

    def __init__(self, stations: dict[str, LocalMqttServer], directory: Path) -> None:
        self.stations, self.directory = stations, directory
        self._by_certificate = {}
        for station in stations.values():
            cert = x509.load_pem_x509_certificate((station.directory / "client.pem").read_bytes())
            fingerprint = cert.fingerprint(hashes.SHA256())
            if fingerprint in self._by_certificate:
                raise ValueError("Stations must have distinct client certificates")
            self._by_certificate[fingerprint] = station
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self, host: str, port: int = 8883) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.directory / "server.pem", self.directory / "server-key.pem")
        for station in self.stations.values():
            context.load_verify_locations(station.directory / "ca.pem")
        context.verify_mode = ssl.CERT_REQUIRED
        self._server = await asyncio.start_server(self._accept, host, port, ssl=context,
                                                 ssl_handshake_timeout=10, ssl_shutdown_timeout=3, limit=262144)
        for station in self.stations.values():
            station.changed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        tls = writer.get_extra_info("ssl_object")
        certificate = tls.getpeercert(binary_form=True) if tls else None
        station = None
        if certificate:
            fingerprint = x509.load_der_x509_certificate(certificate).fingerprint(hashes.SHA256())
            station = self._by_certificate.get(fingerprint)
        if station is None or len(self._tasks) >= 2 * len(self.stations):
            writer.close()
            return
        self._tasks.add(task)
        try:
            await station._accept(reader, writer)
        finally:
            self._tasks.discard(task)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()
