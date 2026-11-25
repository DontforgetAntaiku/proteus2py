"""Headless PROSPICE.EXE run on an SDF netlist (Linux: inside the Wine sandbox; Windows: the installed program, no sandbox)."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from . import workspace as ws


def _command(sdf_name: str, res_dir: str) -> tuple[list[str], dict[str, str] | None]:
    if ws.IS_WINDOWS:
        exe = ws.proteus_dir() / "BIN" / "PROSPICE.EXE"
        return [str(exe), "-M:" + str(ws.proteus_dir() / "DATA" / "MODELS"), ws.guest_path(sdf_name)], {**os.environ, "RESULTS": res_dir}
    cmd = ws.bwrap(["env", r"RESULTS=C:\exchange\res", "wine", ws.BIN + r"\PROSPICE.EXE", "-M:" + ws.MODELS, ws.guest_path(sdf_name)])
    return cmd, None


def run(sdf_name: str, timeout: int = 120) -> dict[str, Any]:
    """sdf_name: file in exchange (e.g. 'x.SDF'). Returns console lines and the result files written to exchange/res."""
    ws.name_ok(sdf_name)
    if not (ws.exchange() / sdf_name).exists():
        raise FileNotFoundError(sdf_name)
    res = ws.exchange() / "res"
    res.mkdir(exist_ok=True)
    before = {p.name for p in res.iterdir()}
    cmd, env = _command(sdf_name, str(res))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(ws.exchange()) if ws.IS_WINDOWS else None)
    lines = [x for x in (r.stdout + r.stderr).splitlines() if re.match(r"#[IFXW]:", x)]
    files = sorted(p.name for p in res.iterdir())
    return {"rc": r.returncode, "console": lines[-40:], "result_files": files, "new": [f for f in files if f not in before]}
