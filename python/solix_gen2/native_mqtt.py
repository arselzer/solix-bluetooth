"""Compatibility alias for solix_link.native_mqtt."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("solix_link.native_mqtt")
