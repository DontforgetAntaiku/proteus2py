"""JSON-like circuit spec -> SDF -> PROSPICE -> decoded numbers.

spec = {"parts":[{"ref":"R1","kind":"R","nodes":["in","out"],"value":"1k"}, ...],
        "models":{"MYDIODE":{"type":"D","params":{"IS":"1n"}}},   # optional; library models are found by name
        "analysis":{"type":"transient","tstop":"5m"} | {"type":"ac","fstart":10,"fstop":"1M","points":10,"mode":"dec"} |
                   {"type":"dc","source":"V1","start":0,"stop":5,"step":0.1} | {"type":"op"},
        "probes":["v(out)","i(R1)"]}      # default: every node voltage
Part kinds: R C L | V I (value = DC, wave={...}) | D Q QP J JP M MP (model) | K (inductors) | E G F H | X (SPICE subcircuit) | OPAMP (ideal).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import library, pdf, prospice
from . import workspace as ws
from .sdf import Sdf, num, spice_number

SINE, PULSE, EXP, SFFM, PWL = "sine", "pulse", "exp", "sffm", "pwl"

# Wave parameters: user key -> ISIS property (verified by experiment against PROSPICE, see lessons):
# sine AMP OFFSET FREQ TD PHASE THETA | pulse LOW HIGH TD TR TF PW(time or %) FREQ|PER | exp LOW HIGH TD1 TAU1 TD2 TAU2 |
# sffm AMP OFFSET CFREQ MDI SFREQ. (DELAY/TRISE/WIDTH style names are silently ignored by PROSPICE for pulses!)
WAVES = {
    SINE: ("SINE", {"amp": "AMP", "amplitude": "AMP", "offset": "OFFSET", "freq": "FREQ", "delay": "TD", "td": "TD",
                    "phase": "PHASE", "damping": "THETA", "theta": "THETA"}),
    PULSE: ("PULSE", {"low": "LOW", "high": "HIGH", "delay": "TD", "td": "TD", "rise": "TR", "tr": "TR", "fall": "TF",
                      "tf": "TF", "width": "PW", "pw": "PW", "freq": "FREQ", "period": "PER", "per": "PER"}),
    EXP: ("EXP", {"low": "LOW", "high": "HIGH", "td1": "TD1", "tau1": "TAU1", "td2": "TD2", "tau2": "TAU2"}),
    SFFM: ("SFFM", {"amp": "AMP", "offset": "OFFSET", "carrier": "CFREQ", "cfreq": "CFREQ", "index": "MDI", "mdi": "MDI",
                    "signal": "SFREQ", "sfreq": "SFREQ"}),
}

BUILTIN_MODELS = {   # generic parts (parameters from the LX_* models Proteus ships); name -> (type, params)
    "NPN": ("NPN", {"IS": "1E-14", "BF": "200", "BR": "3", "RC": "5", "RB": "50", "CJC": "5p", "CJE": "8p", "VAF": "70"}),
    "PNP": ("PNP", {"IS": "1E-14", "BF": "120", "BR": "1.5", "RC": "5", "RB": "50", "CJC": "10p", "CJE": "30p", "VAF": "100"}),
    "DIODE": ("D", {"IS": "1E-14"}),
}
TYPE_KIND = {"NPN": "Q", "PNP": "QP", "D": "D", "NJF": "J", "PJF": "JP", "NMOS": "M", "PMOS": "M"}


class SpecError(ValueError):
    pass


def _pairs(d: dict) -> list[str]:
    return [f"{k.upper()}={num(v)}" for k, v in d.items()]


@dataclass
class Built:
    sdf: Sdf
    probes: list[tuple[str, str]]           # (probe display name, human label e.g. "v(out)")
    analysis: str
    meta: dict = field(default_factory=dict)


def _wave_props(w: dict) -> tuple[str, list[str]]:
    t = w["type"].lower()
    if t == PWL:
        pts = w["points"]                                    # [[t, v], ...]
        return "PWLIN", [f"V({num(tt)})={num(v)}" for tt, v in pts]
    if t not in WAVES:
        raise SpecError(f"wave type {t!r}: one of {sorted([*WAVES, PWL])}")
    suffix, keys = WAVES[t]
    unknown = {k for k in w if k.lower() not in keys and k != "type"}
    if unknown:
        raise SpecError(f"wave {t}: unknown keys {sorted(unknown)}; allowed {sorted(keys)}")
    return suffix, [f"{keys[k.lower()]}={num(v)}" for k, v in w.items() if k != "type"]


def _numeric(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        try:
            out[k.upper()] = spice_number(v)
        except ValueError:
            out[k.upper()] = v                    # flags / strings (e.g. LEVEL=1 is fine, "OFF" stays)
    return out


def _resolve_model(spec: dict, name: str) -> tuple[str, dict]:
    """Model parameters are SPICE notation (m = milli) and are written to the netlist as plain numbers."""
    m = (spec.get("models") or {}).get(name)
    if m:
        p = m.get("params", {})
        return m["type"].upper(), _numeric(dict(p) if isinstance(p, dict) else dict(re.findall(r"(\w+)\s*=\s*([^\s,]+)", p)))
    if name.upper() in BUILTIN_MODELS:
        t, p = BUILTIN_MODELS[name.upper()]
        return t, _numeric(p)
    lm = library.resolve(name)
    if lm:
        return lm[0], _numeric(lm[1])
    raise SpecError(f"model {name!r} not found: define it in spec['models'] or use a Proteus library name (see search_models)")


def _couple(spec: dict) -> dict:
    """K (mutual inductance) parts -> one SPICE subcircuit per group of coupled inductors (ProSPICE parses K natively inside subcircuits)."""
    ks = [p for p in spec["parts"] if p["kind"].upper() == "K"]
    if not ks:
        return spec
    byref = {p["ref"]: p for p in spec["parts"]}
    parent: dict[str, str] = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for k in ks:
        ls = k.get("inductors") or []
        if len(ls) != 2 or any(x not in byref or byref[x]["kind"].upper() != "L" for x in ls):
            raise SpecError(f'{k["ref"]}: K needs "inductors": [ref1, ref2] naming two L parts')
        parent[find(ls[0])] = find(ls[1])
    groups: dict[str, list[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    out = dict(spec)
    out["parts"] = [p for p in spec["parts"] if p["kind"].upper() != "K" and p["ref"] not in parent]
    out["subckts"] = dict(spec.get("subckts") or {})
    order = {p['ref']: i for i, p in enumerate(spec['parts'])}
    for gi, members in enumerate(sorted((sorted(g, key=order.get) for g in groups.values()), key=lambda g: order[g[0]])):
        name = f"KGRP{gi + 1}"
        nodes = [n for m in members for n in byref[m]["nodes"]]
        lines = []
        for i, m in enumerate(members):
            ic = f" IC={num(byref[m]['ic'])}" if "ic" in byref[m] else ""
            lines.append(f"L{m} {2 * i + 1} {2 * i + 2} {_f(byref[m]['value'])!r}{ic}")
        for k in ks:
            if k["inductors"][0] in members:
                lines.append(f"K{k['ref']} L{k['inductors'][0]} L{k['inductors'][1]} {_f(k['value'])!r}")
        out["subckts"][name] = f".SUBCKT {name} " + " ".join(str(i + 1) for i in range(len(nodes))) + "\n" + "\n".join(lines) + "\n.ENDS"
        out["parts"].append({"ref": f"X{name}", "kind": "X", "nodes": nodes, "subckt": name})
    return out


def build(spec: dict) -> Built:
    spec = _couple(spec)
    s = Sdf(title=spec.get("title", "proteus2py"))
    s.props["TEMP"] = num(spec.get("temp", 27))
    for k, v in (spec.get("options") or {}).items():
        s.props[k.upper()] = num(v)
    parts = spec.get("parts") or []
    if not parts:
        raise SpecError("no parts")
    refs = {p["ref"] for p in parts}
    if len(refs) != len(parts):
        raise SpecError("duplicate refs")
    nodes_seen: list[str] = []
    ac_ref = None
    models_used: dict[str, str] = {}
    libs: dict[str, str] = {}

    def see(n):
        n = "GND" if str(n).lower() in ("0", "gnd") else str(n)
        if n not in nodes_seen and n != "GND":
            nodes_seen.append(n)
        return n

    for p in parts:
        ref, kind = p["ref"], p["kind"].upper()
        nodes = [see(n) for n in p.get("nodes", [])]
        val = p.get("value", "")
        if kind in ("R", "C", "L"):
            lib, prim, ptype, pins = {"R": ("RES", "PASSIVE", None, ("1", "2")), "C": ("CAP", "PASSIVE", "CAPACITOR", ("1", "2")),
                                      "L": ("IND", "PASSIVE", "INDUCTOR", ("1", "2"))}[kind]
            if len(nodes) != 2:
                raise SpecError(f"{ref}: needs 2 nodes")
            props = [f"PRIMITIVE={prim}"] + ([f"PRIMTYPE={ptype}"] if ptype else [])
            if "ic" in p:
                props.append(f"IC={num(p['ic'])}")
            s.add_part(ref, lib, num(val), props, [(n, "PS", pn) for n, pn in zip(nodes, pins)])
        elif kind in ("V", "I"):
            if len(nodes) != 2:
                raise SpecError(f"{ref}: needs 2 nodes [plus, minus]")
            w = p.get("wave")
            if p.get("ac") and not w and kind == "V":
                w = {"type": "sine", "amp": p.get("acmag", 1), "offset": val or 0, "freq": 1000}      # ProSPICE wants a sine as AC reference
            if w:
                suffix, props = _wave_props(w)
                prim = ("V" if kind == "V" else "I") + suffix
            else:
                prim = "VSOURCE" if kind == "V" else "ISOURCE"
                props = []
            if p.get("ac") and ac_ref is None:
                ac_ref = ref
            # SPICE current sources push current from n+ through the source to n-. ISIS's DC ISOURCE pushes it INTO '+' (swap), but ISINE/IPULSE/.. follow SPICE
            pp, pm = (nodes[1], nodes[0]) if (kind == "I" and not w) else (nodes[0], nodes[1])    # only the DC ISOURCE is reversed
            s.add_part(ref, prim, num(val) if not w else "", [*props, "PRIMITIVE=PASSIVE"], [(pp, "PS", "+"), (pm, "PS", "-")])
        elif kind in ("D", "Q", "QP", "J", "JP", "M", "MP"):
            mname = p.get("model") or str(val)
            if not mname:
                raise SpecError(f"{ref}: needs 'model'")
            mtype, mparams = _resolve_model(spec, mname)
            want = {"D": "D", "Q": "NPN", "QP": "PNP", "J": "NJF", "JP": "PJF", "M": "NMOS", "MP": "PMOS"}[kind]
            if mtype != want and not (kind in ("M", "MP") and mtype in ("NMOS", "PMOS")):
                raise SpecError(f"{ref}: model {mname} is {mtype}, part kind {kind} expects {want}")
            pins = {"D": ("A", "K"), "Q": ("C", "B", "E"), "QP": ("C", "B", "E"), "J": ("D", "G", "S"), "JP": ("D", "G", "S"),
                    "M": ("D", "G", "S", "B"), "MP": ("D", "G", "S", "B")}[kind]
            if len(nodes) != len(pins):
                raise SpecError(f"{ref}: {kind} needs {len(pins)} nodes {pins}")
            mid = "M_" + re.sub(r"\W", "_", mname)
            models_used[mid] = ",".join(f"{k}={num(v)}" for k, v in mparams.items()) or "IS=1e-14"
            lib = {"D": "DIODE", "Q": "NPN", "QP": "PNP", "J": "NJF", "JP": "PJF", "M": "NMOS", "MP": "PMOS"}[kind]
            s.add_part(ref, lib, mname, [f"MODEL={mid}", "PRIMITIVE=ACTIVE", *_pairs(p.get("params", {}))],
                       [(n, "PS", pn) for n, pn in zip(nodes, pins)])
        elif kind in ("E", "G"):
            if len(nodes) != 4:
                raise SpecError(f"{ref}: {kind} needs 4 nodes [out+, out-, ctrl+, ctrl-]")
            if kind == "G":
                # SPICE: current flows n+ -> source -> n-. ISIS VCISOURCE pushes current INTO its '+' pin, so swap the pins.
                s.add_part(ref, "VCISOURCE", num(val), ["PRIMITIVE=PASSIVE"],
                           [(nodes[1], "PS", "+"), (nodes[0], "PS", "-"), (nodes[2], "PS", "P"), (nodes[3], "PS", "N")])
            else:
                # no VCVS primitive in PROSPICE: VCCS of K/Rs into a 1 mohm resistor = ideal source with 1 mohm output resistance
                rs = 1e-3
                s.add_part(f"{ref}_G", "VCISOURCE", num(_f(val) / rs), ["PRIMITIVE=PASSIVE"],
                           [(nodes[0], "PS", "+"), (nodes[1], "PS", "-"), (nodes[2], "PS", "P"), (nodes[3], "PS", "N")])
                s.add_part(f"{ref}_R", "RES", num(rs), ["PRIMITIVE=PASSIVE"], [(nodes[0], "PS", "1"), (nodes[1], "PS", "2")])
        elif kind in ("F", "H"):
            if len(nodes) != 2 or not p.get("control"):
                raise SpecError(f'{ref}: {kind} needs 2 nodes and "control": ref of the voltage source whose current controls it')
            if p["control"] not in refs:
                raise SpecError(f"{ref}: control source {p['control']!r} not found")
            s.add_part(ref, "CCCS" if kind == "F" else "CCVS", num(val), [f"PROBE={p['control']}", "PRIMITIVE=ANALOGUE"],
                       [(nodes[0], "PS", "+"), (nodes[1], "PS", "-")])
        elif kind == "X":
            sub = p.get("subckt") or p.get("model") or str(val)
            text = (spec.get("subckts") or {}).get(sub) or library.get(sub)
            if not text:
                raise SpecError(f"{ref}: subcircuit {sub!r} not in spec['subckts'] and not in the Proteus libraries")
            if not nodes:
                raise SpecError(f"{ref}: X needs nodes in subcircuit port order")
            libs.setdefault(sub, text)
            pins = [f"P{i + 1}" for i in range(len(nodes))]
            s.add_part(ref, "SUBCKT", sub, [f'SPICEMODEL="{sub},@LIB@{sub}"', f'SPICEPINS="{",".join(pins)}"', "PRIMITIVE=ANALOGUE"],
                       [(n, "PS", pn) for n, pn in zip(nodes, pins)])
        elif kind == "OPAMP":
            if len(nodes) != 3:
                raise SpecError(f"{ref}: OPAMP needs [in+, in-, out]")
            a = _f(p.get("gain", 1e6))
            mid = see(f"{ref}_int")
            s.add_part(f"{ref}_G", "VCISOURCE", num(a), ["PRIMITIVE=PASSIVE"],
                       [(mid, "PS", "+"), ("GND", "PS", "-"), (nodes[0], "PS", "P"), (nodes[1], "PS", "N")])
            s.add_part(f"{ref}_RG", "RES", "1", ["PRIMITIVE=PASSIVE"], [(mid, "PS", "1"), ("GND", "PS", "2")])
            s.add_part(f"{ref}_RO", "RES", num(p.get("rout", 1)), ["PRIMITIVE=PASSIVE"], [(mid, "PS", "1"), (nodes[2], "PS", "2")])
        else:
            raise SpecError(f"{ref}: unknown kind {kind!r}")
    s.models.update(models_used)
    if libs:
        extra = spec.get("spice_text", "")
        for sub, text in libs.items():
            fn = f"{s.partition}_{re.sub(r'[^A-Za-z0-9_]', '_', sub)}.LIB"
            body = "\n".join([text.replace("\x00", ""), *( [extra] if extra else [] ),
                              *(t.replace("\x00", "") for k, t in (spec.get("subckts") or {}).items() if k != sub)])
            (ws.exchange() / fn).write_text(body + "\n")
            for part in s.parts:
                part.props = [x.replace(f"@LIB@{sub}", "C:\\exchange\\" + fn) for x in part.props]

    # ---- analysis
    an = (spec.get("analysis") or {"type": "op"})
    t = an["type"].lower()
    if t in ("op", "transient", "tran"):
        s.props.update(SIMTYPE="TRANSIENT", TSTART=num(an.get("tstart", 0)),
                       TSTOP=num(an.get("tstop", 1e-9) if t != "op" else 1e-9))
        if an.get("tmax"):
            s.props["TMAX"] = num(an["tmax"])
        elif t != "op":
            s.props["TMAX"] = num(float(_f(an.get("tstop", 1e-3))) / 1000)
        t = "op" if t == "op" else "transient"
    elif t == "ac":
        if ac_ref is None:
            for p in parts:
                if p["kind"].upper() == "V":
                    ac_ref = p["ref"]
                    break
        if ac_ref is None:
            raise SpecError("ac analysis needs a voltage source (mark one with \"ac\": true)")
        mode = {"dec": "DEC", "oct": "OCT", "lin": "LIN"}[an.get("mode", "dec").lower()]
        s.props.update(SIMTYPE="FREQUENCY", FSTART=num(an["fstart"]), FSTOP=num(an["fstop"]), NUMSTEPS=num(an.get("points", 10)),
                       FREQREF=ac_ref)
        s.props[mode] = "TRUE"
    elif t == "noise":
        src = an.get("input") or ac_ref
        if src is None:
            src = next((q["ref"] for q in parts if q["kind"].upper() == "V"), None)
        if src is None or "output" not in an:
            raise SpecError('noise analysis needs "output" (node) and a voltage source "input" (ref)')
        mode = {"dec": "DEC", "oct": "OCT", "lin": "LIN"}[an.get("mode", "dec").lower()]
        s.props.update(SIMTYPE="NOISE", FSTART=num(an["fstart"]), FSTOP=num(an["fstop"]), NUMSTEPS=num(an.get("points", 10)),
                       INPUT=src, LOGSPECTRAL="FALSE", LOGTOTAL="TRUE", FREQREF=src)
        s.props[mode] = "TRUE"
        spec = dict(spec)
        spec["probes"] = [f"v({an['output']})"]
    elif t == "dc":
        s.props.update(ANALYSIS="DC", NAME1=an["source"], START1=num(an["start"]), STOP1=num(an["stop"]), STEP1=num(an["step"]))
        if an.get("source2"):
            s.props.update(NAME2=an["source2"], START2=num(an["start2"]), STOP2=num(an["stop2"]), STEP2=num(an["step2"]))
    else:
        raise SpecError(f"analysis {t!r} not supported")

    # ---- probes
    want = spec.get("probes") or [f"v({n})" for n in nodes_seen]
    probes = []
    for i, w in enumerate(want):
        m = re.fullmatch(r"\s*([vi])\(\s*([^)]+?)\s*\)\s*", w, re.I)
        if not m:
            raise SpecError(f"probe {w!r}: use v(node) or i(ref)")
        kind, what = m.group(1).lower(), m.group(2)
        pid = f"P{i:04d}"
        if kind == "v":
            n = see(what)
            if n not in s.nets and n != "GND":
                raise SpecError(f"probe {w}: unknown node {what!r}; nodes: {nodes_seen}")
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
    return Built(s, probes, t, {"nodes": nodes_seen, "ac_ref": ac_ref})


def _f(x) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    m = re.fullmatch(r"\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*", str(x))
    if not m:
        raise SpecError(f"bad number {x!r}")
    mult = {"": 1, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "meg": 1e6, "G": 1e9, "g": 1e9, "T": 1e12}
    return float(m.group(1)) * mult[m.group(2)]


LADDER = [{}, {"ITL1": 1000}, {"ITL1": 1000, "RELTOL": "1e-3", "GMINSTEPS": 100, "SRCSTEPS": 100},
          {"ITL1": 2000, "ITL2": 500, "GMIN": "1e-9", "RELTOL": "1e-3", "GMINSTEPS": 200, "SRCSTEPS": 200}]


def run(spec: dict, name: str = "sim", timeout: int = 180) -> tuple[Built, pdf.Result, dict]:
    """Build, simulate, decode. Convergence failures are retried with progressively looser solver options."""
    errors: list[str] = []
    for step in LADDER:
        sp = dict(spec)
        sp["options"] = {**step, **(spec.get("options") or {})}
        b = build(sp)
        fn = f"{ws.name_ok(name)}.SDF"
        (ws.exchange() / fn).write_text(b.sdf.text())
        for old in (ws.exchange() / "res").glob(f"{b.sdf.partition}.PDF"):
            old.unlink()
        r = prospice.run(fn, timeout=timeout)
        f = ws.exchange() / "res" / f"{b.sdf.partition}.PDF"
        bad = [x for x in r["console"] if x.startswith(("#W:", "#F:", "#X:")) and ("converge" in x or "failed to start" in x or "failed to complete" in x)]
        if f.exists() and not bad:
            r["options_used"] = sp["options"]
            return b, pdf.load(f), r
        errors = r["console"]
    raise RuntimeError("PROSPICE failed (also after relaxing solver options): " + " | ".join(x for x in errors if x[:3] in ("#W:", "#F:", "#X:")))
