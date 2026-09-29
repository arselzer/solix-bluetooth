"""Compatibility alias for solix_link.cli."""

from importlib import import_module
import sys

if __name__ == "__main__":
    from solix_link.cli import main
    raise SystemExit(main())
else:
    sys.modules[__name__] = import_module("solix_link.cli")
