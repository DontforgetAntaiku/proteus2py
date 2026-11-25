"""Circuit spec (JSON-like dict or `CircuitSpec`) -> SDF -> ProSPICE -> decoded results.

    {"parts": [{"ref": "R1", "kind": "R", "nodes": ["in", "out"], "value": "1k"}, ...],
     "analysis": {"type": "transient", "tstop": "5m"},          # op | transient | ac | dc | noise  (see models.py)
     "probes": ["v(out)", "i(R1)"]}                             # default: every node voltage

Elements are registered in `elements.py`, analyses in `analyses.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from . import analyses, elements, pdf, prospice
from . import workspace as ws
from .elements import Ctx
from .errors import SpecError
from .models import CircuitSpec, Part
from .sdf import Sdf, num
from .units import to_float

__all__ = ["Built", "SpecError", "build", "run", "LADDER"]


@dataclass
class Built:
    sdf: Sdf
    probes: list[tuple[str, str]]           # (probe display name, label e.g. "v(out)")
    analysis: str
    meta: dict[str, Any] = field(default_factory=dict)


def validate(spec: dict[str, Any] | CircuitSpec) -> CircuitSpec:
    if isinstance(spec, CircuitSpec):
        return spec
    try:
        return CircuitSpec.model_validate(spec)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msgs.append(f"{loc}: {err['msg']}")
        raise SpecError("; ".join(msgs)) from e


def couple(spec: CircuitSpec) -> CircuitSpec:
    """K (mutual inductance) parts -> one SPICE subcircuit per group of coupled inductors (ProSPICE parses K natively inside subcircuits)."""
    ks = [p for p in spec.parts if p.kind == "K"]
    if not ks:
        return spec
    byref = {p.ref: p for p in spec.parts}
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for k in ks:
        ls = k.inductors or []
        if len(ls) != 2 or any(x not in byref or byref[x].kind != "L" for x in ls):
            raise SpecError(f'{k.ref}: K needs "inductors": [ref1, ref2] naming two L parts')
        parent[find(ls[0])] = find(ls[1])
    groups: dict[str, list[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    order = {p.ref: i for i, p in enumerate(spec.parts)}
    parts = [p for p in spec.parts if p.kind != "K" and p.ref not in parent]
    subckts = dict(spec.subckts)
    for gi, members in enumerate(sorted((sorted(g, key=lambda r: order[r]) for g in groups.values()), key=lambda g: order[g[0]])):
        name = f"KGRP{gi + 1}"
        nodes = [n for m in members for n in byref[m].nodes]
        lines = []
        for i, m in enumerate(members):
            ic = f" IC={num(byref[m].ic)}" if byref[m].ic is not None else ""
            lines.append(f"L{m} {2 * i + 1} {2 * i + 2} {to_float(byref[m].value)!r}{ic}")
        for k in ks:
            if k.inductors and k.inductors[0] in members:
                lines.append(f"K{k.ref} L{k.inductors[0]} L{k.inductors[1]} {to_float(k.value)!r}")
        subckts[name] = f".SUBCKT {name} " + " ".join(str(i + 1) for i in range(len(nodes))) + "\n" + "\n".join(lines) + "\n.ENDS"
        parts.append(Part(ref=f"X{name}", kind="X", nodes=nodes, subckt=name))
    return spec.model_copy(update={"parts": parts, "subckts": subckts})


def _probe_list(spec: CircuitSpec, ctx: Ctx) -> list[str]:
    if isinstance(spec.analysis, analyses.NoiseAnalysis):
        return [f"v({spec.analysis.output})"]
    return spec.probes or [f"v({n})" for n in ctx.nodes_seen]


def _add_probes(ctx: Ctx, want: list[str]) -> list[tuple[str, str]]:
    s = ctx.sdf
    probes: list[tuple[str, str]] = []
    for i, w in enumerate(want):
        m = re.fullmatch(r"\s*([vi])\(\s*([^)]+?)\s*\)\s*", w, re.I)
        if not m:
            raise SpecError(f"probe {w!r}: use v(node) or i(ref)")
        kind, what = m.group(1).lower(), m.group(2)
        pid = f"P{i:04d}"
        if kind == "v":
            n = ctx.see(what)
            if n not in s.nets and n != "GND":
                raise SpecError(f"probe {w}: unknown node {what!r}; nodes: {ctx.nodes_seen}")
            s.add_part(pid, "APROBE", w, [f"PARTITION={s.partition}", "PRIMITIVE=PROBE"], [(n, "IP", "*")])
        else:
            ref = what.split(".")[0]
            part = next((q for q in s.parts if q.ref == ref), None)
            if part is None:
                raise SpecError(f"probe {w}: no part {ref!r}")
            pin_i = int(what.split(".")[1]) - 1 if "." in what else 0
            net, pt, pn = part.pins[pin_i]
            mid = f"{ref}_{pn}_i"
            # re-route this pin through the current probe: net -> (+ IPROBE -) -> mid -> part pin
            s.nets[net] = [x for x in s.nets[net] if not (x[0] == ref and x[2] == pn)]
            part.pins[pin_i] = (mid, pt, pn)
            s.nets.setdefault(mid, []).append((ref, pt, pn))
            s.add_part(pid, "IPROBE", w, [f"PARTITION={s.partition}", "PRIMITIVE=PROBE"], [(net, "IP", "+"), (mid, "IP", "-")])
        probes.append((w, w))
    return probes


def build(spec: dict[str, Any] | CircuitSpec, partition: str | None = None) -> Built:
    sp = couple(validate(spec))
    prep = analyses.PREPARE.get(type(sp.analysis))
    if prep:
        sp = prep(sp)
    if not sp.parts:
        raise SpecError("no parts")
    refs = {p.ref for p in sp.parts}
    if len(refs) != len(sp.parts):
        raise SpecError("duplicate refs")
    sdf = Sdf(title=sp.title, **({"partition": partition} if partition else {}))
    sdf.props["TEMP"] = num(sp.temp)
    for k, v in sp.options.items():
        sdf.props[k.upper()] = num(v)
    ctx = Ctx(spec=sp, sdf=sdf, refs=refs)
    for p in sp.parts:
        handler = elements.REGISTRY.get(p.kind)
        if handler is None:
            raise SpecError(f"{p.ref}: no handler for kind {p.kind!r}")
        handler(ctx, p)
    sdf.models.update(ctx.models_used)
    elements.write_libraries(ctx)
    kind = analyses.REGISTRY[type(sp.analysis)](ctx, sp.analysis)
    probes = _add_probes(ctx, _probe_list(sp, ctx))
    return Built(sdf, probes, kind, {"nodes": ctx.nodes_seen, "ac_ref": ctx.ac_ref})


LADDER: list[dict[str, Any]] = [{}, {"ITL1": 1000}, {"ITL1": 1000, "RELTOL": "1e-3", "GMINSTEPS": 100, "SRCSTEPS": 100},
                                {"ITL1": 2000, "ITL2": 500, "GMIN": "1e-9", "RELTOL": "1e-3", "GMINSTEPS": 200, "SRCSTEPS": 200}]


def run(spec: dict[str, Any] | CircuitSpec, name: str = "sim", timeout: int = 180) -> tuple[Built, pdf.Result, dict[str, Any]]:
    """Build, simulate, decode. Convergence failures are retried with progressively looser solver options."""
    base = validate(spec)
    errors: list[str] = []
    for step in LADDER:
        sp = base.model_copy(update={"options": {**step, **base.options}})
        b = build(sp)
        fn = f"{ws.name_ok(name)}.SDF"
        (ws.exchange() / fn).write_text(b.sdf.text())
        for old in (ws.exchange() / "res").glob(f"{b.sdf.partition}.PDF"):
            old.unlink()
        r = prospice.run(fn, timeout=timeout)
        f = ws.exchange() / "res" / f"{b.sdf.partition}.PDF"
        bad = [x for x in r["console"] if x.startswith(("#W:", "#F:", "#X:")) and ("converge" in x or "failed to start" in x or "failed to complete" in x)]
        if f.exists() and not bad:
            r["options_used"] = sp.options
            return b, pdf.load(f), r
        errors = r["console"]
    raise RuntimeError("PROSPICE failed (also after relaxing solver options): " + " | ".join(x for x in errors if x[:3] in ("#W:", "#F:", "#X:")))
