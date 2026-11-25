"""Headless PROSPICE run on an SDF netlist (experimental: output .PDF is binary and not decoded yet)."""
from __future__ import annotations

import re
import subprocess

from . import workspace as ws


def run(sdf_name: str, timeout: int = 120) -> dict:
    """sdf_name: file in exchange (e.g. 'x.SDF'). Returns console lines and the result files written to exchange/res."""
    ws.name_ok(sdf_name)
    if not (ws.exchange() / sdf_name).exists():
        raise FileNotFoundError(sdf_name)
    res = ws.exchange() / "res"
    res.mkdir(exist_ok=True)
    before = {p.name for p in res.iterdir()}
    cmd = ws.bwrap(["env", r"RESULTS=C:\exchange\res", "wine", ws.BIN + r"\PROSPICE.EXE", "-M:" + ws.MODELS,
                    "C:\\exchange\\" + sdf_name])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    lines = [x for x in (r.stdout + r.stderr).splitlines() if re.match(r"#[IFXW]:", x)]
    files = sorted(p.name for p in res.iterdir())
    return {"rc": r.returncode, "console": lines[-40:], "result_files": files, "new": [f for f in files if f not in before],
            "note": "binary .PDF results not decoded yet"}
