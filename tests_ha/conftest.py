"""Offline Home Assistant runtime tests (pytest-homeassistant-custom-component).

These start a real (test) Home Assistant core, load the integration from
``custom_components/dimplex_wpm`` and talk Modbus TCP to an in-process
simulated gateway on 127.0.0.1 only. They need Home Assistant installed, so
they live outside ``tests/`` (which CI runs without Home Assistant):

    pip install pytest-homeassistant-custom-component "pymodbus>=3.10"
    pytest tests_ha -o asyncio_mode=auto
"""

from __future__ import annotations

import logging
import os
import sys

import pytest
import pytest_socket

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading ``custom_components/dimplex_wpm``."""
    return


@pytest.fixture(autouse=True)
def loopback_only(socket_enabled):
    """Sockets are needed for the simulated gateway, but only to 127.0.0.1.

    The harness blocks all sockets; re-enable them and immediately restrict
    every connect() to the loopback address so no live IP can be reached.
    """
    pytest_socket.socket_allow_hosts(["127.0.0.1"])
    yield
