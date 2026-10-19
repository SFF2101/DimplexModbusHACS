"""Regenerate tests/fixtures/jlm_baseline.json from an upstream commit.

The fixture freezes J/L/M behaviour (addresses, decode metadata, read plans,
write specs, enum maps) of upstream nb3rt/DimplexModbusHACS at the H-series
branch point, so H-series changes can be proven not to alter J/L/M.

Usage (from the repo root, needs git history containing the commit):
    python tests/tools/make_jlm_baseline.py a76937c3513627de2898d9514909722ffc5cf06c
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from jlm_snapshot import snapshot  # noqa: E402

FILES = ("registers.py", "const.py")


def main(commit: str) -> None:
    tmp = tempfile.mkdtemp()
    for name in FILES:
        src = subprocess.check_output(
            ["git", "-C", ROOT, "show", f"{commit}:custom_components/dimplex_wpm/{name}"]
        )
        with open(os.path.join(tmp, name), "wb") as fh:
            fh.write(src)
    spec = importlib.util.spec_from_loader("baseline_pkg", loader=None, is_package=True)
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [tmp]
    sys.modules["baseline_pkg"] = pkg
    reg = importlib.import_module("baseline_pkg.registers")
    const = importlib.import_module("baseline_pkg.const")
    data = {"source_commit": commit, **snapshot(reg, const)}
    out = os.path.join(ROOT, "tests", "fixtures", "jlm_baseline.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1])
