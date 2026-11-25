"""SPICE deck -> circuit spec (flattens .SUBCKT, evaluates .PARAM/{expr}, resolves library models/subcircuits).

Supported: R C L V I D Q J M E G X(subckt) ; V/I sources DC/AC/SIN/PULSE/EXP/SFFM/PWL ; .MODEL .SUBCKT .PARAM .TEMP .OPTIONS
.OP .TRAN .AC .DC .PRINT/.PROBE V()/I(). Not supported yet (clear error): W and i() inside B, and .INCLUDE/.LIB of host files.
Numbers are SPICE notation (m = milli, MEG = mega).
"""
from __future__ import annotations

import math
import re

from . import library
from .sdf import spice_number


class SpiceError(ValueError):
    pass


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
    def __init__(self, text: str):
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

    def _model(self, ln: str):
        m = re.match(r"\.model\s+(\S+)\s+(\w+)\s*\(?(.*?)\)?\s*$", ln, re.I)
        if not m:
            raise SpiceError(f"bad .MODEL: {ln}")
        self.model_lines.append(ln)
        self.models[m.group(1).lower()] = (m.group(2).upper(), dict((k.upper(), v) for k, v in re.findall(r"(\w+)\s*=\s*([^\s,()]+)", m.group(3))))

    def _top(self, ln: str):
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


def _source(toks: list[str], params) -> dict:
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


KNOWN_PRIMARY = {"r", "c", "l", "v", "i", "d", "q", "j", "m", "e", "g", "x"}


def parse(text: str, native: bool = True) -> dict:
    """native=True keeps X subcircuits as SPICE text for ProSPICE's own parser (F/H/B/K inside subcircuits work);
    native=False flattens them here (fallback)."""
    d = Deck(text)
    used_subckts: dict[str, str] = {}
    parts: list[dict] = []
    models: dict[str, dict] = {}

    def need_model(name: str, kind_hint: str | None = None):
        k = name.lower()
        if k in d.models:
            t, p = d.models[k]
            models[name] = {"type": t, "params": p}
        else:
            lm = library.resolve(name)
            if lm is None:
                raise SpiceError(f"model {name!r} not defined in the deck and not found in the Proteus libraries")
            models[name] = {"type": lm[0], "params": lm[1]}

    def element(ln: str, prefix: str, nmap, params: dict[str, float], depth=0):
        t = _split(ln)
        name = t[0]
        ref = prefix + name
        pos, kv = _kv(t[1:])
        kind = name[0].lower()

        def N(x):
            return nmap(x)
        if kind in "rcl":
            if len(pos) < 3:
                raise SpiceError(f"{name}: needs 2 nodes and a value")
            val = next((evaluate(x, params) for x in pos[2:] if re.match(r"[-+.\d{'(]", x)), None)
            if val is None:
                raise SpiceError(f"{name}: no value")
            p = {"ref": ref, "kind": kind.upper(), "nodes": [N(pos[0]), N(pos[1])], "value": val}
            if "ic" in kv:
                p["ic"] = evaluate(kv["ic"], params)
            parts.append(p)
        elif kind in "vi":
            if len(pos) < 2:
                raise SpiceError(f"{name}: needs 2 nodes")
            src = _source(t[3:], params)
            p = {"ref": ref, "kind": kind.upper(), "nodes": [N(pos[0]), N(pos[1])], "value": src["value"]}
            if "wave" in src:
                p["wave"] = src["wave"]
            if src.get("ac"):
                p["ac"] = True
            parts.append(p)
        elif kind == "d":
            need_model(pos[2])
            parts.append({"ref": ref, "kind": "D", "nodes": [N(pos[0]), N(pos[1])], "model": pos[2]})
        elif kind == "q":
            mi = 4 if len(pos) >= 5 and (pos[4].lower() in d.models or library.resolve(pos[4])) else 3
            need_model(pos[mi])
            mt = models[pos[mi]]["type"]
            q = {"ref": ref, "kind": "QP" if mt == "PNP" else "Q", "nodes": [N(x) for x in pos[:3]], "model": pos[mi]}
            if any(x.lower() == "off" for x in pos[mi + 1:]):
                q["params"] = {"OFF": "TRUE"}
            parts.append(q)
        elif kind == "j":
            need_model(pos[3])
            mt = models[pos[3]]["type"]
            parts.append({"ref": ref, "kind": "JP" if mt == "PJF" else "J", "nodes": [N(x) for x in pos[:3]], "model": pos[3]})
        elif kind == "m":
            need_model(pos[4])
            mt = models[pos[4]]["type"]
            parts.append({"ref": ref, "kind": "MP" if mt == "PMOS" else "M", "nodes": [N(x) for x in pos[:4]], "model": pos[4],
                          "params": {k.upper(): evaluate(v, params) for k, v in kv.items() if k in ("w", "l", "as", "ad", "ps", "pd")}})
        elif kind in "eg":
            if len(pos) < 5:
                raise SpiceError(f"{name}: only 'n+ n- nc+ nc- gain' form is supported")
            parts.append({"ref": ref, "kind": kind.upper(), "nodes": [N(x) for x in pos[:4]], "value": evaluate(pos[4], params)})
        elif kind == "x" and native and depth == 0:
            sub = pos[-1].lower()
            if sub not in d.subckts:
                txt = library.get(pos[-1])
                if txt is None or ".subckt" not in txt.lower():
                    raise SpiceError(f"{name}: subcircuit {pos[-1]!r} not defined and not in the Proteus libraries")
            else:
                ports, defaults, body = d.subckts[sub]
                header = ".SUBCKT " + d.subckt_names[sub] + " " + " ".join(ports) + ((" PARAMS: " + " ".join(f"{k}={v}" for k, v in defaults.items())) if defaults else "")
                used_subckts[d.subckt_names[sub]] = "\n".join([header, *body, ".ENDS"])
            parts.append({"ref": ref, "kind": "X", "nodes": [N(x) for x in pos[:-1]], "subckt": d.subckt_names.get(sub, pos[-1])})
        elif kind == "x":
            sub = pos[-1].lower()
            actual = pos[:-1]
            ps = {k: evaluate(v, params) for k, v in kv.items()}
            if sub not in d.subckts:
                txt = library.get(pos[-1])
                if txt is None or ".subckt" not in txt.lower():
                    raise SpiceError(f"{name}: subcircuit {pos[-1]!r} not defined and not in the Proteus libraries")
                sd = Deck(txt)
                d.subckts.update(sd.subckts)
                for k, v in sd.models.items():
                    d.models.setdefault(k, v)
                if sub not in d.subckts:
                    raise SpiceError(f"{name}: library entry {pos[-1]!r} has no .SUBCKT")
            ports, defaults, body = d.subckts[sub]
            if len(ports) != len(actual):
                raise SpiceError(f"{name}: {pos[-1]} has {len(ports)} ports, got {len(actual)} nodes")
            if depth > 12:
                raise SpiceError("subcircuit nesting too deep")
            local = dict(params)
            for k, v in defaults.items():
                local[k.lower()] = evaluate(v, local)
            local.update({k.lower(): v for k, v in ps.items()})
            mapping = {p.lower(): N(a) for p, a in zip(ports, actual)}

            def sub_nmap(x, mapping=mapping, pre=ref + "."):
                if x == "0":
                    return "0"
                return mapping.get(x.lower(), pre + x)
            for bl in body:
                if bl.lower().startswith(".param"):
                    for tok in _split(bl)[1:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            local[k.lower()] = evaluate(v, local)
                elif bl.startswith("."):
                    continue
                else:
                    element(bl, ref + ".", sub_nmap, local, depth + 1)
        elif kind in "fh":
            if len(pos) < 4:
                raise SpiceError(f"{name}: needs 'n+ n- Vcontrol gain'")
            parts.append({"ref": ref, "kind": kind.upper(), "nodes": [N(pos[0]), N(pos[1])], "control": prefix + pos[2], "value": evaluate(pos[3], params)})
        elif kind == "k":
            if len(pos) < 3:
                raise SpiceError(f"{name}: needs 'L1 L2 k'")
            parts.append({"ref": ref, "kind": "K", "inductors": [prefix + pos[0], prefix + pos[1]], "value": evaluate(pos[2], params)})
        elif kind in "sb":
            # switches and behavioural sources are expressed as one-element subcircuits that ProSPICE's own SPICE parser handles
            if kind == "s":
                if len(pos) < 5:
                    raise SpiceError(f"{name}: needs 'n+ n- nc+ nc- model'")
                mdl = next((m for m in d.model_lines if re.match(rf"\.model\s+{re.escape(pos[4])}\s", m, re.I)), None)
                if mdl is None:
                    raise SpiceError(f"{name}: switch model {pos[4]!r} not defined in the deck")
                nodes = [N(x) for x in pos[:4]]
                sub = f"SW_{ref}".replace(".", "_")
                body = f"S{name[1:]} p1 p2 p3 p4 {pos[4]} {' '.join(x for x in pos[5:] if x.lower() in ('on', 'off'))}\n{mdl}"
                used_subckts[sub] = f".SUBCKT {sub} p1 p2 p3 p4\n{body}\n.ENDS"
            else:
                m = re.match(r"(?i)\s*([vi])\s*=\s*(.*)", " ".join(t[3:]))
                if len(pos) < 2 or not m:
                    raise SpiceError(f"{name}: only 'B n+ n- V={{expr}}' / 'I={{expr}}' is supported")
                expr = m.group(2).strip().strip("{}'\"")
                if re.search(r"(?i)\bi\s*\(", expr):
                    raise SpiceError(f"{name}: i(...) inside a behavioural source cannot cross the subcircuit boundary")
                nets: list[str] = []

                def port(mm):
                    names = [x.strip() for x in mm.group(1).split(",")]
                    ps = []
                    for x in names:
                        x = "0" if x.lower() in ("0", "gnd") else x
                        if x == "0":
                            ps.append("0")
                            continue
                        if x not in nets:
                            nets.append(x)
                        ps.append(f"q{nets.index(x) + 1}")
                    return "v(" + ",".join(ps) + ")"
                expr2 = re.sub(r"(?i)\bv\(([^()]*)\)", port, expr)
                nodes = [N(pos[0]), N(pos[1])] + [N(x) for x in nets]
                sub = f"BS_{ref}".replace(".", "_")
                ports = ["p1", "p2"] + [f"q{i + 1}" for i in range(len(nets))]
                used_subckts[sub] = f".SUBCKT {sub} {' '.join(ports)}\nB{name[1:]} p1 p2 {m.group(1).upper()}={expr2}\n.ENDS"
            parts.append({"ref": "X" + ref.replace(".", "_"), "kind": "X", "nodes": nodes, "subckt": sub})
        elif kind in "w":
            raise SpiceError(f"{name}: element type {kind.upper()} is not supported yet")
        else:
            raise SpiceError(f"{name}: unknown element letter {kind!r}")

    for ln in d.main:
        element(ln, "", lambda x: "0" if x == "0" or x.lower() in ("gnd",) else x, d.params)

    spec: dict = {"title": d.title or "spice deck", "parts": parts, "models": models}
    if used_subckts:
        spec["subckts"] = used_subckts
        spec["spice_text"] = "\n".join(d.model_lines)
    if d.temp is not None:
        spec["temp"] = d.temp
    if d.options:
        spec["options_raw"] = d.options
    spec["analyses"] = d.analyses
    spec["analysis"] = d.analyses[0] if d.analyses else {"type": "op"}
    nodes: list[str] = []
    for p in parts:
        for n in p.get("nodes", []):
            if n != "0" and n not in nodes:
                nodes.append(n)
    spec["probes"] = d.probes or [f"v({n})" for n in nodes if "." not in n]
    return spec
