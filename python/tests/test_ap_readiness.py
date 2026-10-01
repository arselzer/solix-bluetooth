"""Hostapd may take the adapter down during its startup transition."""

from types import SimpleNamespace

import pytest

from solix_link.isolated_ap import IsolatedAP


def ap(tmp_path):
    return IsolatedAP(SimpleNamespace(interface="wlan_test", namespace="synthetic"), tmp_path)


def test_readiness_requires_hostapd_event_and_current_up_flag(tmp_path, monkeypatch):
    instance = ap(tmp_path)
    calls = []
    monkeypatch.setattr(instance, "check", lambda: None)
    monkeypatch.setattr(instance, "_run", lambda *args: calls.append(args) or '[{"flags": ["UP"]}]')
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        (tmp_path / "hostapd.log").write_text("wlan_test: AP-ENABLED\n")

    monkeypatch.setattr("solix_link.isolated_ap.time.sleep", advance)
    instance._wait_ready()
    assert sleeps == [.05]
    assert calls == [("ip", "netns", "exec", "synthetic", "ip", "-j", "link", "show", "dev", "wlan_test")]


def test_enabled_event_does_not_accept_interface_still_down(tmp_path, monkeypatch):
    instance = ap(tmp_path)
    (tmp_path / "hostapd.log").write_text("wlan_test: AP-ENABLED\n")
    monkeypatch.setattr(instance, "check", lambda: None)
    responses = iter(['[{"flags": []}]', '[{"flags": ["UP"]}]'])
    monkeypatch.setattr(instance, "_run", lambda *_: next(responses))
    sleeps = []
    monkeypatch.setattr("solix_link.isolated_ap.time.sleep", sleeps.append)
    instance._wait_ready()
    assert sleeps == [.05]


def test_readiness_reports_child_failure_without_waiting(tmp_path, monkeypatch):
    instance = ap(tmp_path)

    def failed():
        raise RuntimeError("An AP service exited")

    monkeypatch.setattr(instance, "check", failed)
    with pytest.raises(RuntimeError, match="service exited"):
        instance._wait_ready()


@pytest.mark.parametrize("log", ["", "other: AP-ENABLED\n", "wlan_test: AP-DISABLED\n"])
def test_readiness_timeout_does_not_accept_missing_or_wrong_event(tmp_path, monkeypatch, log):
    instance = ap(tmp_path)
    (tmp_path / "hostapd.log").write_text(log)
    monkeypatch.setattr(instance, "check", lambda: None)
    clock = iter([0, 0, 11])
    monkeypatch.setattr("solix_link.isolated_ap.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("solix_link.isolated_ap.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="hostapd"):
        instance._wait_ready()
