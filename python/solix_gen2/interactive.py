"""Compatibility alias for solix_link.interactive."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("solix_link.interactive")
