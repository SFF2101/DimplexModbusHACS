"""J/L/M must behave exactly as upstream baseline a76937c (frozen fixture)."""

from __future__ import annotations

import json
import os

from conftest import load
from jlm_snapshot import snapshot

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "jlm_baseline.json")


def _current() -> dict:
    return json.loads(json.dumps(snapshot(load("registers"), load("const")), sort_keys=True))


def _baseline() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.pop("source_commit") == "a76937c3513627de2898d9514909722ffc5cf06c"
    return data


def test_jlm_addresses_unchanged():
    assert _current()["addresses"] == _baseline()["addresses"]


def test_jlm_spec_metadata_unchanged():
    assert _current()["spec_meta"] == _baseline()["spec_meta"]


def test_jlm_read_plans_unchanged():
    cur, base = _current()["plans"], _baseline()["plans"]
    assert cur.keys() == base.keys()
    for name in base:
        assert cur[name] == base[name], name


def test_jlm_writes_energy_enums_unchanged():
    cur, base = _current(), _baseline()
    for part in ("write_specs", "write_encoding", "energy_groups", "enum_maps"):
        assert cur[part] == base[part], part
