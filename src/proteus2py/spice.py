"""SPICE deck -> circuit spec (flattens .SUBCKT, evaluates .PARAM/{expr}, resolves library models/subcircuits).

Supported: R C L V I D Q J M E G X(subckt) ; V/I sources DC/AC/SIN/PULSE/EXP/SFFM/PWL ; .MODEL .SUBCKT .PARAM .TEMP .OPTIONS
.OP .TRAN .AC .DC .PRINT/.PROBE V()/I(). Not supported yet (clear error): W and i() inside B, and .INCLUDE/.LIB of host files.
Numbers are SPICE notation (m = milli, MEG = mega).
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import library
from .errors import SpiceError
from .sdf import spice_number


def _clean(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r", "").split("\n"):
        ln = re.split(r"\s;|\s\$\s", raw)[0].rstrip()
        if not ln.strip() or ln.lstrip().startswith("*"):
            continue
        if ln.lstrip().startswith("+") and lines:
            lines[-1] += " " + ln.lstrip()[1:].strip()
        else:
            lines.append(ln.strip())
    return lines


_NUM = re.compile(r"(?<![\w.])(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)(meg|mil|[tgkmunpf])?[a-zA-Z]*(?![\w.(])", re.I)
_FUNCS = {n: getattr(math, n) for n in ("sin", "cos", "tan", "exp", "log", "log10", "sqrt", "atan", "asin", "acos", "sinh", "cosh", "tanh", "pi")}
_FUNCS.update(abs=abs, min=min, max=max, pow=pow, ln=math.log, pwr=lambda a, b: abs(a) ** b)


def evaluate(expr: str, params: dict[str, float]) -> float:
    e = expr.strip().strip("{}'\"")
    try:
        return spice_number(e)
    except ValueError:
        pass
    e = e.replace("^", "**")
    e = _NUM.sub(lambda m: repr(spice_number(m.group(0))), e)
    env = {k.lower(): v for k, v in params.items()}
    env.update(_FUNCS)
    if not re.fullmatch(r"[\w\s+\-*/().,<>=!&|]*", e):
        raise SpiceError(f"unsupported expression {expr!r}")
    try:
        return float(eval(e, {"__builtins__": {}}, {**{k: v for k, v in env.items()}, **{k.upper(): v for k, v in env.items()}}))
    except Exception as ex:                                  # noqa: BLE001
        raise SpiceError(f"cannot evaluate {expr!r}: {ex}") from ex


def _split(line: str) -> list[str]:
    """Tokens; keeps (...) groups and {expr} intact, '=' glued."""
    line = re.sub(r"\s*=\s*", "=", line)
    toks, cur, depth = [], "", 0
    for ch in line:
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        if (ch.isspace() or ch == ",") and depth == 0:
            if cur:
                toks.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        toks.append(cur)
    return toks


def _kv(toks: list[str]) -> tuple[list[str], dict[str, str]]:
    pos, kv = [], {}
    for t in toks:
        if "=" in t and not t.startswith("{"):
            k, v = t.split("=", 1)
            kv[k.lower()] = v
        else:
            pos.append(t)
    return pos, kv


def _group(tok: str) -> list[str]:
    inner = tok[tok.index("(") + 1:tok.rindex(")")]
    return [x for x in re.split(r"[\s,]+", inner.strip()) if x]


class Deck:
    def __init__(self, text: str) -> None:
        self.params: dict[str, float] = {}
        self.models: dict[str, tuple[str, dict[str, str]]] = {}
        self.subckts: dict[str, tuple[list[str], dict[str, str], list[str]]] = {}
        self.subckt_names: dict[str, str] = {}
        self.model_lines: list[str] = []
        self.main: list[str] = []
        self.analyses: list[dict] = []
        self.probes: list[str] = []
        self.temp = None
        self.options: dict[str, str] = {}
        self.title = ""
        lines = _clean(text)
        if lines and not lines[0].startswith("."):
            self.title = lines.pop(0)                     # SPICE: first line is the title
        i, cur = 0, None
        while i < len(lines):
            ln = lines[i]
            low = ln.lower()
            if low.startswith(".subckt"):
                t = _split(ln)
                pos, kv = _kv(t[2:])
                cur = [t[1].lower(), pos, kv, []]
                self.subckt_names[t[1].lower()] = t[1]
            elif low.startswith(".ends"):
                self.subckts[cur[0]] = (cur[1], cur[2], cur[3])
                cur = None
            elif cur is not None:
                (cur[3] if not low.startswith(".model") else cur[3]).append(ln)
                if low.startswith(".model"):
                    self._model(ln)
            else:
                self._top(ln)
            i += 1

    def _model(self, ln: str) -> None:
        m = re.match(r"\.model\s+(\S+)\s+(\w+)\s*\(?(.*?)\)?\s*$", ln, re.I)
        if not m:
            raise SpiceError(f"bad .MODEL: {ln}")
        self.model_lines.append(ln)
        self.models[m.group(1).lower()] = (m.group(2).upper(), dict((k.upper(), v) for k, v in re.findall(r"(\w+)\s*=\s*([^\s,()]+)", m.group(3))))

    def _top(self, ln: str) -> None:
        low = ln.lower()
        t = _split(ln)
        if low.startswith(".model"):
            self._model(ln)
        elif low.startswith(".param"):
            for tok in t[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    self.params[k.lower()] = evaluate(v, self.params)
        elif low.startswith(".temp"):
            self.temp = evaluate(t[1], self.params)
        elif low.startswith(".option"):
            for tok in t[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    self.options[k] = v
        elif low.startswith(".op"):
            self.analyses.append({"type": "op"})
        elif low.startswith(".tran"):
            v = [evaluate(x, self.params) for x in t[1:] if not x.lower().startswith("uic")]
            a = {"type": "transient", "tstop": v[1] if len(v) > 1 else v[0]}
            if len(v) > 2:
                a["tstart"] = v[2]
            if len(v) > 3:
                a["tmax"] = v[3]
            elif v[0] > 0:
                a["tmax"] = v[0]
            self.analyses.append(a)
        elif low.startswith(".ac"):
            mode, n, f1, f2 = t[1].lower(), evaluate(t[2], self.params), evaluate(t[3], self.params), evaluate(t[4], self.params)
            self.analyses.append({"type": "ac", "mode": mode, "points": n, "fstart": f1, "fstop": f2})
        elif low.startswith(".dc"):
            a = {"type": "dc", "source": t[1], "start": evaluate(t[2], self.params), "stop": evaluate(t[3], self.params),
                 "step": evaluate(t[4], self.params)}
            if len(t) >= 9:
                a.update(source2=t[5], start2=evaluate(t[6], self.params), stop2=evaluate(t[7], self.params), step2=evaluate(t[8], self.params))
            self.analyses.append(a)
        elif low.startswith((".print", ".probe", ".plot")):
            self.probes += [x for x in t[1:] if re.match(r"[vi]\(", x, re.I)]
        elif low.startswith((".end", ".title", ".width", ".save", ".meas", ".four", ".noise", ".tf", ".ic", ".nodeset", ".global")):
            if low.startswith((".meas", ".four", ".noise", ".tf")):
                raise SpiceError(f"{t[0]} is not supported; use the measurement tools on the results")
        elif low.startswith((".include", ".inc", ".lib")):
            raise SpiceError(f"{t[0]}: host files cannot be read; paste the models into the deck or use Proteus library names")
        elif low.startswith("."):
            raise SpiceError(f"unsupported directive {t[0]}")
        else:
            self.main.append(ln)


def _source(toks: list[str], params: dict[str, float]) -> dict[str, Any]:
    """Tokens after the node names of a V/I source -> {value, wave, ac}."""
    out: dict = {"value": 0.0}
    i = 0
    while i < len(toks):
        tk = toks[i]
        low = tk.lower()
        name = re.match(r"([a-z]+)\(", low)
        if low == "dc" and i + 1 < len(toks):
            out["value"] = evaluate(toks[i + 1], params)
            i += 1
        elif low == "ac":
            out["ac"] = True
            if i + 1 < len(toks) and re.match(r"[-+.\d{]", toks[i + 1]):
                i += 1
                if i + 1 < len(toks) and re.match(r"[-+.\d{]", toks[i + 1]):
                    i += 1
        elif name:
            f = [evaluate(x, params) for x in _group(tk)] if name.group(1) != "pwl" else [evaluate(x, params) for x in _group(tk) if "=" not in x]
            n = name.group(1)
            if n in ("sin", "sine"):
                keys = ["offset", "amp", "freq", "delay", "damping", "phase"]
                out["wave"] = {"type": "sine", **dict(zip(keys, f))}
            elif n == "pulse":
                keys = ["low", "high", "delay", "rise", "fall", "width", "period"]
                out["wave"] = {"type": "pulse", **dict(zip(keys, f))}
            elif n == "exp":
                out["wave"] = {"type": "exp", **dict(zip(["low", "high", "td1", "tau1", "td2", "tau2"], f))}
            elif n == "sffm":
                out["wave"] = {"type": "sffm", **dict(zip(["offset", "amp", "carrier", "index", "signal"], f))}
            elif n == "pwl":
                out["wave"] = {"type": "pwl", "points": [[f[k], f[k + 1]] for k in range(0, len(f) - 1, 2)]}
            else:
                raise SpiceError(f"unsupported source function {tk}")
        elif re.match(r"[-+.\d{]", tk) and "value" in out and not out.get("_set"):
            out["value"] = evaluate(tk, params)
            out["_set"] = True
        i += 1
    out.pop("_set", None)
    return out


Params = dict[str, float]
NodeMap = Callable[[str], str]


@dataclass
class Elem:
    """One element line being translated."""
    name: str
    ref: str
    pos: list[str]
    kv: dict[str, str]
    toks: list[str]
    nmap: NodeMap
    params: Params
    prefix: str
    depth: int

    def nodes(self, n: int, start: int = 0) -> list[str]:
        if len(self.pos) < start + n:
            raise SpiceError(f"{self.name}: needs {n} nodes")
        return [self.nmap(x) for x in self.pos[start:start + n]]


Handler = Callable[["Translator", Elem], None]
ELEMENTS: dict[str, Handler] = {}


def spice_element(*letters: str) -> Callable[[Handler], Handler]:
    """Register the translator for SPICE element letters (lower case)."""
    def deco(fn: Handler) -> Handler:
        for ch in letters:
            ELEMENTS[ch] = fn
        return fn
    return deco


class Translator:
    """Deck -> circuit spec dict. `native=True` keeps X subcircuits as SPICE text for ProSPICE's parser (F/H/B/K inside work);
    `native=False` flattens them here (fallback)."""

    def __init__(self, deck: Deck, native: bool = True) -> None:
        self.d = deck
        self.native = native
        self.parts: list[dict[str, Any]] = []
        self.models: dict[str, dict[str, Any]] = {}
        self.subckts: dict[str, str] = {}

    def need_model(self, name: str) -> dict[str, Any]:
        k = name.lower()
        if k in self.d.models:
            t, p = self.d.models[k]
            self.models[name] = {"type": t, "params": p}
        else:
            lm = library.resolve(name)
            if lm is None:
                raise SpiceError(f"model {name!r} not defined in the deck and not found in the Proteus libraries")
            self.models[name] = {"type": lm[0], "params": lm[1]}
        return self.models[name]

    def element(self, ln: str, prefix: str, nmap: NodeMap, params: Params, depth: int = 0) -> None:
        t = _split(ln)
        pos, kv = _kv(t[1:])
        handler = ELEMENTS.get(t[0][0].lower())
        if handler is None:
            raise SpiceError(f"{t[0]}: unknown element letter {t[0][0]!r}")
        handler(self, Elem(t[0], prefix + t[0], pos, kv, t, nmap, params, prefix, depth))

    def run(self) -> dict[str, Any]:
        d = self.d
        for ln in d.main:
            self.element(ln, "", lambda x: "0" if x == "0" or x.lower() == "gnd" else x, d.params)
        spec: dict[str, Any] = {"title": d.title or "spice deck", "parts": self.parts, "models": self.models}
        if self.subckts:
            spec["subckts"] = self.subckts
            spec["spice_text"] = "\n".join(d.model_lines)
        if d.temp is not None:
            spec["temp"] = d.temp
        if d.options:
            spec["options_raw"] = d.options
        spec["analyses"] = d.analyses
        spec["analysis"] = d.analyses[0] if d.analyses else {"type": "op"}
        nodes: list[str] = []
        for p in self.parts:
            for n in p.get("nodes", []):
                if n != "0" and n not in nodes:
                    nodes.append(n)
        spec["probes"] = d.probes or [f"v({n})" for n in nodes if "." not in n]
        return spec


@spice_element("r", "c", "l")
def _rcl(tr: Translator, e: Elem) -> None:
    if len(e.pos) < 3:
        raise SpiceError(f"{e.name}: needs 2 nodes and a value")
    val = next((evaluate(x, e.params) for x in e.pos[2:] if re.match(r"[-+.\d{'(]", x)), None)
    if val is None:
        raise SpiceError(f"{e.name}: no value")
    p: dict[str, Any] = {"ref": e.ref, "kind": e.name[0].upper(), "nodes": e.nodes(2), "value": val}
    if "ic" in e.kv:
        p["ic"] = evaluate(e.kv["ic"], e.params)
    tr.parts.append(p)


@spice_element("v", "i")
def _source_element(tr: Translator, e: Elem) -> None:
    src = _source(e.toks[3:], e.params)
    p: dict[str, Any] = {"ref": e.ref, "kind": e.name[0].upper(), "nodes": e.nodes(2), "value": src["value"]}
    if "wave" in src:
        p["wave"] = src["wave"]
    if src.get("ac"):
        p["ac"] = True
    tr.parts.append(p)


@spice_element("d")
def _diode(tr: Translator, e: Elem) -> None:
    tr.need_model(e.pos[2])
    tr.parts.append({"ref": e.ref, "kind": "D", "nodes": e.nodes(2), "model": e.pos[2]})


@spice_element("q")
def _bjt(tr: Translator, e: Elem) -> None:
    mi = 4 if len(e.pos) >= 5 and (e.pos[4].lower() in tr.d.models or library.resolve(e.pos[4])) else 3
    mt = tr.need_model(e.pos[mi])["type"]
    q: dict[str, Any] = {"ref": e.ref, "kind": "QP" if mt == "PNP" else "Q", "nodes": e.nodes(3), "model": e.pos[mi]}
    if any(x.lower() == "off" for x in e.pos[mi + 1:]):
        q["params"] = {"OFF": "TRUE"}
    tr.parts.append(q)


@spice_element("j")
def _jfet(tr: Translator, e: Elem) -> None:
    mt = tr.need_model(e.pos[3])["type"]
    tr.parts.append({"ref": e.ref, "kind": "JP" if mt == "PJF" else "J", "nodes": e.nodes(3), "model": e.pos[3]})


@spice_element("m")
def _mosfet(tr: Translator, e: Elem) -> None:
    mt = tr.need_model(e.pos[4])["type"]
    tr.parts.append({"ref": e.ref, "kind": "MP" if mt == "PMOS" else "M", "nodes": e.nodes(4), "model": e.pos[4],
                     "params": {k.upper(): evaluate(v, e.params) for k, v in e.kv.items() if k in ("w", "l", "as", "ad", "ps", "pd")}})


@spice_element("e", "g")
def _controlled(tr: Translator, e: Elem) -> None:
    if len(e.pos) < 5:
        raise SpiceError(f"{e.name}: only 'n+ n- nc+ nc- gain' form is supported")
    tr.parts.append({"ref": e.ref, "kind": e.name[0].upper(), "nodes": e.nodes(4), "value": evaluate(e.pos[4], e.params)})


@spice_element("f", "h")
def _current_controlled(tr: Translator, e: Elem) -> None:
    if len(e.pos) < 4:
        raise SpiceError(f"{e.name}: needs 'n+ n- Vcontrol gain'")
    tr.parts.append({"ref": e.ref, "kind": e.name[0].upper(), "nodes": e.nodes(2), "control": e.prefix + e.pos[2], "value": evaluate(e.pos[3], e.params)})


@spice_element("k")
def _mutual(tr: Translator, e: Elem) -> None:
    if len(e.pos) < 3:
        raise SpiceError(f"{e.name}: needs 'L1 L2 k'")
    tr.parts.append({"ref": e.ref, "kind": "K", "inductors": [e.prefix + e.pos[0], e.prefix + e.pos[1]], "value": evaluate(e.pos[2], e.params)})


def _clean_name(x: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", x)


def _wrap(tr: Translator, e: Elem, sub: str, ports: int, body: str, nodes: list[str]) -> None:
    """One-element subcircuit (ProSPICE's own SPICE parser handles S, B ...)."""
    tr.subckts[sub] = f".SUBCKT {sub} {' '.join(f'p{i + 1}' for i in range(ports))}\n{body}\n.ENDS"
    tr.parts.append({"ref": "X" + e.ref.replace(".", "_"), "kind": "X", "nodes": nodes, "subckt": sub})


def _model_line(tr: Translator, e: Elem, name: str) -> str:
    mdl = next((m for m in tr.d.model_lines if re.match(rf"\.model\s+{re.escape(name)}\s", m, re.I)), None)
    if mdl is None:
        raise SpiceError(f"{e.name}: model {name!r} not defined in the deck")
    return mdl


@spice_element("s")
def _vswitch(tr: Translator, e: Elem) -> None:
    if len(e.pos) < 5:
        raise SpiceError(f"{e.name}: needs 'n+ n- nc+ nc- model'")
    sub = f"SW_{e.ref}".replace(".", "_")
    flags = " ".join(x for x in e.pos[5:] if x.lower() in ("on", "off"))
    _wrap(tr, e, sub, 4, f"S{e.name[1:]} p1 p2 p3 p4 {e.pos[4]} {flags}\n{_model_line(tr, e, e.pos[4])}", e.nodes(4))


@spice_element("w")
def _cswitch(tr: Translator, e: Elem) -> None:
    """Current-controlled switch: sense the control current with a 1 ohm transresistance (H) and switch on that voltage (S)."""
    if len(e.pos) < 4:
        raise SpiceError(f"{e.name}: needs 'n+ n- Vcontrol model'")
    mline = _model_line(tr, e, e.pos[3])
    m = re.match(r"\.model\s+\S+\s+\w+\s*\(?(.*?)\)?\s*$", mline, re.I)
    kv = {k.lower(): v for k, v in re.findall(r"(\w+)\s*=\s*([^\s,()]+)", m.group(1) if m else "")}
    sw = f".model {e.pos[3]}_s SW(vt={kv.get('it', '0')} vh={kv.get('ih', '0')} ron={kv.get('ron', '1')} roff={kv.get('roff', '1e6')})"
    sense = "sense" + _clean_name(e.ref)                      # no '_' in H refs: ProSPICE reads "<prefix>_" as a subcircuit prefix
    tr.parts.append({"ref": f"HS{_clean_name(e.ref)}", "kind": "H", "nodes": [sense, "0"], "control": e.prefix + e.pos[2], "value": 1})
    sub = f"CSW_{e.ref}".replace(".", "_")
    _wrap(tr, e, sub, 4, f"S{e.name[1:]} p1 p2 p3 0 {e.pos[3]}_s\n{sw}", [*e.nodes(2), sense, "0"])


_BEXPR = re.compile(r"(?i)\s*([vi])\s*=\s*(.*)")


@spice_element("b")
def _behavioural(tr: Translator, e: Elem) -> None:
    m = _BEXPR.match(" ".join(e.toks[3:]))
    if len(e.pos) < 2 or not m:
        raise SpiceError(f"{e.name}: only 'B n+ n- V={{expr}}' / 'I={{expr}}' is supported")
    expr = m.group(2).strip().strip("{}'\"")
    nets: list[str] = []

    def net(x: str) -> str:
        x = "0" if x.lower() in ("0", "gnd") else x
        if x == "0":
            return "0"
        if x not in nets:
            nets.append(x)
        return f"q{nets.index(x) + 1}"

    holes: list[str] = []

    def cur(mm: re.Match[str]) -> str:
        """i(Vx) -> voltage of a 1 ohm CCVS that senses Vx's current (the subcircuit boundary hides Vx)."""
        vname = e.prefix + mm.group(1).strip()
        aux = "isense" + _clean_name(vname)
        if not any(p["ref"] == f"HI{_clean_name(vname)}" for p in tr.parts):
            tr.parts.append({"ref": f"HI{_clean_name(vname)}", "kind": "H", "nodes": [aux, "0"], "control": vname, "value": 1})
        holes.append(net(e.nmap(aux)))
        return f"@@{len(holes) - 1}@@"
    expr = re.sub(r"(?i)\bi\(([^()]*)\)", cur, expr)
    expr = re.sub(r"(?i)\bv\(([^()]*)\)", lambda mm: "v(" + ",".join(net(e.nmap(x.strip())) if x.strip() not in ("0", "gnd") else "0" for x in mm.group(1).split(",")) + ")", expr)
    expr = re.sub(r"@@(\d+)@@", lambda mm: f"v({holes[int(mm.group(1))]})", expr)
    sub = f"BS_{e.ref}".replace(".", "_")
    ports = ["p1", "p2"] + [f"q{i + 1}" for i in range(len(nets))]
    tr.subckts[sub] = f".SUBCKT {sub} {' '.join(ports)}\nB{e.name[1:]} p1 p2 {m.group(1).upper()}={expr}\n.ENDS"
    tr.parts.append({"ref": "X" + e.ref.replace(".", "_"), "kind": "X", "nodes": [*e.nodes(2), *nets], "subckt": sub})


@spice_element("x")
def _subckt_call(tr: Translator, e: Elem) -> None:
    d = tr.d
    sub = e.pos[-1].lower()
    actual = e.pos[:-1]
    if sub not in d.subckts:
        txt = library.get(e.pos[-1])
        if txt is None or ".subckt" not in txt.lower():
            raise SpiceError(f"{e.name}: subcircuit {e.pos[-1]!r} not defined and not in the Proteus libraries")
        if tr.native and e.depth == 0:
            tr.parts.append({"ref": e.ref, "kind": "X", "nodes": [e.nmap(x) for x in actual], "subckt": e.pos[-1]})
            return
        sd = Deck(txt)
        d.subckts.update(sd.subckts)
        d.subckt_names.update(sd.subckt_names)
        for k, v in sd.models.items():
            d.models.setdefault(k, v)
        if sub not in d.subckts:
            raise SpiceError(f"{e.name}: library entry {e.pos[-1]!r} has no .SUBCKT")
    ports, defaults, body = d.subckts[sub]
    if tr.native and e.depth == 0:
        header = ".SUBCKT " + d.subckt_names[sub] + " " + " ".join(ports) + ((" PARAMS: " + " ".join(f"{k}={v}" for k, v in defaults.items())) if defaults else "")
        tr.subckts[d.subckt_names[sub]] = "\n".join([header, *body, ".ENDS"])
        tr.parts.append({"ref": e.ref, "kind": "X", "nodes": [e.nmap(x) for x in actual], "subckt": d.subckt_names[sub]})
        return
    if len(ports) != len(actual):
        raise SpiceError(f"{e.name}: {e.pos[-1]} has {len(ports)} ports, got {len(actual)} nodes")
    if e.depth > 12:
        raise SpiceError("subcircuit nesting too deep")
    local = dict(e.params)
    for k, v in defaults.items():
        local[k.lower()] = evaluate(v, local)
    local.update({k.lower(): evaluate(v, e.params) for k, v in e.kv.items()})
    mapping = {p.lower(): e.nmap(a) for p, a in zip(ports, actual)}
    pre = e.ref + "."

    def sub_nmap(x: str) -> str:
        return "0" if x == "0" else mapping.get(x.lower(), pre + x)
    for bl in body:
        if bl.lower().startswith(".param"):
            for tok in _split(bl)[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    local[k.lower()] = evaluate(v, local)
        elif not bl.startswith("."):
            tr.element(bl, pre, sub_nmap, local, e.depth + 1)


def parse(text: str, native: bool = True) -> dict[str, Any]:
    """SPICE deck -> circuit spec dict (feed it to `circuit.build` / `sim.simulate`)."""
    return Translator(Deck(text), native).run()
