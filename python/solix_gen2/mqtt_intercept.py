"""Compatibility alias for solix_link.mqtt_intercept."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("solix_link.mqtt_intercept")
