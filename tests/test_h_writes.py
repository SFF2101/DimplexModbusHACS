"""H-software write table (LWPM 410 manual 08/2023 §6.3, WPM_H column)."""

from __future__ import annotations

from conftest import load

reg = load("registers")

ALL_MODULES = frozenset(reg.ALL_MODULES)

# key -> (H address, manual page/section, row)
EXPECTED_H_WRITES = {
    "set_operating_mode": 134,    # p.24 §6.3.3 Betriebsmodus 142|222|134
    "set_hc1_curve_offset": 129,  # p.19 §6.3.1 Parallelverschiebung 163|243|129
    "set_dhw_setpoint": 149,      # p.25 §6.3.4 Solltemperatur 174|254|149
}
# Documented on H but deliberately NOT implemented (not approved / scaling unknown).
EXCLUDED_H = {21, 22, 130, 131, 135, 136, 142, 151}


def _by_key(specs):
    return {w.key: w for w in specs}


def test_h_write_table_exact():
    got = {w.key: w.address for w in reg.h_write_registers()}
    assert got == EXPECTED_H_WRITES
    assert not set(got.values()) & EXCLUDED_H


def test_h_write_addresses_never_nwpm():
    nwpm = {w.address for w in reg.WRITE_REGISTERS}
    assert not nwpm & set(EXPECTED_H_WRITES.values())
    # NWPM table (incl. SG Ready) still never applies to H.
    assert reg.active_write_registers(enabled_modules=ALL_MODULES, version="H") == []


def test_h_writes_have_no_module_gates():
    for w in reg.h_write_registers():
        assert w.module_flag is None and w.module in reg.CORE_MODULES


def test_h_curve_offset_encoding():
    w = _by_key(reg.h_write_registers())["set_hc1_curve_offset"]
    assert (w.min_value, w.max_value, w.step, w.unit) == (-19, 19, 1, "K")
    # manual p.19-20 code list: 0 = -19 K, 19 = 0 K, 38 = +19 K
    assert [w.to_raw(v) for v in (-19, -3, 0, 1, 19)] == [0, 16, 19, 20, 38]
    assert [w.from_raw(r) for r in (0, 16, 19, 38)] == [-19, -3, 0, 19]
    assert w.to_raw(-40) == 0 and w.to_raw(40) == 38  # clamped
    assert w.raw_allowed(0) and w.raw_allowed(38)
    assert not w.raw_allowed(39) and not w.raw_allowed(-1)


def test_h_dhw_setpoint_safe_range():
    w = _by_key(reg.h_write_registers())["set_dhw_setpoint"]
    assert (w.min_value, w.max_value, w.step) == (30, 60, 1)
    assert w.max_value <= 85  # manual maximum
    assert w.to_raw(48.4) == 48 and w.to_raw(85) == 60 and w.to_raw(10) == 30
    assert w.from_raw(52) == 52
    assert w.raw_allowed(30) and w.raw_allowed(60)
    assert not w.raw_allowed(29) and not w.raw_allowed(61) and not w.raw_allowed(85)


def test_h_operating_mode_codes():
    w = _by_key(reg.h_write_registers())["set_operating_mode"]
    assert w.kind == reg.KIND_SELECT
    # manual p.24: 0 Sommer, 1 Auto, 2 Urlaub, 3 Party, 4 2.Wärmeerzeuger, 5 Kühlen
    assert w.options_map == {0: "Summer", 1: "Auto", 2: "Holiday", 3: "Party",
                             4: "2nd heat generator", 5: "Cooling"}
    assert [c for c in range(-1, 8) if w.raw_allowed(c)] == [0, 1]


def test_nwpm_select_without_writable_codes_allows_all_options():
    w = _by_key(reg.WRITE_REGISTERS)["set_operating_mode"]
    assert w.writable_codes is None
    assert all(w.raw_allowed(c) for c in range(6)) and not w.raw_allowed(6)


def test_h_write_registers_returns_copy():
    a = reg.h_write_registers()
    a.clear()
    assert len(reg.h_write_registers()) == 3
