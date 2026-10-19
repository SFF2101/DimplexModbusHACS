"""HA runtime: H write safety against a fault-injecting raw Modbus device.

Uses ``tests/fake_modbus.py`` (pure asyncio, 127.0.0.1) so faults such as a
lost FC06 response are reproduced identically on every pymodbus version.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_wpm.const import DOMAIN

_spec = importlib.util.spec_from_file_location(
    "fake_modbus",
    os.path.join(os.path.dirname(__file__), "..", "tests", "fake_modbus.py"),
)
fake_modbus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_modbus)

UNIT = 2
H_REGS = {
    13: 0, 14: 2, 27: 118, 28: 300, 29: 285, 30: 480, 31: 321,
    40: 480, 41: 12, 94: 0, 129: 19, 134: 1, 149: 48,
}


async def _setup(hass: HomeAssistant, port: int) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "127.0.0.1", "port": port, "unit_id": UNIT, "profile": "generic_wpm",
            "software_version": "H", "scan_interval": 300, "timeout": 1,
            "enabled_modules": [], "has_electric_meter": False, "has_heat_meter": False,
            "has_flow_sensor": False, "flow_sensor_entity": "", "has_inverter_freq": False,
            "include_re_registers": False,
        },
        options={"enable_write_entities": True},
        unique_id=f"127.0.0.1:{port}_{UNIT}",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _dhw(hass: HomeAssistant, entry) -> str:
    return er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{entry.entry_id}_dhw_set_dhw_setpoint"
    )


@pytest.fixture
async def device():
    dev = await fake_modbus.FakeModbusDevice(H_REGS, UNIT).start()
    yield dev
    await dev.stop()


async def test_setup_sends_no_write(hass: HomeAssistant, device) -> None:
    entry = await _setup(hass, device.port)
    assert device.fc(6) == []
    assert {u for u, _f, _a in device.frames} == {UNIT}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_lost_write_response_one_frame_then_recovery(hass: HomeAssistant, device) -> None:
    entry = await _setup(hass, device.port)
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    dhw = _dhw(hass, entry)
    device.drop_fc06_response = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": dhw, "value": 50}, blocking=True
        )
    assert device.fc(6) == [(UNIT, 6, 149)]  # one control action -> one FC06 frame
    # timeout recovery: the next poll reconnects and shows the device's true value
    device.drop_fc06_response = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert float(hass.states.get(dhw).state) == 50
    assert device.fc(6) == [(UNIT, 6, 149)]  # still exactly one
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_waits_for_inflight_write(hass: HomeAssistant, device) -> None:
    entry = await _setup(hass, device.port)
    dhw = _dhw(hass, entry)
    device.fc06_delay = 0.4
    call = asyncio.create_task(
        hass.services.async_call(
            "number", "set_value", {"entity_id": dhw, "value": 52}, blocking=True
        )
    )
    for _ in range(100):  # wait until the FC06 is on the wire
        if device.fc(6):
            break
        await asyncio.sleep(0.01)
    assert device.fc(6) == [(UNIT, 6, 149)]
    connections = device.connections
    assert await hass.config_entries.async_unload(entry.entry_id)  # closes the client
    await call  # the in-flight write completed (incl. read-back) instead of being cut off
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert device.connections == connections  # no reconnect after unload
    assert device.registers[149] == 52
    assert device.fc(6) == [(UNIT, 6, 149)]
    reads_149 = [f for f in device.frames if f[1] == 3 and f[2] == 149]
    assert len(reads_149) >= 2  # pre-read and read-back both happened
