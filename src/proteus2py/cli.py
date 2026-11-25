"""`proteus2py` command line: doctor, setup, spice."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import workspace as ws


def _doctor() -> int:
    ok = True
    print(f"proteus2py {__version__}   data dir: {ws.home()}")
    if ws.IS_WINDOWS:
        exe = ws.proteus_dir() / "BIN" / "PROSPICE.EXE"
        print(f"  Proteus install: {ws.proteus_dir()}  PROSPICE.EXE {'ok' if exe.exists() else 'MISSING (set PROTEUS_DIR)'}")
        return 0 if exe.exists() else 1
    for tool in ("bwrap", "wine", "Xvfb", "xdotool", "import"):
        p = shutil.which(tool)
        gui_only = tool in ("Xvfb", "xdotool", "import")
        print(f"  {tool:8s} {p or 'MISSING'}{'  (GUI path only)' if gui_only and not p else ''}")
        ok &= bool(p) or gui_only
    pristine = ws.home() / "prefix-pristine"
    print(f"  Wine prefix with Proteus: {'ok' if pristine.exists() else 'MISSING (run: proteus2py setup <prefix>)'}")
    ok &= pristine.exists()
    try:
        import matplotlib  # noqa: F401
        print("  matplotlib: ok (plot_run available)")
    except ImportError:
        print("  matplotlib: not installed (pip install 'proteus2py[plot]' for plot_run)")
    return 0 if ok else 1


def _setup(prefix: str) -> int:
    src = Path(prefix).expanduser()
    exe = src / "drive_c/Program Files (x86)/Labcenter Electronics/Proteus 8 Professional/BIN/PROSPICE.EXE"
    if not exe.exists():
        print(f"{src} does not contain Proteus 8 Professional at the default path:\n  {exe}", file=sys.stderr)
        return 2
    dst = ws.home() / "prefix-pristine"
    if dst.exists():
        print(f"{dst} exists already; delete it first to re-run setup", file=sys.stderr)
        return 2
    ws.home().mkdir(parents=True, exist_ok=True)
    print(f"copying {src} -> {dst} (about 2 GB) ...")
    subprocess.run(["cp", "-a", "--reflink=auto", str(src), str(dst)], check=True)
    # keep the sandbox closed: no symlinks into the real home, no host root drive
    for link in dst.rglob("*"):
        if link.is_symlink() and str(link.readlink()).startswith(("/home", "/root")):
            link.unlink()
            link.mkdir()
    (dst / "dosdevices" / "z:").unlink(missing_ok=True)
    print("done. Try: proteus2py spice examples/rc_lowpass.cir")
    return 0


def _spice(path: str, analysis: int, as_json: bool) -> int:
    from . import sim, spice
    spec = spice.parse(Path(path).read_text())
    if spec["analyses"]:
        spec["analysis"] = spec["analyses"][analysis]
    r = sim.simulate(spec, Path(path).stem)
    if as_json:
        print(r.model_dump_json(indent=1, exclude_none=True))
        return 0
    print(f"{r.analysis}: {len(r.probes)} probes, x = {r.x_range[0]:g} .. {r.x_range[1]:g}   csv: {r.csv}")
    for key in ("op", "stats", "gain", "noise"):
        v = getattr(r, key)
        if v is not None:
            print(json.dumps(v if isinstance(v, dict) else v.model_dump(), indent=1, default=lambda o: o.model_dump()))
    for w in r.warnings:
        print("warning:", w)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="proteus2py", description="Script Proteus 8 from Python and AI assistants.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check the environment")
    s = sub.add_parser("setup", help="copy a Wine prefix that has Proteus 8 installed into the sandbox")
    s.add_argument("prefix")
    c = sub.add_parser("spice", help="simulate a SPICE deck headless with Proteus's engine")
    c.add_argument("deck")
    c.add_argument("--analysis", type=int, default=0, help="index of the analysis card to run")
    c.add_argument("--json", action="store_true")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        return _doctor()
    if a.cmd == "setup":
        return _setup(a.prefix)
    if a.cmd == "spice":
        try:
            return _spice(a.deck, a.analysis, a.json)
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    from .server import main as serve
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
