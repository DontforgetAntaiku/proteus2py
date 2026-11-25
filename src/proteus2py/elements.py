"""Circuit element registry: one small function per element kind translates a `Part` into SDF parts.

Add a kind: write a handler `(ctx, part) -> None` and decorate it with `@element("KIND", ...)`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import library
from . import workspace as ws
from .errors import SpecError
from .models import CircuitSpec, Part
from .sdf import Sdf, num, spice_number
from .units import to_float

# Wave parameters: user key -> ISIS property (verified against ProSPICE; DELAY/TRISE/WIDTH-style names are silently ignored for pulses!)
WAVES: dict[str, tuple[str, dict[str, str]]] = {
    "sine": ("SINE", {"amp": "AMP", "amplitude": "AMP", "offset": "OFFSET", "freq": "FREQ", "delay": "TD", "td": "TD",
                      "phase": "PHASE", "damping": "THETA", "theta": "THETA"}),
    "pulse": ("PULSE", {"low": "LOW", "high": "HIGH", "delay": "TD", "td": "TD", "rise": "TR", "tr": "TR", "fall": "TF",
                        "tf": "TF", "width": "PW", "pw": "PW", "freq": "FREQ", "period": "PER", "per": "PER"}),
    "exp": ("EXP", {"low": "LOW", "high": "HIGH", "td1": "TD1", "tau1": "TAU1", "td2": "TD2", "tau2": "TAU2"}),
    "sffm": ("SFFM", {"amp": "AMP", "offset": "OFFSET", "carrier": "CFREQ", "cfreq": "CFREQ", "index": "MDI", "mdi": "MDI",
                      "signal": "SFREQ", "sfreq": "SFREQ"}),
}

BUILTIN_MODELS: dict[str, tuple[str, dict[str, str]]] = {   # generic parts (values from the LX_* models Proteus ships)
    "NPN": ("NPN", {"IS": "1E-14", "BF": "200", "BR": "3", "RC": "5", "RB": "50", "CJC": "5p", "CJE": "8p", "VAF": "70"}),
    "PNP": ("PNP", {"IS": "1E-14", "BF": "120", "BR": "1.5", "RC": "5", "RB": "50", "CJC": "10p", "CJE": "30p", "VAF": "100"}),
    "DIODE": ("D", {"IS": "1E-14"}),
}


@dataclass
class Ctx:
    """State shared by element handlers while one circuit is translated."""
    spec: CircuitSpec
    sdf: Sdf
    refs: set[str]
    nodes_seen: list[str] = field(default_factory=list)
    models_used: dict[str, str] = field(default_factory=dict)
    libs: dict[str, str] = field(default_factory=dict)
    ac_ref: str | None = None

    def see(self, n: str) -> str:
        n = "GND" if str(n).lower() in ("0", "gnd") else str(n)
        if n not in self.nodes_seen and n != "GND":
            self.nodes_seen.append(n)
        return n


Handler = Callable[[Ctx, Part], None]
REGISTRY: dict[str, Handler] = {}


def element(*kinds: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        for k in kinds:
            REGISTRY[k] = fn
        return fn
    return deco


def _numeric(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in params.items():
        try:
            out[k.upper()] = spice_number(v)
        except ValueError:
            out[k.upper()] = v                    # flags / strings stay as they are
    return out


def resolve_model(spec: CircuitSpec, name: str) -> tuple[str, dict[str, Any]]:
    """Model parameters are SPICE notation (m = milli) and are written to the netlist as plain numbers."""
    m = spec.models.get(name)
    if m:
        p = m.params if isinstance(m.params, dict) else dict(re.findall(r"(\w+)\s*=\s*([^\s,]+)", m.params))
        return m.type.upper(), _numeric(dict(p))
    if name.upper() in BUILTIN_MODELS:
        t, p = BUILTIN_MODELS[name.upper()]
        return t, _numeric(dict(p))
    lm = library.resolve(name)
    if lm:
        return lm[0], _numeric(dict(lm[1]))
    raise SpecError(f"model {name!r} not found: define it in spec.models or use a Proteus library name (see search_models)")


def wave_props(w: dict[str, Any]) -> tuple[str, list[str]]:
    t = w["type"]
    if t == "pwl":
        return "PWLIN", [f"V({num(tt)})={num(v)}" for tt, v in (w.get("points") or [])]
    suffix, keys = WAVES[t]
    unknown = {k for k in w if k.lower() not in keys and k not in ("type", "points")}
    if unknown:
        raise SpecError(f"wave {t}: unknown keys {sorted(unknown)}; allowed {sorted(keys)}")
    return suffix, [f"{keys[k.lower()]}={num(v)}" for k, v in w.items() if k not in ("type", "points") and v is not None]


def _need(p: Part, n: int, what: str = "") -> list[str]:
    if len(p.nodes) != n:
        raise SpecError(f"{p.ref}: {p.kind} needs {n} nodes {what}".rstrip())
    return p.nodes


@element("R", "C", "L")
def _rcl(ctx: Ctx, p: Part) -> None:
    lib, ptype = {"R": ("RES", None), "C": ("CAP", "CAPACITOR"), "L": ("IND", "INDUCTOR")}[p.kind]
    nodes = [ctx.see(n) for n in _need(p, 2)]
    props = ["PRIMITIVE=PASSIVE"] + ([f"PRIMTYPE={ptype}"] if ptype else [])
    if p.ic is not None:
        props.append(f"IC={num(p.ic)}")
    ctx.sdf.add_part(p.ref, lib, num(p.value if p.value is not None else ""), props, [(n, "PS", pn) for n, pn in zip(nodes, ("1", "2"))])


@element("V", "I")
def _source(ctx: Ctx, p: Part) -> None:
    nodes = [ctx.see(n) for n in _need(p, 2, "[plus, minus]")]
    wave = p.wave.model_dump(exclude_none=True) if p.wave else None
    if p.ac and wave is None and p.kind == "V":
        wave = {"type": "sine", "amp": p.acmag, "offset": p.value or 0, "freq": 1000}        # ProSPICE wants a sine as AC reference
    if wave:
        suffix, props = wave_props(wave)
        prim = p.kind + suffix
    else:
        prim, props = ("VSOURCE" if p.kind == "V" else "ISOURCE"), []
    if p.ac and ctx.ac_ref is None:
        ctx.ac_ref = p.ref
    # only the DC ISOURCE pushes current INTO its '+' pin; ISINE/IPULSE/... and all SPICE sources go from n+ through the source to n-
    pp, pm = (nodes[1], nodes[0]) if (p.kind == "I" and not wave) else (nodes[0], nodes[1])
    ctx.sdf.add_part(p.ref, prim, "" if wave else num(p.value if p.value is not None else ""), [*props, "PRIMITIVE=PASSIVE"],
                     [(pp, "PS", "+"), (pm, "PS", "-")])


_DEVICE = {"D": ("DIODE", "D", ("A", "K")), "Q": ("NPN", "NPN", ("C", "B", "E")), "QP": ("PNP", "PNP", ("C", "B", "E")),
           "J": ("NJF", "NJF", ("D", "G", "S")), "JP": ("PJF", "PJF", ("D", "G", "S")),
           "M": ("NMOS", "NMOS", ("D", "G", "S", "B")), "MP": ("PMOS", "PMOS", ("D", "G", "S", "B"))}


@element("D", "Q", "QP", "J", "JP", "M", "MP")
def _semiconductor(ctx: Ctx, p: Part) -> None:
    lib, want, pins = _DEVICE[p.kind]
    mname = p.model or (str(p.value) if p.value is not None else "")
    if not mname:
        raise SpecError(f"{p.ref}: needs 'model'")
    mtype, mparams = resolve_model(ctx.spec, mname)
    if mtype != want and not (p.kind in ("M", "MP") and mtype in ("NMOS", "PMOS")):
        raise SpecError(f"{p.ref}: model {mname} is {mtype}, part kind {p.kind} expects {want}")
    nodes = [ctx.see(n) for n in _need(p, len(pins), str(pins))]
    mid = "M_" + re.sub(r"\W", "_", mname)
    ctx.models_used[mid] = ",".join(f"{k}={num(v)}" for k, v in mparams.items()) or "IS=1e-14"
    extra = [f"{k.upper()}={num(v)}" for k, v in p.params.items()]
    ctx.sdf.add_part(p.ref, lib, mname, [f"MODEL={mid}", "PRIMITIVE=ACTIVE", *extra], [(n, "PS", pn) for n, pn in zip(nodes, pins)])


@element("E", "G")
def _controlled(ctx: Ctx, p: Part) -> None:
    n = [ctx.see(x) for x in _need(p, 4, "[out+, out-, ctrl+, ctrl-]")]
    if p.kind == "G":
        # SPICE: current flows n+ -> source -> n-. ISIS VCISOURCE pushes current INTO its '+' pin, so swap the pins.
        ctx.sdf.add_part(p.ref, "VCISOURCE", num(p.value), ["PRIMITIVE=PASSIVE"], [(n[1], "PS", "+"), (n[0], "PS", "-"), (n[2], "PS", "P"), (n[3], "PS", "N")])
        return
    rs = 1e-3                                   # no VCVS primitive: VCCS of K/Rs into 1 mohm = ideal source with 1 mohm output resistance
    ctx.sdf.add_part(f"{p.ref}_G", "VCISOURCE", num(to_float(p.value) / rs), ["PRIMITIVE=PASSIVE"],
                     [(n[0], "PS", "+"), (n[1], "PS", "-"), (n[2], "PS", "P"), (n[3], "PS", "N")])
    ctx.sdf.add_part(f"{p.ref}_R", "RES", num(rs), ["PRIMITIVE=PASSIVE"], [(n[0], "PS", "1"), (n[1], "PS", "2")])


@element("F", "H")
def _current_controlled(ctx: Ctx, p: Part) -> None:
    n = [ctx.see(x) for x in _need(p, 2)]
    if not p.control:
        raise SpecError(f'{p.ref}: {p.kind} needs "control": ref of the voltage source whose current controls it')
    if p.control not in ctx.refs:
        raise SpecError(f"{p.ref}: control source {p.control!r} not found")
    ctx.sdf.add_part(p.ref, "CCCS" if p.kind == "F" else "CCVS", num(p.value), [f"PROBE={p.control}", "PRIMITIVE=ANALOGUE"],
                     [(n[0], "PS", "+"), (n[1], "PS", "-")])


@element("X")
def _subckt(ctx: Ctx, p: Part) -> None:
    sub = p.subckt or p.model or (str(p.value) if p.value is not None else "")
    text = ctx.spec.subckts.get(sub) or library.get(sub)
    if not text:
        raise SpecError(f"{p.ref}: subcircuit {sub!r} not in spec.subckts and not in the Proteus libraries")
    if not p.nodes:
        raise SpecError(f"{p.ref}: X needs nodes in subcircuit port order")
    nodes = [ctx.see(n) for n in p.nodes]
    ctx.libs.setdefault(sub, text)
    pins = [f"P{i + 1}" for i in range(len(nodes))]
    ctx.sdf.add_part(p.ref, "SUBCKT", sub, [f'SPICEMODEL="{sub},@LIB@{sub}"', f'SPICEPINS="{",".join(pins)}"', "PRIMITIVE=ANALOGUE"],
                     [(n, "PS", pn) for n, pn in zip(nodes, pins)])


@element("OPAMP")
def _opamp(ctx: Ctx, p: Part) -> None:
    nodes = [ctx.see(n) for n in _need(p, 3, "[in+, in-, out]")]
    mid = ctx.see(f"{p.ref}_int")
    ctx.sdf.add_part(f"{p.ref}_G", "VCISOURCE", num(to_float(p.gain if p.gain is not None else 1e6)), ["PRIMITIVE=PASSIVE"],
                     [(mid, "PS", "+"), ("GND", "PS", "-"), (nodes[0], "PS", "P"), (nodes[1], "PS", "N")])
    ctx.sdf.add_part(f"{p.ref}_RG", "RES", "1", ["PRIMITIVE=PASSIVE"], [(mid, "PS", "1"), ("GND", "PS", "2")])
    ctx.sdf.add_part(f"{p.ref}_RO", "RES", num(to_float(p.rout if p.rout is not None else 1)), ["PRIMITIVE=PASSIVE"], [(mid, "PS", "1"), (nodes[2], "PS", "2")])


_GATES = {"AND", "NAND", "OR", "NOR", "XOR", "XNOR"}


@element("GATE")
def _gate(ctx: Ctx, p: Part) -> None:
    g = (p.gate or p.model or "").upper()
    if g not in _GATES | {"NOT", "BUF"}:
        raise SpecError(f"{p.ref}: gate must be one of {sorted(_GATES | {'NOT', 'BUF'})}")
    if len(p.nodes) < 2:
        raise SpecError(f"{p.ref}: GATE needs inputs then the output in `nodes`")
    nodes = [ctx.see(n) for n in p.nodes]
    n_in = len(nodes) - 1
    lib = {"NOT": "INVERTER", "BUF": "BUFFER"}.get(g) or f"{g}_{n_in}"
    pins = [("IP", "D")] if g in ("NOT", "BUF") else [("IP", f"D{i}") for i in range(n_in)]
    ctx.sdf.add_part(p.ref, lib, g, ["PRIMITIVE=DIGITAL", *[f"{k.upper()}={num(v)}" for k, v in p.delays.items()]],
                     [(n, t, pn) for n, (t, pn) in zip(nodes, [*pins, ("OP", "Q")])])


@element("DIGITAL")
def _digital(ctx: Ctx, p: Part) -> None:
    if not p.primitive or not p.pins:
        raise SpecError(f'{p.ref}: DIGITAL needs "primitive" (DSIM name) and "pins" like ["D:IP", "Q:OP"]')
    nodes = [ctx.see(n) for n in _need(p, len(p.pins))]
    spec = [x.split(":") for x in p.pins]
    ctx.sdf.add_part(p.ref, p.primitive, p.primitive, ["PRIMITIVE=DIGITAL", *[f"{k.upper()}={num(v)}" for k, v in {**p.params, **p.delays}.items()]],
                     [(n, t, pn) for n, (pn, t) in zip(nodes, spec)])


@element("DGEN")
def _digital_generator(ctx: Ctx, p: Part) -> None:
    """Digital pattern generator (a ProSPICE 'generator' part driving one net)."""
    n = ctx.see(_need(p, 1)[0])
    if not p.pattern or p.width is None:
        raise SpecError(f'{p.ref}: DGEN needs "pattern" (e.g. "0110") and "width" (time per bit)')
    ctx.sdf.add_part(p.ref, "DIGITAL!", p.ref, [f"INIT={p.init}", f"PATTERN={p.pattern}", f"WIDTH={num(p.width)}", "PRIMITIVE=PROBE"], [(n, "OP", "*")])


def write_libraries(ctx: Ctx) -> None:
    """Subcircuit texts -> .LIB files in the exchange folder (ProSPICE parses SPICE text itself, full Windows path required)."""
    sdf = ctx.sdf
    for sub, text in ctx.libs.items():
        fn = f"{sdf.partition}_{re.sub(r'[^A-Za-z0-9_]', '_', sub)}.LIB"
        others = [t.replace("\x00", "") for k, t in ctx.spec.subckts.items() if k != sub]
        body = "\n".join([text.replace("\x00", ""), *([ctx.spec.spice_text] if ctx.spec.spice_text else []), *others])
        (ws.exchange() / fn).write_text(body + "\n")
        for part in sdf.parts:
            part.props = [x.replace(f"@LIB@{sub}", ws.guest_path(fn)) for x in part.props]
