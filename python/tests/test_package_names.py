"""The former public imports must share classes and module state with solix_link."""

import importlib

import solix_gen2
import solix_link


def test_compatibility_exports_and_submodules_share_identity():
    assert solix_gen2.__all__ == solix_link.__all__
    for name in solix_link.__all__:
        assert getattr(solix_gen2, name) is getattr(solix_link, name)
    for name in ("cli", "client", "config", "protocol", "native_mqtt", "mqtt_intercept", "lab_worker"):
        assert importlib.import_module(f"solix_gen2.{name}") is importlib.import_module(f"solix_link.{name}")
