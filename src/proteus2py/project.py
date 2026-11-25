"""Open a Proteus project WITHOUT the GUI: ROOT.DSN (generic reader) + ROOT.CDB -> nets, components, generators -> circuit spec -> ProSPICE.

Components come from `dsn_devices`, connectivity from `dsn_nets`; ISIS generators (`$SINEGEN`, `$PULSEGEN`, `$DCGEN`, `$PWLINGEN`) become voltage sources and
voltage probes (`$VPROBE`) become default probes. Analyses are not read from the graphs: pass one (op, transient, ac, dc, noise, distortion).
"""
from __future__ import annotations

import re
import zipfile
from os import PathLike
from typing import Any

from pydantic import BaseModel, Field

from . import dsn_devices, dsn_nets
from .dsn import parse_dsn
from .models import SimResult


class ProjectSpec(BaseModel):
    spec: dict[str, Any]
    ignored: list[str] = Field(default_factory=list)                 # non-simulated components
    unsupported: dict[str, str] = Field(default_factory=dict)        # component -> why it cannot be simulated (VSM models, behavioural parts ...)
    notes: list[str] = Field(default_factory=list)
    nets: dict[str, str] = Field(default_factory=dict)               # sanitised spec node name -> original ISIS net name


def _safe(name: str, used: dict[str, str]) -> str:
    if name.upper() in ("GND", "0"):
        return "0"
    s = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "n"
    if s[0].isdigit():
        s = "n" + s
    base, k = s, 1
    while s in used and used[s] != name:
        k += 1
        s = f"{base}_{k}"
    used[s] = name
    return s


def _wave(device: str, props: dict[str, str]) -> tuple[dict[str, Any] | None, Any]:
    """ISIS generator -> (wave dict | None, DC value)."""
    g = {k.upper(): v for k, v in props.items()}
    if device == "$DCGEN":
        return None, g.get("VALUE", "0")
    if device == "$SINEGEN":
        return {"type": "sine", "amp": g.get("AMP", "1"), "offset": g.get("OFFSET", "0"), "freq": g.get("FREQ", "1k"),
                "phase": g.get("PHASE", "0"), "damping": g.get("THETA", "0")}, 0
    if device == "$PULSEGEN":
        return {"type": "pulse", "low": g.get("V1", "0"), "high": g.get("V2", "1"), "delay": g.get("TD", "0"), "rise": g.get("TR", "1u"),
                "fall": g.get("TF", "1u"), "width": g.get("PW", "50%"), "period": g.get("PER", "1m")}, 0
    if device == "$PWLINGEN":
        pts: list[list[float]] = []
        for k, v in g.items():
            m = re.fullmatch(r"V\(([-0-9.eE+]+)\)", k)
            if m:
                pts.append([float(m.group(1)), dsn_devices.isis_number(v) or 0.0])
        return {"type": "pwl", "points": sorted(pts)}, 0
    return None, None


def project_to_spec(path: str | PathLike[str], analysis: dict[str, Any] | None = None, probes: list[str] | None = None,
                    variables: dict[str, str] | None = None) -> ProjectSpec:
    z = zipfile.ZipFile(path)
    dsn = parse_dsn(z.read("ROOT.DSN"))
    if not dsn.ok:
        raise ValueError(f"cannot read the project's object stream: {dsn.error}")
    cdb = z.read("ROOT.CDB")
    nets = dsn_nets.build_nets(dsn.objects)
    triples = dsn_nets.named_pins(nets, dsn.objects, cdb)
    used: dict[str, str] = {}
    safe_triples = [(ref, pin, _safe(net, used)) for ref, pin, net in triples]
    sr = dsn_devices.spec_from_cdb(cdb, safe_triples, variables=variables)
    parts = list(sr.parts)
    notes: list[str] = []
    refs = {p["ref"] for p in parts}
    vprobes: list[str] = []
    iprobes: list[str] = []
    for oi, o in enumerate(dsn.objects):
        if o.get("kind") != "PROBE":
            continue
        dev = o.get("device", "")
        net = nets.net_of((oi, 0))
        label = (o.get("label") or {}).get("text") or f"obj{oi}"
        if net is None:
            continue
        node = _safe(nets.name(net), used)
        if dev == "$VPROBE":
            vprobes.append(node)
        elif dev.endswith("GEN") and dev != "$DIGGEN":
            wave, dc = _wave(dev, dsn_devices.parse_props((o.get("props") or {}).get("text", "")))
            ref = "V" + re.sub(r"[^A-Za-z0-9]", "", label)
            while ref in refs:
                ref += "x"
            refs.add(ref)
            part: dict[str, Any] = {"ref": ref, "kind": "V", "nodes": [node, "0"]}
            if wave:
                part["wave"] = wave
            else:
                part["value"] = dc
            if not any(p.get("ac") for p in parts):
                part["ac"] = True
            parts.append(part)
        elif dev == "$IPROBE":
            other = nets.net_of((oi, 1))
            if other is None:
                notes.append(f"current probe {label} has an unconnected side: ignored")
                continue
            ref = "VI" + re.sub(r"[^A-Za-z0-9]", "", label)          # a 0 V source in series measures the same current
            while ref in refs:
                ref += "x"
            refs.add(ref)
            parts.append({"ref": ref, "kind": "V", "nodes": [node, _safe(nets.name(other), used)], "value": 0})
            iprobes.append(f"i({ref})")
        else:
            notes.append(f"{dev} ({label}) ignored")
    spec: dict[str, Any] = {"title": str(path).rsplit("/", 1)[-1], "parts": parts, "analysis": analysis or {"type": "op"}}
    want = probes or sorted({f"v({n})" for n in vprobes if n != "0"}) + iprobes
    if want:
        spec["probes"] = want
    return ProjectSpec(spec=spec, ignored=sr.ignored, unsupported=sr.unsupported, notes=notes, nets=used)


def simulate_project_file(path: str | PathLike[str], analysis: dict[str, Any] | None = None, name: str = "project", probes: list[str] | None = None,
                          skip_unsupported: bool = False) -> tuple[SimResult, ProjectSpec]:
    from . import sim
    ps = project_to_spec(path, analysis, probes)
    if ps.unsupported and not skip_unsupported:
        raise ValueError("unsupported components: " + "; ".join(f"{k}: {v}" for k, v in list(ps.unsupported.items())[:8]))
    return sim.simulate(ps.spec, name), ps
