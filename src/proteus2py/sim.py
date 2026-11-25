"""Run a circuit spec headless and shape the answer: summary numbers, a downsampled table, a full-resolution CSV, optional plot."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from . import circuit, measure, pdf
from . import workspace as ws
from .models import CircuitSpec, MonteCarloResult, MonteCarloRun, SimResult, SweepResult, SweepRow
from .units import to_float

RUNS: dict[str, dict[str, Any]] = {}            # name -> {"spec","built","result","analysis"}


def _as_dict(spec: dict[str, Any] | CircuitSpec) -> dict[str, Any]:
    return spec.model_dump(exclude_unset=True) if isinstance(spec, CircuitSpec) else spec


def _warnings(console: list[str]) -> list[str]:
    return [x[3:].strip() for x in console if x.startswith(("#W:", "#F:", "#X:"))]


def simulate(spec: dict[str, Any] | CircuitSpec, name: str = "sim", max_rows: int = 40, timeout: int = 180) -> SimResult:
    spec = circuit.validate(spec)
    b, res, info = circuit.run(spec, name, timeout)
    RUNS[name] = {"spec": spec, "built": b, "result": res, "analysis": b.analysis}
    out: dict[str, Any] = {"name": name, "analysis": b.analysis, "probes": [p.name for p in res.probes], "x_range": [res.x_min, res.x_max],
                 "warnings": _warnings(info["console"]), "solver_options": info.get("options_used", {})}
    csvp = ws.exchange() / f"{name}.csv"
    out["csv"] = str(csvp)
    out["csv_rows"] = measure.write_csv(res, csvp)
    t = b.analysis
    if t == "op":
        out["op"] = {p.name: (p.y[0].real if isinstance(p.y[0], complex) else p.y[0]) for p in res.probes if p.y}
    elif t == "transient":
        out["stats"] = {p.name: measure.stats(p) for p in res.probes if p.y}
        out["table"] = measure.table(res, n=max_rows)
        dig = {p.name: [(x, int(y)) for x, y in zip(p.x, p.y)][:200] for p in res.probes if p.kind == pdf.KIND_DIGITAL}
        if dig:
            out["digital"] = dig
    elif t == "distortion":
        an = spec.analysis
        h = measure.harmonics(res.probes[0], to_float(an.f0), an.periods, None, an.nharm + 1)
        rel = [100 * a / h["fund"] for a in h["h"][1:]]
        out["distortion"] = {"f0": to_float(an.f0), "dc": h["dc"], "fundamental": h["fund"], "harmonics": h["h"][1:], "harmonics_rel_pct": rel, "thd_pct": h["thd_pct"]}
        out["table"] = measure.table(res, n=max_rows)
    elif t == "noise":
        # ProSPICE stores noise as complex: real = output noise density [V/rtHz], imag = input-referred density [V/rtHz]
        p0 = res.probes[0]
        xs = p0.x
        onoise = [y.real for y in p0.y]
        inoise = [y.imag for y in p0.y]

        def total(v: list[float]) -> float:
            return math.sqrt(sum((xs[i + 1] - xs[i]) * (v[i] ** 2 + v[i + 1] ** 2) / 2 for i in range(len(xs) - 1)))
        out["noise"] = {"output_V_per_rtHz": {"at_start": onoise[0], "at_end": onoise[-1], "max": max(onoise)}, "total_output_rms_V": total(onoise),
                        "input_referred_V_per_rtHz": {"at_start": inoise[0], "at_end": inoise[-1]}, "total_input_rms_V": total(inoise)}
        step = max(1, len(xs) // max_rows)
        out["table"] = {"columns": ["f", "output V/rtHz", "input-referred V/rtHz"], "rows": [[xs[i], onoise[i], inoise[i]] for i in range(0, len(xs), step)]}
    elif t == "ac":
        out["table"] = measure.table(res, n=max_rows, log=True)
        out["gain"] = {p.name: _ac_summary(p) for p in res.probes if p.complex and p.y}
    else:
        out["table"] = measure.table(res, n=max_rows)
        if len(res.family) > 1:                              # nested DC sweep: one curve per outer value
            an = spec.analysis
            s2, d2 = getattr(an, "start2", None), getattr(an, "step2", None)
            out["family"] = [{"outer": (to_float(s2) + k * to_float(d2)) if s2 is not None and d2 is not None else float(k),
                              "table": measure.table(c, n=max(8, max_rows // 4))} for k, c in enumerate(res.family)]
    return SimResult.model_validate(out)


def _ac_summary(p: pdf.Probe) -> dict[str, Any]:
    try:
        bw = measure.bandwidth(p)
    except ValueError:
        bw = {}
    rows = measure.ac_table(p)
    return {"db_at_start": rows[0]["db"], "peak_db": bw.get("peak_db"), "peak_f": bw.get("peak_f"), "f_high_3db": bw.get("f_high_3db")}


def get(name: str) -> dict[str, Any]:
    if name not in RUNS:
        raise KeyError(f"no run named {name!r}; run simulate first (have {sorted(RUNS)})")
    return RUNS[name]


def measure_run(name: str, what: str, probe: str, **kw: Any) -> dict[str, Any]:
    r = get(name)["result"]
    mode = kw.pop("interp", "linear") or "linear"            # linear (Proteus's drawing) | hold (step between samples)
    try:
        p = r.probe(probe).with_interp(mode)
    except KeyError:
        p = measure.derived(r, probe, interp=mode)           # expression such as db(v(out)/v(in)) or v(in)/i(R1)
    what = what.lower()
    if what == "stats":
        return measure.stats(p)
    if what == "at":
        v = p.at(float(kw["x"]))
        return {"x": kw["x"], "value": v if not isinstance(v, complex) else {"re": v.real, "im": v.imag, "mag": abs(v)}}
    if what == "bandwidth":
        return measure.bandwidth(p, kw.get("ref", "first"))
    if what in ("harmonics", "thd"):
        return measure.harmonics(p, float(kw["f0"]), int(kw.get("periods", 5)), None, int(kw.get("nharm", 10)))
    if what == "edge":
        return measure.edge(p, after=float(kw.get("after", 0)))
    raise ValueError("what: stats | at | bandwidth | harmonics | edge")


def plot(name: str, probes: list[str] | None, file_name: str, title: str | None = None) -> Path:
    try:
        import matplotlib
    except ImportError as e:
        raise RuntimeError("plotting needs matplotlib: pip install 'proteus2py[plot]'") from e
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    run = get(name)
    res = run["result"]
    ps = [res.probe(x) for x in probes] if probes else res.probes
    ac = run["analysis"] == "ac"
    if ac:
        fig, (a1, a2) = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
        for p in ps:
            if not p.complex:
                continue
            a1.semilogx(p.x, [20 * math.log10(max(abs(y), 1e-30)) for y in p.y], label=p.name)
            a2.semilogx(p.x, [math.degrees(math.atan2(y.imag, y.real)) for y in p.y], label=p.name)
        a1.set_ylabel("gain [dB]")
        a2.set_ylabel("phase [deg]")
        a2.set_xlabel("frequency [Hz]")
        a1.legend()
        for a in (a1, a2):
            a.grid(True, which="both", alpha=.3)
    else:
        fig, ax = plt.subplots(figsize=(9, 5))
        for p in ps:
            ax.plot(p.x, [y.real if isinstance(y, complex) else y for y in p.y], label=p.name)
        ax.set_xlabel({"transient": "time [s]", "dc": "sweep"}.get(run["analysis"], "x"))
        ax.legend()
        ax.grid(True, alpha=.3)
    fig.suptitle(title or name)
    out = ws.exchange() / (file_name if file_name.endswith(".png") else file_name + ".png")
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out


def derive(name: str, expr: str, n: int = 40, interp: str = "linear") -> dict[str, Any]:
    """Table of an expression of the run's probes, e.g. "db(v(out)/v(in))", "v(in)/i(R1)" (input impedance)."""
    r = get(name)["result"]
    p = measure.derived(r, expr, n=max(n, 2), interp=interp)
    ac = get(name)["analysis"] == "ac"
    if p.complex:
        return {"expr": expr, "columns": ["x", "mag", "dB", "deg"],
                "rows": [[x, abs(y), 20 * math.log10(max(abs(y), 1e-300)), math.degrees(math.atan2(y.imag, y.real))] for x, y in zip(p.x, p.y)]}
    return {"expr": expr, "columns": ["x", "value"], "rows": [[x, y] for x, y in zip(p.x, p.y)], "log_x": ac}


def search_models(query: str, limit: int = 30) -> dict:
    """SPICE models (diodes, BJTs, MOSFETs, op-amp macromodels) and Proteus device-library parts matching the query."""
    from . import library
    q = query.upper()
    spice_hits = []
    for n in library.names():
        if all(w in n for w in q.split()):
            t = library.get(n) or ""
            kind = "subckt" if ".SUBCKT" in t.upper() else (library.model(n) or ("?",))[0]
            spice_hits.append({"name": n, "kind": kind})
            if len(spice_hits) >= limit:
                break
    return {"spice_models": spice_hits, "devices": library.search_devices(query, limit)}


def model_text(name: str) -> str:
    from . import library
    t = library.get(name)
    if t is None:
        raise KeyError(f"{name!r} not in the Proteus SPICE libraries")
    return re.sub(r"\r", "", t)


# ---- parameter sweeps / Monte Carlo -------------------------------------------------------------------------------
def _metric(run_name: str, metric: dict[str, Any]) -> float:
    """metric = {"what":"stats","probe":"v(out)","field":"rms"} | {"what":"op","probe":"v(out)"} |
    {"what":"bandwidth","probe":"v(out)","field":"f_high_3db"} | {"what":"at","probe":"v(out)","x":1000,"field":"mag"} | {"what":"harmonics",...,"field":"thd_pct"}"""
    w = metric["what"].lower()
    r = get(run_name)["result"]
    try:
        p = r.probe(metric["probe"])
    except KeyError:
        p = measure.derived(r, metric["probe"])
    if w == "op":
        y = p.y[0]
        return y.real if isinstance(y, complex) else y
    kw = {k: v for k, v in metric.items() if k not in ("what", "probe", "field")}
    d = measure_run(run_name, w, metric["probe"], **kw)
    f = metric.get("field")
    v = d[f] if f else d.get("value")
    if isinstance(v, dict):
        v = v.get("mag", v)
    if v is None:
        raise ValueError(f"metric {metric} gave no value: {d}")
    return float(v)


def _apply(spec: dict[str, Any], target: str, value: Any) -> dict[str, Any]:
    """target: "R1" (part value) | "R1.value" | "temp" | "V1.wave.freq" | "V1.value" -> modified deep copy."""
    import copy
    s = copy.deepcopy(spec)
    if target == "temp":
        s["temp"] = value
        return s
    ref, *path = target.split(".")
    part = next((p for p in s["parts"] if p["ref"] == ref), None)
    if part is None:
        raise KeyError(f"no part {ref!r}")
    path = path or ["value"]
    d = part
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value
    return s


def sweep(spec: dict[str, Any] | CircuitSpec, target: str, values: list[Any], metric: dict[str, Any], name: str = "sweep") -> SweepResult:
    """Re-simulate for each value of `target` (a part value, 'temp', 'V1.wave.freq'...) and collect one metric per run."""
    base = _as_dict(spec)
    rows: list[SweepRow] = []
    for i, v in enumerate(values):
        try:
            simulate(_apply(base, target, v), f"{name}_{i}", max_rows=2)
            rows.append(SweepRow(value=v, result=_metric(f"{name}_{i}", metric)))
        except Exception as e:                           # noqa: BLE001
            rows.append(SweepRow(value=v, error=str(e)[:200]))
    return SweepResult(target=target, metric=metric, rows=rows)


def _tol(x: str | float) -> float:
    if isinstance(x, str) and x.strip().endswith("%"):
        return float(x.strip()[:-1]) / 100
    return float(x)


def monte_carlo(spec: dict[str, Any] | CircuitSpec, tolerances: dict[str, str | float], metric: dict[str, Any], runs: int = 20, seed: int = 1,
                distribution: str = "gauss", name: str = "mc") -> MonteCarloResult:
    """Randomise part values (R/C/L; tolerance 5% or 0.05; gauss = sigma tol/3, uniform = +-tol) and collect one metric per run."""
    import random
    base = _as_dict(spec)
    rng = random.Random(seed)
    vals: list[float] = []
    runs_out: list[MonteCarloRun] = []
    for i in range(runs):
        s = base
        draw: dict[str, float] = {}
        for ref, tol in tolerances.items():
            part = next(p for p in base["parts"] if p["ref"] == ref)
            t = _tol(tol)
            f = rng.gauss(0, t / 3) if distribution == "gauss" else rng.uniform(-t, t)
            draw[ref] = to_float(part["value"]) * (1 + f)
            s = _apply(s, ref, draw[ref])
        try:
            simulate(s, f"{name}_{i}", max_rows=2)
            m = _metric(f"{name}_{i}", metric)
            vals.append(m)
            runs_out.append(MonteCarloRun(values=draw, metric=m))
        except Exception as e:                           # noqa: BLE001
            runs_out.append(MonteCarloRun(values=draw, error=str(e)[:200]))
    if not vals:
        return MonteCarloResult(runs=runs_out, error="all runs failed")
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / max(len(vals) - 1, 1)) ** 0.5
    return MonteCarloResult(n=len(vals), mean=mean, std=sd, min=min(vals), max=max(vals), runs=runs_out)


# ---- run an SDF that ISIS wrote (project netlist) headless ---------------------------------------------------------
def run_sdf(sdf_path: Path, name: str, timeout: int = 180, max_rows: int = 40) -> SimResult:
    """Simulate an ISIS-written netlist headless; decode results; register as run `name` (measure/derive/plot_run work on it)."""
    from . import prospice
    text = sdf_path.read_text(errors="replace")
    props = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text.split("*MODELDEFS")[0], re.M))
    fn = f"{ws.name_ok(name)}.SDF"
    (ws.exchange() / fn).write_text(text)
    r = prospice.run(fn, timeout)
    part = props.get("PARTITION", "")
    f = ws.exchange() / "res" / f"{part}.PDF"
    if not f.exists():
        raise RuntimeError(f"no result for partition {part!r}: {r['console']}")
    res = pdf.load(f)
    st = props.get("SIMTYPE", "")
    kind = {"TRANSIENT": "transient", "FREQUENCY": "ac", "DCSWEEP": "dc", "NOISE": "noise"}.get(st, "dc" if props.get("ANALYSIS") == "DC" else "transient")
    RUNS[name] = {"spec": None, "built": None, "result": res, "analysis": kind, "sdf": str(sdf_path)}
    csvp = ws.exchange() / f"{name}.csv"
    n = measure.write_csv(res, csvp)
    out: dict[str, Any] = {"name": name, "analysis": kind, "partition": part, "probes": [p.name for p in res.probes], "x_range": [res.x_min, res.x_max],
                           "warnings": _warnings(r["console"]), "table": measure.table(res, n=max_rows, log=(kind == "ac")), "csv": str(csvp), "csv_rows": n}
    if kind == "transient":
        out["stats"] = {p.name: measure.stats(p) for p in res.probes if p.y and not p.complex}
    return SimResult.model_validate(out)
