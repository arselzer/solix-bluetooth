"""Compatibility alias for solix_link.ap_service."""

from importlib import import_module
import sys

if __name__ == "__main__":
    from solix_link.ap_service import main
    main()
else:
    sys.modules[__name__] = import_module("solix_link.ap_service")
