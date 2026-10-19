"""Test helpers: import integration modules without Home Assistant.

The integration package ``__init__`` imports Home Assistant, which CI does not
install. The pure modules (registers, const, profiles, modbus_client) only use
relative imports among themselves, so they are loaded under a stub package
whose ``__init__`` is never executed.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys

PKG = "dwpm_pkg"
PKG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "dimplex_wpm")
)


def load(module: str):
    """Import ``custom_components/dimplex_wpm/<module>.py`` HA-free."""
    if PKG not in sys.modules:
        spec = importlib.util.spec_from_loader(PKG, loader=None, is_package=True)
        pkg = importlib.util.module_from_spec(spec)
        pkg.__path__ = [PKG_DIR]
        sys.modules[PKG] = pkg
    return importlib.import_module(f"{PKG}.{module}")
