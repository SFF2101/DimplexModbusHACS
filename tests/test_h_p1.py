"""H software P1: runtimes (FC03) and input/output coils (FC01), opt-in.

Source: Glen Dimplex "Modbus RTU Anbindung" (LWPM 410), version 08/2023,
sha256 4e3ce0b4680f3aa463ed6826e46e749e10fe84b8480b1d19eab91a4f9bae5390,
WPM_H column of §6.2 Historie (p. 17), §6.6 Eingänge (p. 44-45) and
§6.7 Ausgänge (p. 45).
"""

from __future__ import annotations

from conftest import load
from test_modbus_mock import H_VALUES, UNIT_DIMPLEX, MockGateway, run

reg = load("registers")
mc = load("modbus_client")

CORE = frozenset(reg.CORE_MODULES)
ALL_MODULES = frozenset(reg.ALL_MODULES)
ALL_CAPS = frozenset(
    {reg.CAP_ELECTRIC_METER, reg.CAP_HEAT_METER, reg.CAP_FLOW_SENSOR, reg.CAP_INVERTER_FREQ}
)

# The ten P1 points: key -> (H address, object type).
P1_H = {
    "runtime_compressor_1": (64, reg.HOLDING),
    "runtime_primary_pump": (66, reg.HOLDING),
    "runtime_heating_pump_m13": (68, reg.HOLDING),
    "runtime_dhw_pump": (69, reg.HOLDING),
    "utility_lockout": (56, reg.COIL),
    "external_lockout": (63, reg.COIL),
    "output_compressor_1": (80, reg.COIL),
    "output_primary_pump": (82, reg.COIL),
    "output_heating_pump_m13": (84, reg.COIL),
    "output_dhw_pump": (85, reg.COIL),
}
# Their unchanged J/L/M addresses.
P1_JLM = {
    "runtime_compressor_1": 72,
    "runtime_primary_pump": 74,
    "runtime_heating_pump_m13": 76,
    "runtime_dhw_pump": 77,
    "utility_lockout": 5,
    "external_lockout": 6,
    "output_compressor_1": 41,
    "output_primary_pump": 43,
    "output_heating_pump_m13": 45,
    "output_dhw_pump": 46,
}
# Baseline H core plan (installed 4c2d9e2) and the P1 additions.
CORE_PLAN = [("holding", 13, 2), ("holding", 27, 5), ("holding", 40, 2), ("holding", 94, 1)]
P1_PLAN = [
    ("holding", 13, 2), ("holding", 27, 5), ("holding", 40, 2),
    ("holding", 64, 1), ("holding", 66, 1), ("holding", 68, 2), ("holding", 94, 1),
    ("coil", 56, 1), ("coil", 63, 1), ("coil", 80, 1), ("coil", 82, 1), ("coil", 84, 2),
]
# Every address the H P1 plan may ever touch (manual-documented, WPM_H column).
DOCUMENTED_H_HOLDING = {13, 14, 27, 28, 29, 30, 31, 40, 41, 94, 64, 66, 68, 69}
DOCUMENTED_H_COILS = {56, 63, 80, 82, 84, 85}

BY_KEY = {s.key: s for s in reg.REGISTERS}


def _specs(version="H", modules=CORE, caps=frozenset(), include_re=False, h_extended=False):
    return reg.active_registers(
        version=version, enabled_modules=modules, capabilities=caps,
        include_re=include_re, h_extended=h_extended,
    )


def _plan(specs, version="H"):
    return reg.build_read_plan(specs, [], version, set(),
                               cluster_gap=reg.read_cluster_gap(version))


def test_p1_h_addresses_and_object_types():
    for key, (addr, obj) in P1_H.items():
        spec = BY_KEY[key]
        assert spec.resolve_address("H") == addr, key
        assert spec.obj == obj, key
        assert spec.h_opt_in is True, key


def test_p1_jlm_addresses_unchanged():
    for key, addr in P1_JLM.items():
        for version in ("J", "L", "M"):
            assert BY_KEY[key].resolve_address(version) == addr, (key, version)


def test_exactly_ten_specs_are_h_opt_in():
    assert {s.key for s in reg.REGISTERS if s.h_opt_in} == set(P1_H)


def test_runtimes_are_unsigned_hours_total_increasing():
    for key in ("runtime_compressor_1", "runtime_primary_pump",
                "runtime_heating_pump_m13", "runtime_dhw_pump"):
        spec = BY_KEY[key]
        assert not spec.signed and spec.scale == 1.0
        assert spec.unit == "h" and spec.state_class == "total_increasing"
        assert reg.decode_value(spec, 65535) == 65535  # uint16, never negative
        assert reg.decode_value(spec, 0) == 0


def test_default_off_keeps_installed_h_plan():
    specs = _specs()
    assert not any(s.h_opt_in for s in specs)
    assert _plan(specs) == CORE_PLAN


def test_default_off_even_with_everything_else_enabled():
    specs = _specs(modules=ALL_MODULES, caps=ALL_CAPS, include_re=True)
    assert not any(s.h_opt_in for s in specs)


def test_opt_in_plan_exact_and_documented_only():
    specs = _specs(h_extended=True)
    assert {s.key for s in specs if s.h_opt_in} == set(P1_H)
    plan = _plan(specs)
    assert plan == P1_PLAN
    touched = {"holding": set(), "coil": set()}
    for obj, start, count in plan:
        touched[obj].update(range(start, start + count))
    assert touched["holding"] <= DOCUMENTED_H_HOLDING
    assert touched["coil"] == DOCUMENTED_H_COILS
    # Undocumented / not-P1 neighbours are never read.
    assert not touched["coil"] & {57, 58, 59, 60, 61, 62, 81, 83, 86, 93, 97}
    assert not touched["holding"] & {65, 67, 70, 71}


def test_opt_in_does_not_enable_other_optional_h_points():
    specs = _specs(modules=ALL_MODULES, caps=ALL_CAPS, include_re=True, h_extended=True)
    h_addrs = {(s.obj, s.resolve_address("H")) for s in specs}
    extras = {a for a in h_addrs if a[1] not in DOCUMENTED_H_HOLDING | DOCUMENTED_H_COILS}
    # Only the already-implemented optional HC2 pair (32/33) may appear.
    assert extras == {(reg.HOLDING, 32), (reg.HOLDING, 33)}


def test_h_extended_flag_has_no_effect_on_jlm():
    for version in ("J", "L", "M"):
        for modules in (CORE, ALL_MODULES):
            off = _specs(version, modules, ALL_CAPS, True, h_extended=False)
            on = _specs(version, modules, ALL_CAPS, True, h_extended=True)
            assert [s.key for s in off] == [s.key for s in on]
            assert _plan(off, version) == _plan(on, version)


def test_no_writes_on_h_regardless_of_option():
    assert not reg.writes_supported("H")
    assert reg.active_write_registers(enabled_modules=ALL_MODULES, version="H") == []


# ----- in-process gateway simulation (unit 2 = Dimplex, unit 1 = decoy) -----

P1_HOLDING = {**H_VALUES, 64: 12345, 66: 23456, 68: 34567, 69: 4567}
P1_COILS = {56: False, 63: False, 80: True, 82: True, 84: False, 85: True}


def test_p1_full_cycle_fc03_fc01_unit2_only():
    async def go():
        async with MockGateway(dimplex=P1_HOLDING, coils=P1_COILS) as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            plan = _plan(_specs(h_extended=True))
            holding, coils, failed = await mc.execute_read_plan(client, plan)
            await client.close()
            return plan, holding, coils, failed, gw.reads
    plan, holding, coils, failed, reads = run(go())
    assert failed == 0
    assert {a: holding[a] for a in (64, 66, 68, 69)} == {64: 12345, 66: 23456, 68: 34567, 69: 4567}
    assert coils == P1_COILS
    assert all(dev == UNIT_DIMPLEX for dev, _fc, _a, _c in reads)
    assert {fc for _d, fc, _a, _c in reads} == {1, 3}  # reads only, no FC05/06/15/16
    # The wire requests are exactly the plan, in order (FC03 holding, FC01 coils).
    expected = [(UNIT_DIMPLEX, 3 if obj == "holding" else 1, start, count)
                for obj, start, count in plan]
    assert reads == expected


def test_p1_unsupported_coils_drop_only_their_chunks():
    """If the LWPM rejects FC01 addresses, core temperatures still update."""
    async def go():
        async with MockGateway(dimplex=P1_HOLDING, coils={500: True}) as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            plan = _plan(_specs(h_extended=True))
            result = await mc.execute_read_plan(client, plan)
            await client.close()
            return result
    holding, coils, failed = run(go())
    assert failed == 5  # the five coil chunks
    assert coils == {}
    assert holding[27] == H_VALUES[27] and holding[14] == H_VALUES[14]
    assert holding[64] == 12345


def test_p1_missing_runtime_register_partial_only():
    async def go():
        values = dict(P1_HOLDING)
        del values[66]
        async with MockGateway(dimplex=values, coils=P1_COILS) as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            plan = _plan(_specs(h_extended=True))
            result = await mc.execute_read_plan(client, plan)
            await client.close()
            return result
    holding, coils, failed = run(go())
    assert failed == 1 and 66 not in holding
    assert holding[64] == 12345 and holding[68] == 34567 and coils[85] is True


def test_default_off_cycle_is_fc03_only():
    async def go():
        async with MockGateway(dimplex=P1_HOLDING, coils=P1_COILS) as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            holding, coils, failed = await mc.execute_read_plan(client, _plan(_specs()))
            await client.close()
            return holding, coils, failed, gw.reads
    holding, coils, failed, reads = run(go())
    assert failed == 0 and coils == {}
    assert {fc for _d, fc, _a, _c in reads} == {3}
    assert not {64, 66, 68, 69} & set(holding)
