"""Genuine Home Assistant runtime tests against a simulated Modbus TCP gateway.

Real HA core (test harness), real config flow, real config-entry setup,
coordinator, entity platforms and service calls. The only fake is the device:
an in-process pymodbus server on 127.0.0.1 (no live IP, gateway or pump).
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from modbus_sim import UNIT_DECOY, UNIT_DIMPLEX, SimGateway
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_wpm.const import DOMAIN

READ_FCS = {3}
DOCUMENTED_H_READS = {13, 14, 27, 28, 29, 30, 31, 32, 33, 40, 41, 94}
H_WRITE_REGS = {129, 134, 149}


def _data(port: int, version: str = "H") -> dict:
    return {
        "host": "127.0.0.1",
        "port": port,
        "unit_id": UNIT_DIMPLEX,
        "profile": "generic_wpm",
        "software_version": version,
        "scan_interval": 300,
        "timeout": 2,
        "enabled_modules": [],
        "has_electric_meter": False,
        "has_heat_meter": False,
        "has_flow_sensor": False,
        "flow_sensor_entity": "",
        "has_inverter_freq": False,
        "include_re_registers": False,
    }


@pytest.fixture
async def gateway():
    gw = await SimGateway().start()
    yield gw
    await gw.stop()


async def _setup(hass: HomeAssistant, gw: SimGateway, *, control: bool, version: str = "H"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data(gw.port, version),
        options={"enable_write_entities": True} if control else {},
        unique_id=f"127.0.0.1:{gw.port}_{UNIT_DIMPLEX}",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _unload(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, entry, platform: str, module: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{module}_{key}"
    )


def _platform_entities(hass: HomeAssistant, entry, platform: str) -> list[str]:
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == platform
    ]


# ---------------------------------------------------------------- config flow


async def test_config_flow_h_unit2_probe_reads_only(hass: HomeAssistant, gateway) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "host": "127.0.0.1", "port": gateway.port, "unit_id": UNIT_DIMPLEX,
            "profile": "generic_wpm", "software_version": "H",
            "scan_interval": 300, "timeout": 2,
        },
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "features"
    # Exactly one FC03 read of register 27 on unit 2; no capability probes.
    assert gateway.requests == [(UNIT_DIMPLEX, 3, 27)]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"enabled_modules": [], "has_electric_meter": False, "has_heat_meter": False,
         "has_flow_sensor": False, "flow_sensor_entity": "", "has_inverter_freq": False,
         "include_re_registers": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.data["software_version"] == "H" and entry.data["unit_id"] == UNIT_DIMPLEX
    assert entry.options.get("enable_write_entities", False) is False
    assert not gateway.writes()
    assert {dev for dev, _fc, _a in gateway.requests} == {UNIT_DIMPLEX}
    await _unload(hass, entry)


async def test_config_flow_cannot_connect_when_h_probe_register_missing(
    hass: HomeAssistant,
) -> None:
    gw = await SimGateway(dimplex={1: 215}).start()  # an L/M-looking map, no reg 27
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "127.0.0.1", "port": gw.port, "unit_id": UNIT_DIMPLEX,
             "profile": "generic_wpm", "software_version": "H",
             "scan_interval": 300, "timeout": 2},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}
        assert not gw.writes()
    finally:
        await gw.stop()


# --------------------------------------------------- setup, gate OFF (default)


async def test_setup_h_default_read_only(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, control=False)
    assert entry.state is config_entries.ConfigEntryState.LOADED

    outdoor = _eid(hass, entry, "sensor", "controller", "outdoor_temperature")
    assert outdoor and float(hass.states.get(outdoor).state) == -10.0
    flow = _eid(hass, entry, "sensor", "hc1", "flow_temperature") or _eid(
        hass, entry, "sensor", "controller", "flow_temperature"
    )
    assert flow and float(hass.states.get(flow).state) == 32.1

    # Gate off: no heat-pump control entities at all.
    assert _platform_entities(hass, entry, "select") == []
    assert _platform_entities(hass, entry, "climate") == []
    assert _platform_entities(hass, entry, "number") == []

    # Only FC03 on unit 2, only documented H read registers, never a write.
    assert not gateway.writes()
    assert {fc for _d, fc, _a in gateway.requests} == READ_FCS
    assert {d for d, _fc, _a in gateway.requests} == {UNIT_DIMPLEX}
    await _unload(hass, entry)


# ------------------------------------------------------------ setup, gate ON


async def test_setup_h_control_entities_no_startup_write(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, control=True)
    mode = _eid(hass, entry, "select", "controller", "set_operating_mode")
    shift = _eid(hass, entry, "number", "hc1", "set_hc1_curve_offset")
    dhw = _eid(hass, entry, "number", "dhw", "set_dhw_setpoint")
    assert mode and shift and dhw
    assert hass.states.get(mode).state == "Auto"
    assert float(hass.states.get(shift).state) == 0
    assert float(hass.states.get(dhw).state) == 48
    attrs = hass.states.get(dhw).attributes
    assert (attrs["min"], attrs["max"]) == (30, 60)
    # Exactly these three controls; no SG Ready, no thermostat, no other number.
    assert sorted(_platform_entities(hass, entry, "select")) == [mode]
    assert sorted(_platform_entities(hass, entry, "number")) == sorted([shift, dhw])
    assert _platform_entities(hass, entry, "climate") == []
    # Setup/startup performed no write; reads touched only documented registers.
    assert not gateway.writes()
    read_addrs = set()
    for _d, _fc, addr in gateway.requests:
        read_addrs.add(addr)
    assert all(d == UNIT_DIMPLEX for d, _fc, _a in gateway.requests)
    plan = hass.data[DOMAIN][entry.entry_id]["coordinator"]._plan
    touched = {a for _o, start, count in plan for a in range(start, start + count)}
    assert touched <= DOCUMENTED_H_READS | H_WRITE_REGS
    assert H_WRITE_REGS <= touched
    await _unload(hass, entry)


async def test_h_number_write_fc06_unit2_with_readback(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, control=True)
    dhw = _eid(hass, entry, "number", "dhw", "set_dhw_setpoint")
    shift = _eid(hass, entry, "number", "hc1", "set_hc1_curve_offset")
    gateway.requests.clear()

    await hass.services.async_call(
        "number", "set_value", {"entity_id": dhw, "value": 52}, blocking=True
    )
    await hass.async_block_till_done()
    assert gateway.writes() == [(UNIT_DIMPLEX, 6, 149)]
    assert gateway.register(149) == 52
    # pre-read, write, read-back of the same register, in that order
    seq = [(fc, a) for _d, fc, a in gateway.requests if a == 149]
    assert seq[:3] == [(3, 149), (6, 149), (3, 149)]
    assert float(hass.states.get(dhw).state) == 52

    gateway.requests.clear()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": shift, "value": -3}, blocking=True
    )
    assert gateway.writes() == [(UNIT_DIMPLEX, 6, 129)]
    assert gateway.register(129) == 16  # offset19: -3 K -> raw 16
    # entity refresh requests are debounced (10 s cooldown): poll explicitly
    await hass.data[DOMAIN][entry.entry_id]["coordinator"].async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(shift).state) == -3
    # the decoy unit was never touched
    assert all(d != UNIT_DECOY for d, _fc, _a in gateway.requests)
    await _unload(hass, entry)


async def test_h_unchanged_value_is_not_rewritten(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, control=True)
    dhw = _eid(hass, entry, "number", "dhw", "set_dhw_setpoint")
    gateway.requests.clear()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": dhw, "value": 48}, blocking=True
    )
    assert not gateway.writes()
    await _unload(hass, entry)


async def test_h_out_of_range_rejected_without_write(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, control=True)
    dhw = _eid(hass, entry, "number", "dhw", "set_dhw_setpoint")
    gateway.requests.clear()
    for bad in (61, 85, 29):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "number", "set_value", {"entity_id": dhw, "value": bad}, blocking=True
            )
    assert not gateway.writes()
    assert gateway.register(149) == 48
    await _unload(hass, entry)


async def test_h_operating_mode_only_summer_auto_writable(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway, control=True)
    mode = _eid(hass, entry, "select", "controller", "set_operating_mode")
    gateway.requests.clear()
    for label in ("Party", "Holiday", "2nd heat generator", "Cooling"):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "select", "select_option", {"entity_id": mode, "option": label}, blocking=True
            )
    assert not gateway.writes()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": mode, "option": "Summer"}, blocking=True
    )
    await hass.async_block_till_done()
    assert gateway.writes() == [(UNIT_DIMPLEX, 6, 134)]
    assert gateway.register(134) == 0
    assert hass.states.get(mode).state == "Summer"
    await _unload(hass, entry)


async def test_h_readback_mismatch_raises(hass: HomeAssistant) -> None:
    gw = await SimGateway(ignore_writes=True).start()
    try:
        entry = await _setup(hass, gw, control=True)
        dhw = _eid(hass, entry, "number", "dhw", "set_dhw_setpoint")
        with pytest.raises(HomeAssistantError, match="Read-back mismatch"):
            await hass.services.async_call(
                "number", "set_value", {"entity_id": dhw, "value": 50}, blocking=True
            )
        # exactly one write attempt, no retry
        assert gw.writes() == [(UNIT_DIMPLEX, 6, 149)]
        await _unload(hass, entry)
    finally:
        await gw.stop()


async def test_h_write_when_gateway_down_fails_cleanly(hass: HomeAssistant) -> None:
    gw = await SimGateway().start()
    entry = await _setup(hass, gw, control=True)
    dhw = _eid(hass, entry, "number", "dhw", "set_dhw_setpoint")
    await gw.stop()
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    await client.close()  # drop the established socket; next call must reconnect
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": dhw, "value": 50}, blocking=True
        )
    assert not gw.writes()
    await _unload(hass, entry)


async def test_h_coordinator_refuses_unlisted_register(hass: HomeAssistant, gateway) -> None:
    from pymodbus.exceptions import ModbusException

    entry = await _setup(hass, gateway, control=True)
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    gateway.requests.clear()
    for addr, raw in ((21, 200), (130, 40), (5047, 50), (149, 85), (129, 39), (134, 5)):
        with pytest.raises(ModbusException):
            await coordinator.write_register(addr, raw)
    assert gateway.requests == []  # rejected before any bus traffic
    await _unload(hass, entry)


# ------------------------------------------------------- J/L/M unchanged path


async def test_m_control_path_unchanged(hass: HomeAssistant) -> None:
    gw = await SimGateway(dimplex={1: 215, 2: 280, 5: 300, 5015: 1, 5047: 48}).start()
    try:
        entry = await _setup(hass, gw, control=True, version="M")
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        assert coordinator.write_register == coordinator._client.write_register
        keys = {ws.key: ws.address for ws in coordinator.write_specs}
        assert keys["set_operating_mode"] == 5015 and keys["set_dhw_setpoint"] == 5047
        assert any(e.endswith("sg_ready_mode") for e in
                   [x.unique_id for x in er.async_entries_for_config_entry(
                       er.async_get(hass), entry.entry_id)])
        assert not gw.writes()
        await _unload(hass, entry)
    finally:
        await gw.stop()
