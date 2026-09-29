"""Own a dedicated Linux Wi-Fi adapter in a namespace without an internet route."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess

from .lab_config import LabConfig, private_write


class IsolatedAP:
    """Context manager for hostapd + dnsmasq. Requires root and a DOWN adapter.

    Only processes started by this instance are terminated. Existing namespaces
    and active adapters are refused; host routing/firewall rules are untouched.
    """

    def __init__(self, config: LabConfig, directory: Path, *, hostapd: str = "hostapd", dnsmasq: str = "dnsmasq") -> None:
        self.config = config
        self.directory = directory.resolve()
        self.hostapd = hostapd
        self.dnsmasq = dnsmasq
        self.processes: list[subprocess.Popen] = []
        self.created = False
        self.moved = False
        self._ip = "ip"
        self._iw = "iw"

    def _run(self, *args: str) -> str:
        return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15).stdout

    def exec_args(self, *args: str) -> list[str]:
        return [self._ip, "netns", "exec", self.config.namespace, *args]

    def spawn(self, args: list[str], log_name: str) -> subprocess.Popen:
        path = self.directory / log_name
        private_write(path, b"")
        with path.open("ab") as log:
            process = subprocess.Popen(self.exec_args(*args), stdout=log, stderr=log, start_new_session=True)
        self.processes.append(process)
        return process

    def start(self) -> None:
        if self.created:
            raise RuntimeError("AP has already been started")
        if os.geteuid() != 0:
            raise PermissionError("Isolated AP requires root on Linux")
        resolved = [shutil.which(tool) for tool in ("ip", "iw", self.hostapd, self.dnsmasq)]
        if not all(resolved):
            raise RuntimeError("Install iproute2, iw, hostapd and dnsmasq")
        self._ip, self._iw, self.hostapd, self.dnsmasq = resolved
        config = self.config
        namespaces = self._run(self._ip, "netns", "list")
        if any(line.split()[0] == config.namespace for line in namespaces.splitlines() if line.strip()):
            raise RuntimeError("Refusing an existing network namespace")
        links = json.loads(self._run(self._ip, "-j", "link", "show", "dev", config.interface))
        if not links or "UP" in links[0]["flags"]:
            raise RuntimeError("Use a dedicated Wi-Fi adapter that is administratively DOWN")
        phy_path = Path("/sys/class/net") / config.interface / "phy80211"
        if phy_path.resolve().name != config.phy:
            raise RuntimeError("Interface does not belong to the configured Wi-Fi phy")
        for other in Path("/sys/class/net").iterdir():
            other_phy = other / "phy80211"
            if other.name != config.interface and other_phy.exists() and other_phy.resolve().name == config.phy:
                raise RuntimeError("Dedicated Wi-Fi phy must not contain another interface")
        addresses = json.loads(self._run(self._ip, "-j", "address", "show", "dev", config.interface))
        if any(entry.get("addr_info") for entry in addresses):
            raise RuntimeError("Dedicated Wi-Fi interface must have no existing addresses")
        hostapd_config = (f"interface={config.interface}\ndriver=nl80211\nssid={config.ssid}\n"
                          f"country_code={config.country}\nhw_mode=g\nchannel=6\n"
                          "wpa=2\nwpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\n"
                          f"wpa_passphrase={config.passphrase}\n")
        private_write(self.directory / "hostapd.conf", hostapd_config)
        try:
            self._run(self._ip, "netns", "add", config.namespace)
            self.created = True
            self._run(self._iw, "phy", config.phy, "set", "netns", "name", config.namespace)
            self.moved = True
            self._run(*self.exec_args(self._ip, "link", "set", "lo", "up"))
            self._run(*self.exec_args(self._ip, "addr", "add", f"{config.gateway}/24", "dev", config.interface))
            self._run(*self.exec_args(self._ip, "link", "set", config.interface, "up"))
            self.spawn([self.hostapd, str(self.directory / "hostapd.conf")], "hostapd.log")
            prefix = str(config.network.network_address).rsplit(".", 1)[0]
            self.spawn([self.dnsmasq, "--no-daemon", "--conf-file=/dev/null",
                        f"--interface={config.interface}", "--bind-interfaces", f"--listen-address={config.gateway}",
                        f"--dhcp-range={prefix}.10,{prefix}.99,255.255.255.0,1h",
                        f"--dhcp-option=option:router,{config.gateway}", f"--dhcp-option=option:dns-server,{config.gateway}",
                        "--dhcp-authoritative", "--no-resolv", "--no-hosts",
                        f"--address=/time.nist.gov/{config.gateway}", f"--address=/{config.broker_host}/{config.gateway}",
                        "--leasefile-ro", "--log-dhcp"], "dnsmasq.log")
            if self._run(*self.exec_args(self._ip, "route", "show", "default")).strip():
                raise RuntimeError("Lab namespace unexpectedly has a default route")
        except BaseException:
            self.stop()
            raise

    def check(self) -> None:
        if any(process.poll() is not None for process in self.processes):
            raise RuntimeError("A lab service exited; inspect private logs")
        if self._run(*self.exec_args(self._ip, "route", "show", "default")).strip():
            raise RuntimeError("Lab namespace acquired a default route")

    def stop(self) -> None:
        failures = []
        for process in reversed(self.processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        self.processes.clear()
        if self.moved:
            try:
                self._run(*self.exec_args(self._ip, "link", "set", self.config.interface, "down"))
                self._run(*self.exec_args(self._ip, "addr", "flush", "dev", self.config.interface))
                self._run(*self.exec_args(self._iw, "phy", self.config.phy, "set", "netns", str(os.getpid())))
                self.moved = False
                # Returning a phy can reactivate its interface on the host.
                self._run(self._ip, "link", "set", self.config.interface, "down")
                self._run(self._ip, "addr", "flush", "dev", self.config.interface)
            except Exception:
                failures.append("Could not return Wi-Fi adapter; namespace retained for recovery" if self.moved
                                else "Adapter returned, but final interface cleanup failed")
        if self.created and not self.moved:
            try:
                self._run(self._ip, "netns", "del", self.config.namespace)
                self.created = False
            except Exception:
                failures.append("Could not remove the lab namespace")
        if failures:
            raise RuntimeError("; ".join(failures))

    def __enter__(self) -> IsolatedAP:
        self.start()
        return self

    def __exit__(self, *_args) -> None:
        self.stop()
