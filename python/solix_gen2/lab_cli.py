"""Compatibility alias for solix_link.lab_cli."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("solix_link.lab_cli")
