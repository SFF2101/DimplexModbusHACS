"""WPM 2006/2007 H-software mapping (LWPM 410 manual 08/2023 + Dimplex wiki)."""

from __future__ import annotations

from conftest import load

reg = load("registers")
const = load("const")

ALL_MODULES = frozenset(reg.ALL_MODULES)
ALL_CAPS = frozenset(
    {reg.CAP_ELECTRIC_METER, reg.CAP_HEAT_METER, reg.CAP_FLOW_SENSOR, reg.CAP_INVERTER_FREQ}
)

# Documented H holding registers (manual §6.1 Betriebsdaten; wiki Systemstatus).
EXPECTED_H = {
    "outdoor_temperature": 27,
    "return_setpoint_temperature": 28,
    "return_temperature": 29,
    "dhw_temperature": 30,
    "flow_temperature": 31,
    "hc2_setpoint_temperature": 32,
    "hc2_temperature": 33,
    "dhw_setpoint_temperature": 40,
    "source_outlet_temperature": 41,
    "status_code": 14,
    "status_text": 14,
    "lock_code": 94,
    "lock_text": 94,
    "fault_code": 13,
    "fault_text": 13,
}


def _h_specs(modules=ALL_MODULES, caps=ALL_CAPS, include_re=True):
    return reg.active_registers(
        version="H", enabled_modules=modules, capabilities=caps, include_re=include_re
    )


def test_h_only_documented_registers_active():
    # Even with every module + capability + RE switched on, H exposes only
    # the documented registers — nothing else is created or polled.
    specs = _h_specs()
    got = {s.key: s.resolve_address("H") for s in specs}
    assert got == EXPECTED_H


def test_h_core_install_entities():
    core = frozenset(reg.CORE_MODULES)
    keys = {s.key for s in _h_specs(modules=core, caps=frozenset(), include_re=False)}
    assert keys == set(EXPECTED_H) - {"hc2_temperature", "hc2_setpoint_temperature"}


def test_h_temperatures_signed_tenths():
    by_key = {s.key: s for s in reg.REGISTERS}
    for key in ("outdoor_temperature", "return_temperature", "flow_temperature",
                "return_setpoint_temperature"):
        spec = by_key[key]
        assert spec.obj == reg.HOLDING and spec.signed and spec.scale == 0.1
    # commissioned historical baseline: FC03 reg 27 raw 118 -> 11.8 °C
    assert reg.decode_value(by_key["outdoor_temperature"], 118) == 11.8
    # negative outdoor temperature, two's complement
    assert reg.decode_value(by_key["outdoor_temperature"], 0xFF9C) == -10.0
    assert reg.decode_value(by_key["outdoor_temperature"], 0x8000) == -3276.8


def test_int_addresses_never_resolve_on_h():
    for spec in reg.REGISTERS:
        if isinstance(spec.address, int):
            assert spec.resolve_address("H") is None, spec.key
            assert spec.resolve_address("M") == spec.address, spec.key


def test_h_read_plan_contiguous_only():
    specs = _h_specs()
    plan = reg.build_read_plan(
        specs, [], "H", set(), cluster_gap=reg.read_cluster_gap("H")
    )
    assert plan == [
        ("holding", 13, 2),   # fault 13, status 14
        ("holding", 27, 7),   # 27..33, all documented
        ("holding", 40, 2),   # dhw setpoint 40, source outlet 41
        ("holding", 94, 1),   # lock
    ]
    documented = set(EXPECTED_H.values())
    for _obj, start, count in plan:
        assert set(range(start, start + count)) <= documented


def test_h_core_plan_excludes_hc2():
    core = frozenset(reg.CORE_MODULES)
    plan = reg.build_read_plan(
        _h_specs(modules=core, caps=frozenset(), include_re=False), [], "H", set(),
        cluster_gap=reg.read_cluster_gap("H"),
    )
    assert plan == [
        ("holding", 13, 2), ("holding", 27, 5), ("holding", 40, 2), ("holding", 94, 1),
    ]


def test_h_no_energy_no_writes_no_probes():
    assert reg.active_energy_groups(
        enabled_modules=ALL_MODULES, capabilities=ALL_CAPS, version="H"
    ) == []
    assert reg.active_write_registers(enabled_modules=ALL_MODULES, version="H") == []
    assert reg.active_write_registers(
        enabled_modules=ALL_MODULES, include_all_modules=True, version="H"
    ) == []
    assert reg.writes_supported("H") is False
    assert all(reg.writes_supported(v) for v in ("J", "L", "M", None))
    assert reg.capability_probes("H") == {}
    assert reg.capability_probes("M") == {
        reg.CAP_ELECTRIC_METER: 5170, reg.CAP_HEAT_METER: 5168, reg.CAP_INVERTER_FREQ: 114,
    }


def test_connection_probe_address():
    assert reg.connection_probe_address("H") == 27
    for ver in ("J", "L", "M"):
        assert reg.connection_probe_address(ver) == 1


def test_h_enum_maps():
    status = const.get_enum_map(reg.ENUM_STATUS, "H")
    assert status[2] == "Heat pump on – heating"
    assert status[21] == "Heat pump on – defrost"
    assert status[29] == "Passive cooling"
    lock = const.get_enum_map(reg.ENUM_LOCK, "H")
    assert lock[1] == "Outdoor temperature"
    assert lock[7] == "Utility (EVU) lockout"
    fault = const.get_enum_map(reg.ENUM_FAULT, "H")
    assert 1 not in fault and 2 not in fault  # N17.x are L/M-only
    assert fault[0] == "No fault" and fault[17] == "High pressure"
    # J keeps upstream's shared map unchanged (regression-safe).
    assert const.get_enum_map(reg.ENUM_FAULT, "J")[1] == "Fault N17.1"
    assert const.get_enum_map(reg.ENUM_STATUS, "J")[29] == "Reserved"
