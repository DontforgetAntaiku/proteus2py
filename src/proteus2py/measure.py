"""Numbers from decoded results: statistics, bandwidth, THD/harmonics, rise time, tables, CSV. Pure stdlib."""
from __future__ import annotations

import cmath
import csv
import math
import re
from pathlib import Path

from .pdf import Probe, Result


def _trapz(xs: list[float], ys: list[float]) -> float:
    return sum((xs[i + 1] - xs[i]) * (ys[i] + ys[i + 1]) / 2 for i in range(len(xs) - 1))


def stats(p: Probe) -> dict:
    """min / max / pk-pk / time-average / rms / first / last of a real transient probe."""
    xs, ys = p.x, [float(y.real) if isinstance(y, complex) else y for y in p.y]
    if not xs:
        return {}
    span = xs[-1] - xs[0]
    out = {"min": min(ys), "max": max(ys), "pkpk": max(ys) - min(ys), "first": ys[0], "last": ys[-1]}
    if span > 0:
        out["mean"] = _trapz(xs, ys) / span
        out["rms"] = math.sqrt(_trapz(xs, [y * y for y in ys]) / span)
    return out


def at(p: Probe, x: float):
    return p.at(x)


def uniform(p: Probe, x0: float, x1: float, n: int) -> tuple[list[float], list[float]]:
    xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
    return xs, [p.at(x).real if isinstance(p.at(x), complex) else p.at(x) for x in xs]


def harmonics(p: Probe, f0: float, periods: int = 5, start: float | None = None, nharm: int = 10, spp: int = 256) -> dict:
    """Fourier components at k*f0 over `periods` whole periods ending at the last time point (steady state).
    Returns {"fund": amplitude, "h": [amp k=1..nharm], "thd_pct": ..., "dc": ...}."""
    T = 1.0 / f0
    t1 = p.x[-1]
    t0 = (t1 - periods * T) if start is None else start
    if t0 < p.x[0]:
        raise ValueError(f"signal too short: need {periods} periods of {f0} Hz (have {p.x[-1] - p.x[0]:g} s)")
    n = periods * spp
    ts = [t0 + i * (periods * T) / n for i in range(n)]
    ys = [p.at(t) for t in ts]
    dc = sum(ys) / n
    amps = []
    for k in range(1, nharm + 1):
        w = 2 * math.pi * k * f0
        re = sum(y * math.cos(w * (t - t0)) for t, y in zip(ts, ys)) * 2 / n
        im = sum(y * math.sin(w * (t - t0)) for t, y in zip(ts, ys)) * 2 / n
        amps.append(math.hypot(re, im))
    fund = amps[0]
    thd = 100 * math.sqrt(sum(a * a for a in amps[1:])) / fund if fund > 0 else float("nan")
    return {"dc": dc, "fund": fund, "h": amps, "thd_pct": thd, "window": [t0, t1], "note": "interpolated from solver steps"}


def ac_table(p: Probe) -> list[dict]:
    return [{"f": x, "mag": abs(y), "db": 20 * math.log10(abs(y)) if abs(y) > 0 else -400.0,
             "phase_deg": math.degrees(cmath.phase(y))} for x, y in zip(p.x, p.y)]


def bandwidth(p: Probe, ref: str = "first", drop_db: float = 3.0103) -> dict:
    """-3 dB points of a gain curve (log-frequency interpolation). ref = 'first' | 'peak'."""
    rows = ac_table(p)
    if len(rows) < 3:
        raise ValueError("need an AC sweep with several points")
    peak = max(rows, key=lambda r: r["db"])
    g0 = rows[0]["db"] if ref == "first" else peak["db"]
    thr = g0 - drop_db

    def cross(seq):
        for a, b in zip(seq, seq[1:]):
            if (a["db"] - thr) * (b["db"] - thr) < 0:
                t = (thr - a["db"]) / (b["db"] - a["db"])
                return 10 ** (math.log10(a["f"]) + t * (math.log10(b["f"]) - math.log10(a["f"])))
        return None
    i_pk = rows.index(peak)
    return {"gain_ref_db": g0, "peak_db": peak["db"], "peak_f": peak["f"],
            "f_low_3db": cross(list(reversed(rows[:i_pk + 1]))) if ref == "peak" else None,
            "f_high_3db": cross(rows[i_pk:] if ref == "peak" else rows)}


def edge(p: Probe, lo: float = 0.1, hi: float = 0.9, after: float = 0.0) -> dict:
    """10-90 % rise (or fall) time and overshoot of the first transition after `after`."""
    pts = [(x, y) for x, y in zip(p.x, p.y) if x >= after]
    ys = [y for _, y in pts]
    y0, y1 = ys[0], ys[-1]
    if y0 == y1:
        raise ValueError("no transition")
    a, b = y0 + lo * (y1 - y0), y0 + hi * (y1 - y0)

    def t_at(level):
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            if (ya - level) * (yb - level) <= 0 and ya != yb:
                return xa + (level - ya) / (yb - ya) * (xb - xa)
        return None
    ta, tb = t_at(a), t_at(b)
    over = (max(ys) - y1) / (y1 - y0) * 100 if y1 > y0 else (y1 - min(ys)) / (y0 - y1) * 100
    return {"from": y0, "to": y1, "t_lo": ta, "t_hi": tb, "edge_time": (tb - ta) if ta is not None and tb is not None else None,
            "overshoot_pct": max(over, 0.0)}


def table(res: Result, names: list[str] | None = None, n: int = 40, log: bool = False, complex_as: str = "db") -> dict:
    """Uniform-grid table: columns x + each probe (complex probes as dB/phase pairs)."""
    probes = [res.probe(x) for x in names] if names else res.probes
    d = res.resample(n, log=log)
    cols = ["x"]
    rows = [[x] for x in d["x"]]
    for p in probes:
        col = d[p.name]
        if p.complex:
            cols += [f"{p.name} [dB]", f"{p.name} [deg]"]
            for r, y in zip(rows, col):
                r += [20 * math.log10(abs(y)) if abs(y) > 0 else -400.0, math.degrees(cmath.phase(y))]
        else:
            cols.append(p.name)
            for r, y in zip(rows, col):
                r.append(y)
    return {"columns": cols, "rows": rows}


def write_csv(res: Result, path: Path, max_points: int = 20000) -> int:
    xs = sorted({x for p in res.probes for x in p.x})
    if len(xs) > max_points:
        step = len(xs) / max_points
        xs = [xs[int(i * step)] for i in range(max_points)]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        head = ["x"]
        for p in res.probes:
            head += [f"{p.name}.re", f"{p.name}.im"] if p.complex else [p.name]
        w.writerow(head)
        for x in xs:
            row = [x]
            for p in res.probes:
                y = p.at(x)
                row += [y.real, y.imag] if p.complex else [y]
            w.writerow(row)
    return len(xs)


# ---- derived probes: "db(v(out)/v(in))", "v(in)/i(R1)" ... -----------------------------------------------------------
_ID = re.compile(r"\b([vi])\(\s*([^()]+?)\s*\)", re.I)
_FUN = {"abs": abs, "mag": abs, "db": lambda z: 20 * math.log10(max(abs(z), 1e-300)), "ph": lambda z: math.degrees(cmath.phase(z)),
        "re": lambda z: z.real, "im": lambda z: z.imag, "sqrt": cmath.sqrt, "log10": lambda z: math.log10(abs(z)),
        "ln": lambda z: math.log(abs(z)), "exp": cmath.exp, "sin": cmath.sin, "cos": cmath.cos, "pi": math.pi}


def derived(res: Result, expr: str, n: int = 400, log: bool | None = None) -> Probe:
    """Evaluate an expression of probes over a grid -> a virtual Probe usable by every measure function.
    Functions: db mag ph re im abs sqrt log10 ln exp sin cos. Complex (AC) probes stay complex."""
    names = {m.group(0): m for m in _ID.finditer(expr)}
    used = []
    code = expr
    for i, (text, m) in enumerate(sorted(names.items(), key=lambda kv: -len(kv[0]))):
        key = f"_p{i}"
        used.append((key, res.probe(f"{m.group(1).lower()}({m.group(2)})")))
        code = code.replace(text, key)
    if not used:
        raise ValueError(f"expression {expr!r} contains no v(..)/i(..) probes")
    xs_all = sorted({x for _, p in used for x in p.x})
    if len(xs_all) > n:
        lo, hi = xs_all[0], xs_all[-1]
        lg = (lo > 0 and hi / lo > 50) if log is None else log
        xs_all = [lo * (hi / lo) ** (k / (n - 1)) for k in range(n)] if lg else [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    code = code.replace("^", "**")
    if not re.fullmatch(r"[\w\s+\-*/().,]*", code):
        raise ValueError(f"unsupported characters in {expr!r}")
    ys = []
    for x in xs_all:
        env = {k: p.at(x) for k, p in used}
        env.update(_FUN)
        try:
            ys.append(eval(code, {"__builtins__": {}}, env))             # noqa: S307  (charset checked above)
        except ZeroDivisionError:
            ys.append(float("nan"))
    cplx = any(isinstance(y, complex) and abs(y.imag) > 0 for y in ys)
    if not cplx:
        ys = [y.real if isinstance(y, complex) else float(y) for y in ys]
    pr = Probe(0, expr, expr, 16 if cplx else 8)
    pr.x, pr.y = xs_all, ys
    return pr
