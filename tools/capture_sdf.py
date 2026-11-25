#!/usr/bin/env python3
"""Run bundled Proteus sample designs in the sandbox and capture the SDF netlists ISIS hands to PROSPICE (+ result PDFs).
usage: capture_sdf.py OUTDIR "Graph Based Simulation/Lpf" [more sample paths without .pdsprj] [--key space|F12] [--wait 40]"""
import argparse, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proteus2py import session, workspace as ws

SAMPLES = ws.prefix_work() / "drive_c/Program Files (x86)/Labcenter Electronics/Proteus 8 Professional/DATA/SAMPLES"
ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("samples", nargs="+")
ap.add_argument("--key", default="space"); ap.add_argument("--wait", type=float, default=40)
a = ap.parse_args()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
for smp in a.samples:
    tag = "".join(c if c.isalnum() else "_" for c in smp)[-40:]
    src = SAMPLES / (smp + ".pdsprj")
    shutil.copyfile(src, ws.project(tag))
    cap = ws.exchange() / ("cap_" + tag); shutil.rmtree(cap, ignore_errors=True); cap.mkdir()
    s = session.Session(tag, load_wait=30)
    try:
        s.bg(f"while true; do find /home/sbx/.wine/drive_c/users /home/sbx/.wine/drive_c/windows/temp /home/sbx/exchange -maxdepth 6 -type f "
             f"-iname 'LISA*.SDF' -newer /tmp/stamp -not -path '*/cap_*' -exec cp -f --backup=numbered {{}} /home/sbx/exchange/cap_{tag}/ \\; ; sleep 0.1; done")
        s.sh("touch /tmp/stamp"); time.sleep(1)
        s.click(1200, 560); time.sleep(1); s.key(a.key); time.sleep(a.wait)
        s.shot(f"cap_{tag}_after")
        time.sleep(2)
    finally:
        s.close()
    dst = out / tag; shutil.rmtree(dst, ignore_errors=True); shutil.copytree(cap, dst)
    shutil.copyfile(ws.exchange() / f"cap_{tag}_after.png", dst / "after.png")
    print(tag, sorted(p.name for p in dst.iterdir()))
