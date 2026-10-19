"""Serialise J/L/M-relevant behaviour of registers/const into plain data.

Shared by the fixture generator (run against the upstream baseline) and the
regression test (run against the working tree). Only public functions whose
J/L/M behaviour must stay identical are exercised, called with the baseline's
signatures (new optional parameters are left at their defaults).
"""

from __future__ import annotations

from itertools import product

JLM = ("J", "L", "M")
SPEC_FIELDS = (
    "obj", "signed", "scale", "unit", "device_class", "state_class",
    "entity_category", "module", "enum", "module_flag", "capability", "re_only",
)


def snapshot(reg, const) -> dict:
    out: dict = {}
    out["addresses"] = {
        ver: {s.key: s.resolve_address(ver) for s in reg.REGISTERS} for ver in JLM
    }
    out["spec_meta"] = {
        s.key: {f: getattr(s, f) for f in SPEC_FIELDS} for s in reg.REGISTERS
    }
    out["energy_groups"] = [
        [g.key, list(g.registers), g.module, g.capability] for g in reg.ENERGY_GROUPS
    ]
    out["write_specs"] = [
        [w.key, w.address, w.kind, w.min_value, w.max_value, w.step, w.encode, w.module_flag]
        for w in reg.WRITE_REGISTERS
    ]
    out["write_encoding"] = {
        w.key: [w.to_raw(v) for v in (w.min_value, (w.min_value + w.max_value) / 2, w.max_value)]
        for w in reg.WRITE_REGISTERS
        if w.kind == reg.KIND_NUMBER
    }
    modules_sets = {
        "core": frozenset(reg.CORE_MODULES),
        "all": frozenset(reg.ALL_MODULES),
    }
    cap_sets = {
        "none": frozenset(),
        "all": frozenset(
            {reg.CAP_ELECTRIC_METER, reg.CAP_HEAT_METER, reg.CAP_FLOW_SENSOR, reg.CAP_INVERTER_FREQ}
        ),
    }
    plans: dict = {}
    for ver, (mname, mods), (cname, caps), re_, ctrl in product(
        JLM, modules_sets.items(), cap_sets.items(), (True, False), (True, False)
    ):
        specs = reg.active_registers(
            version=ver, enabled_modules=mods, capabilities=caps, include_re=re_
        )
        groups = reg.active_energy_groups(enabled_modules=mods, capabilities=caps)
        writes = reg.active_write_registers(enabled_modules=mods) if ctrl else []
        extra = {w.address for w in writes}
        plan = reg.build_read_plan(specs, groups, ver, extra)
        plans[f"{ver}|{mname}|{cname}|re={re_}|ctrl={ctrl}"] = {
            "keys": sorted(s.key for s in specs),
            "plan": [list(p) for p in plan],
            "writes": [w.key for w in writes],
        }
    out["plans"] = plans
    out["enum_maps"] = {
        ver: {
            name: {str(k): v for k, v in const.get_enum_map(name, ver).items()}
            for name in (
                reg.ENUM_STATUS, reg.ENUM_LOCK, reg.ENUM_FAULT, reg.ENUM_SENSOR_ERROR,
                reg.ENUM_SG_READY, reg.ENUM_OPERATING_MODE,
            )
        }
        for ver in JLM
    }
    return out
