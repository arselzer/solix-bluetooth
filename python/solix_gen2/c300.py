"""Compatibility alias for solix_link.c300."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("solix_link.c300")
