import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .base import SourceAdapter  # noqa: E402
from .rst import RSTAdapter  # noqa: E402

ADAPTERS = [RSTAdapter]


def get_adapter(name):
    for adapter in ADAPTERS:
        if adapter.name == name:
            return adapter()
    raise ValueError(f"unknown adapter: {name!r}")


def pick_adapter(path):
    """Return an adapter instance that supports ``path``, or None."""
    for adapter in ADAPTERS:
        inst = adapter()
        if inst.supports(path):
            return inst
    return None