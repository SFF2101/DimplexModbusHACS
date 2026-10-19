"""Genuine HA runtime tests for H P1 (runtimes FC03 + input/output coils FC01).

Real HA core test instance, real config entry setup, options flow, coordinator
and entity platforms against the in-process simulated gateway on 127.0.0.1.
Addresses: LWPM 410 manual 08/2023 §6.2 (p. 17), §6.6 (p. 44-45), §6.7 (p. 45),
WPM_H column.
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from modbus_sim import H_VALUES, UNIT_DIMPLEX, SimGateway
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_wpm.const import DOMAIN

P1_HOLDING = {**H_VALUES, 64: 12345, 66: 23456, 68: 34567, 69: 4567}
P1_COILS = {56: False, 63: True, 80: True, 82: True, 84: False, 85: True}

# key -> (platform, module, expected state)
P1_ENTITIES = {
    "runtime_compressor_1": ("sensor", "controller", "12345"),
    "runtime_primary_pump": ("sensor", "controller", "23456"),
    "runtime_heating_pump_m13": ("sensor", "hc1", "34567"),
    "runtime_dhw_pump": ("sensor", "dhw", "4567"),
    "utility_lockout": ("binary_sensor", "controller", "off"),
    "external_lockout": ("binary_sensor", "controller", "on"),
    "output_compressor_1": ("binary_sensor", "controller", "on"),
    "output_primary_pump": ("binary_sensor", "controller", "on"),
    "output_heating_pump_m13": ("binary_sensor", "hc1", "off"),
    "output_dhw_pump": ("binary_sensor", "dhw", "on"),
}
CORE_PLAN = [["holding", 13, 2], ["holding", 27, 5], ["holding", 40, 2], ["holding", 94, 1]]
P1_PLAN = [
    ["holding", 13, 2], ["holding", 27, 5], ["holding", 40, 2],
    ["holding", 64, 1], ["holding", 66, 1], ["holding", 68, 2], ["holding", 94, 1],
    ["coil", 56, 1], ["coil", 63, 1], ["coil", 80, 1], ["coil", 82, 1], ["coil", 84, 2],
]
WRITE_FCS = {5, 6, 15, 16}


def _data(port: int, version: str = "H") -> dict:
    return {
        "host": "127.0.0.1", "port": port, "unit_id": UNIT_DIMPLEX,
        "profile": "generic_wpm", "software_version": version,
        "scan_interval": 300, "timeout": 2, "enabled_modules": [],
        "has_electric_meter": False, "has_heat_meter": False,
        "has_flow_sensor": False, "flow_sensor_entity": "",
        "has_inverter_freq": False, "include_re_registers": False,
    }


@pytest.fixture
async def gateway():
    gw = await SimGateway(dimplex=P1_HOLDING, coils=P1_COILS).start()
    yield gw
    await gw.stop()


async def _setup(hass, gw, options=None, version="H"):
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(gw.port, version), options=options or {},
        unique_id=f"127.0.0.1:{gw.port}_{UNIT_DIMPLEX}",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _unload(hass, entry):
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass, entry, platform, module, key):
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{module}_{key}"
    )


def _platform_entities(hass, entry, platform):
    return [e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if e.domain == platform]


def _meta(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]["coordinator"].data["meta"]


async def test_h_default_off_no_p1_entities_and_installed_plan(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway)
    for key, (platform, module, _state) in P1_ENTITIES.items():
        assert _eid(hass, entry, platform, module, key) is None, key
    assert _meta(hass, entry)["read_plan"] == CORE_PLAN
    assert {fc for _d, fc, _a in gateway.requests} == {3}
    assert {d for d, _fc, _a in gateway.requests} == {UNIT_DIMPLEX}
    assert not gateway.writes()
    # Existing temperature diagnostics unchanged.
    outdoor = _eid(hass, entry, "sensor", "controller", "outdoor_temperature")
    assert float(hass.states.get(outdoor).state) == -10.0
    await _unload(hass, entry)


async def test_h_opt_in_creates_ten_read_only_entities(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, options={"h_extended_monitoring": True})
    for key, (platform, module, state) in P1_ENTITIES.items():
        eid = _eid(hass, entry, platform, module, key)
        assert eid, key
        assert hass.states.get(eid).state == state, key
    rt = hass.states.get(_eid(hass, entry, "sensor", "controller", "runtime_compressor_1"))
    assert rt.attributes["unit_of_measurement"] == "h"
    assert rt.attributes["state_class"] == "total_increasing"
    meta = _meta(hass, entry)
    assert meta["read_plan"] == P1_PLAN and meta["failed_read_chunks"] == 0
    fcs = {fc for _d, fc, _a in gateway.requests}
    assert fcs == {1, 3}
    assert {d for d, _fc, _a in gateway.requests} == {UNIT_DIMPLEX}
    coil_addrs = {a for _d, fc, a in gateway.requests if fc == 1}
    assert coil_addrs == {56, 63, 80, 82, 84}  # chunk starts; 85 read inside 84+2
    # Still no control entities and never a write.
    for platform in ("number", "select", "climate", "switch", "button"):
        assert _platform_entities(hass, entry, platform) == [], platform
    assert not gateway.writes()
    assert not WRITE_FCS & fcs
    # Core temperatures still decode as before.
    flow = _eid(hass, entry, "sensor", "hc1", "flow_temperature")
    assert float(hass.states.get(flow).state) == 32.1
    await _unload(hass, entry)


async def test_h_unsupported_coils_do_not_break_core(hass: HomeAssistant) -> None:
    gw = await SimGateway(dimplex=P1_HOLDING, coils={500: True}).start()
    try:
        entry = await _setup(hass, gw, options={"h_extended_monitoring": True})
        assert entry.state is config_entries.ConfigEntryState.LOADED
        assert _meta(hass, entry)["failed_read_chunks"] == 5
        outdoor = _eid(hass, entry, "sensor", "controller", "outdoor_temperature")
        assert float(hass.states.get(outdoor).state) == -10.0
        runtime = _eid(hass, entry, "sensor", "controller", "runtime_compressor_1")
        assert hass.states.get(runtime).state == "12345"
        coil = _eid(hass, entry, "binary_sensor", "controller", "output_compressor_1")
        # Value absent from this poll -> the coil entity alone is unavailable.
        assert hass.states.get(coil).state == "unavailable"
        assert not gw.writes()
        await _unload(hass, entry)
    finally:
        await gw.stop()


async def test_options_flow_offers_h_toggle_only_on_h(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = {str(k) for k in result["data_schema"].schema}
    assert "h_extended_monitoring" in fields
    default = next(k for k in result["data_schema"].schema if str(k) == "h_extended_monitoring")
    assert default.default() is False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"h_extended_monitoring": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # update listener reloads the entry
    assert entry.options["h_extended_monitoring"] is True
    assert entry.options.get("enable_write_entities", False) is False
    eid = _eid(hass, entry, "binary_sensor", "dhw", "output_dhw_pump")
    assert eid and hass.states.get(eid).state == "on"
    assert not gateway.writes()
    await _unload(hass, entry)


async def test_options_flow_no_h_toggle_for_m(hass: HomeAssistant) -> None:
    gw = await SimGateway(dimplex={1: 215}).start()
    try:
        entry = MockConfigEntry(
            domain=DOMAIN, data=_data(gw.port, "M"),
            unique_id=f"127.0.0.1:{gw.port}_{UNIT_DIMPLEX}",
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_init(entry.entry_id)
        fields = {str(k) for k in result["data_schema"].schema}
        assert "h_extended_monitoring" not in fields
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        await gw.stop()


async def test_h_diagnostics_reports_option(hass: HomeAssistant, gateway) -> None:
    from custom_components.dimplex_wpm.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass, gateway, options={"h_extended_monitoring": True})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["config"]["h_extended_monitoring"] is True
    assert diag["config"]["enable_control"] is False
    assert diag["config"]["write_specs"] == []
    assert "127.0.0.1" not in str(diag)  # host redacted
    await _unload(hass, entry)
