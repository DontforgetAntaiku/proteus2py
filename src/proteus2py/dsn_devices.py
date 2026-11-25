"""Schematic components (ROOT.CDB parts + their properties) -> circuit-spec `Part` dicts, so a netlist extracted without the GUI can be simulated.

Entry points
  to_spec_parts(ref, device, value, props, pin_nets) -> list[dict]   one component, possibly several spec parts (pots, crystals, digital ICs ...)
  to_spec_part(...)                                  -> dict | None  the common single-part case (raises when the component expands to several)
  spec_from_parts(parts, nets) / spec_from_cdb(cdb_bytes, nets) -> `SpecResult` for a whole design

`pin_nets` maps the component's pin NAME (as in the CDB element: ``A``, ``K``, ``+``, ``D0`` ...) to a net name; pins that are absent are unconnected.
`props` is the CDB part property text (``{PRIMITIVE=ANALOGUE,NPN}{MODEL=LX_NPN_SSLF,BIPOLAR}`` plus bare ``K=V`` lines) or a dict.

How a component is classified (first hit wins), after merging the device-library record (``{*COMPONENT}`` section of DATA/LIBRARY/*.LIB, read lazily and
optional) under the instance properties:
  1. ``{PRIMITIVE=<domain>,<TYPE>}``  TYPE in the primitive table below (RESISTOR, CAPACITOR, DIODE, NPN, SUBCKT, POT, RTSWITCH ...), DSIM gate/flip-flop names
  2. ``{PRIMTYPE=RESISTOR|CAPACITOR|INDUCTOR}`` (domain-only ``{PRIMITIVE=DIGITAL}`` resistors)
  3. the device name (RES, RESISTOR, CAP, CAP-ELEC, IND, BATTERY, CELL, DIODE, NPN, VSINE, VPULSE ...)
  4. ``{MODFILE=name}`` -> the *.LML model description (what ISIS flattens into ``U1:A_U3`` style SDF parts): expanded part by part, each sub-part is
     classified again with these same rules (74xx gates, POT-LIN, SPDT switches, crystals ...)
  5. ``{SPICEMODEL=..}{SPICEPINS=..}`` -> kind ``X`` with the nodes in SPICEPINS order
Non-simulated things (NULL/crystal symbols, real-time probes, VU meters) give ``[]`` / ``None``; simulated things that have no spec equivalent (VSM micro-
controller DLL models, behavioural AVS/ACS, analogue switches ...) raise `UnsupportedDevice` (``strict=False`` turns that into ``None``).
"""
from __future__ import annotations

import ast
import math
import mmap
import re
import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from . import library

__all__ = ["DeviceError", "SpecResult", "UnsupportedDevice", "connect_groups", "isis_number", "parse_props", "read_components", "spec_from_cdb",
           "spec_from_parts", "to_spec_part", "to_spec_parts"]

Spec = dict[str, Any]
Props = Mapping[str, str] | str | None


class DeviceError(ValueError):
    """The component cannot be translated (bad input: missing pin / value)."""


class UnsupportedDevice(DeviceError):
    """A simulated component with no circuit-spec equivalent (the message says why)."""


# ---------------------------------------------------------------------------------------------------------------- numbers

_EXP = {"f": -15, "p": -12, "n": -9, "u": -6, "µ": -6, "m": -3, "k": 3, "K": 3, "M": 6, "G": 9, "T": 12, "meg": 6, "MEG": 6, "Meg": 6, "": 0}
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(rf"\s*({_NUM})\s*(meg|MEG|Meg|[fpnuµmkKMGT])?\s*([A-Za-zΩµ%]*)\s*")
_RKM_RE = re.compile(r"\s*(\d+)([fpnuµmkKMGTR])(\d+)\s*")
_TOKEN_RE = re.compile(r"(?<![A-Za-z_\d.])(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?:meg|MEG|Meg|[fpnuµmkKMGT])?(?:[A-Za-zΩµ%]*)")
_EXPR_OK = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Call, ast.Name, ast.Load)
_E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
_E24 = (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1)


def _preferred(series: tuple[float, ...]) -> Callable[[float], float]:
    def f(x: float) -> float:
        dec = 10.0 ** math.floor(math.log10(x))
        return dec * min((*series, 10.0), key=lambda v: abs(math.log(v * dec / x)))
    return f


_FUNCS: dict[str, Callable[..., float]] = {"E12": _preferred(_E12), "E24": _preferred(_E24), "SQRT": math.sqrt, "EXP": math.exp, "LOG": math.log, "LN": math.log,
                                            "SIN": math.sin, "COS": math.cos, "ABS": abs}
_CONSTS = {"PI": math.pi, "VTHERM": 8.617333262e-5 * 300.15}                 # kT/q at TNOM = 27 C


def _plain_number(text: str) -> float | None:
    m = _NUM_RE.fullmatch(text)
    if m:
        return _scaled(m.group(1), m.group(2) or "")
    m = _RKM_RE.fullmatch(text)                                                           # 1k6 = 1.6k, 4u7, 2R2
    if m:
        return _scaled(f"{m.group(1)}.{m.group(3)}", "" if m.group(2) == "R" else m.group(2))
    return None


def _scaled(mantissa: str, suffix: str) -> float:
    """mantissa x 10^exp(suffix) without the rounding noise of float multiplication (100u -> 1e-4 exactly)."""
    if "e" in mantissa.lower():
        return float(mantissa) * 10.0 ** _EXP[suffix]
    return float(f"{mantissa}e{_EXP[suffix]}")


def _var(names: Mapping[str, str | float] | None, name: str) -> float | None:
    for k, v in (names or {}).items():
        if k.upper() == name:
            return isis_number(v)
    return None


def isis_number(text: str | float | int | None, names: Mapping[str, str | float] | None = None) -> float | None:
    """ISIS notation -> float: m = milli, M = mega (MEG too), k u n p f G T, trailing units (V, F, Hz, Ohms) ignored, ``1k6``, and arithmetic like
    ``220*1.41`` or ``0.5*1k``, ``EVAL(..)``, ``E12(..)``, PI and VTHERM; ``names`` supplies design variables (``IMP``, ``FREQ``).  None when the text is
    not a number."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    m = re.fullmatch(r"EVAL\((.*)\)", s, re.I | re.S)                                  # ISIS evaluates EVAL(expr) values
    if m:
        s = m.group(1).strip()
    if not s:
        return None
    v = _plain_number(s)
    if v is not None:
        return v
    if not re.search(r"[-+*/()]", s[1:] if s[0] in "+-" else s):
        return _CONSTS.get(s.upper()) or _var(names, s.upper())
    try:
        expr = _TOKEN_RE.sub(lambda m: repr(x) if (x := _plain_number(m.group(0))) is not None else m.group(0), s)
        tree = ast.parse(expr.upper(), mode="eval")
        calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        if not all(isinstance(n, _EXPR_OK) for n in ast.walk(tree)):
            return None
        env: dict[str, Any] = {**_CONSTS, **_FUNCS}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and id(n) not in calls and n.id not in _CONSTS:
                v = _var(names, n.id)
                if v is None:
                    return None
                env[n.id] = v
        if any(isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id.upper() in _FUNCS and not n.keywords) for n in ast.walk(tree)):
            return None
        out = eval(compile(tree, "<isis>", "eval"), {"__builtins__": {}}, env)                # noqa: S307 - only numeric nodes survive the whitelist above
        return float(out)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


def _n(x: str | float | int | None, names: Mapping[str, str | float] | None = None) -> float | int | str | None:
    """Spec number for an ISIS value: float/int when it parses, else the original text (the spec accepts strings)."""
    v = isis_number(x, names)
    if v is None:
        return None if x is None or str(x).strip() == "" else str(x).strip()
    v = float(f"{v:.12g}")
    return int(v) if v.is_integer() and abs(v) < 1e15 else v


# ---------------------------------------------------------------------------------------------------------------- properties

_BRACED = re.compile(r"\{([^{}=\r\n]+)=([^{}\r\n]*)\}")
_BARE = re.compile(r"(?:^|\s)([A-Za-z_][\w()+\-.]*)=([^\s{}]*)")


def _key(k: str) -> str:
    k = k.strip()
    return k if re.match(r"[VI]\(", k) else k.upper()                                  # V(1m) = point of a PWL source: the case of the time unit matters


def parse_props(props: Props) -> dict[str, str]:
    """``{K=V}`` blocks and bare ``K=V`` tokens -> dict (keys upper-case, ``PRIMITIVE`` keeps ``DOMAIN,TYPE``, the first occurrence of a key wins;
    ``V(t)`` keys keep their case)."""
    if props is None:
        return {}
    if not isinstance(props, str):
        return {_key(str(k)): str(v) for k, v in props.items()}
    out: dict[str, str] = {}
    for m in _BRACED.finditer(props):
        out.setdefault(_key(m.group(1)), m.group(2).strip())
    for m in _BARE.finditer(_BRACED.sub(" ", props)):
        out.setdefault(_key(m.group(1)), m.group(2).strip())
    return out


# ---------------------------------------------------------------------------------------------------------------- Proteus libraries (optional)

def _proteus_dirs() -> tuple[Path, Path] | None:
    try:
        models = library.models_dir()
    except Exception:                                                                       # no workspace configured: offline use
        return None
    return (models, models.parent / "LIBRARY") if models.is_dir() else None


@cache
def _lib_index() -> dict[str, tuple[Path, int]]:
    """Device name -> (DEVICE LIBRARY file, record offset): 0x50-byte entries from 0x20 (name[0x40], u32 offset)."""
    idx: dict[str, tuple[Path, int]] = {}
    dirs = _proteus_dirs()
    if not dirs:
        return idx
    for f in sorted(dirs[1].glob("*.LIB")):
        try:
            with f.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as b:
                if not b[:14] == b"DEVICE LIBRARY":
                    continue
                p = 0x20
                while p + 0x50 <= len(b):
                    name = bytes(b[p:p + 0x40]).split(b"\0")[0].decode("latin-1")
                    off = struct.unpack_from("<I", b, p + 0x40)[0]
                    if not name or not name.isprintable() or off == 0 or off + 4 > len(b):
                        break
                    idx.setdefault(name.upper(), (f, off))
                    p += 0x50
        except (OSError, ValueError):
            continue
    return idx


@cache
def lib_component(device: str) -> Mapping[str, str]:
    """Default properties of a device-library entry (its ``{*COMPONENT}`` section); empty when the library is not installed or the name is unknown."""
    hit = _lib_index().get(device.upper())
    if not hit:
        return {}
    try:
        with hit[0].open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as b:
            n = struct.unpack_from("<I", b, hit[1])[0]
            blob = bytes(b[hit[1] + 4:hit[1] + 4 + n])
    except (OSError, ValueError, struct.error):
        return {}
    i = blob.find(b"{*COMPONENT}")
    if i < 0:
        return {}
    out: dict[str, str] = {}
    for m in re.finditer(rb"\s*\{([A-Za-z0-9_+\-()]+)=([^{}\r\n]*)\}", blob[i + 12:]):
        out.setdefault(m.group(1).decode("latin-1").upper(), m.group(2).decode("latin-1").strip())
    return out


@cache
def _lml_index() -> dict[str, tuple[Path, int]]:
    """Model description name (``74INV``) -> (MODEL LIBRARY file, offset): entries of 0x20 bytes from 0x20 (name[0x14], u32 offset, timestamp, aux)."""
    idx: dict[str, tuple[Path, int]] = {}
    dirs = _proteus_dirs()
    if not dirs:
        return idx
    for f in sorted(dirs[0].glob("*.LML")):
        try:
            b = f.read_bytes()
        except OSError:
            continue
        if not b.startswith(b"MODEL LIBRARY"):
            continue
        p = 0x20
        while p + 0x20 <= len(b):
            name = b[p:p + 0x14].split(b"\0")[0].decode("latin-1")
            off = struct.unpack_from("<I", b, p + 0x14)[0]
            if not name or not name.isprintable() or off == 0 or off + 4 > len(b):
                break
            idx.setdefault(name.upper(), (f, off))
            p += 0x20
    return idx


@cache
def mdf_text(name: str) -> str | None:
    """Text of a ``*.MDF`` model description from the DATA/MODELS/*.LML libraries (``74INV`` or ``74INV.MDF``)."""
    hit = _lml_index().get(re.sub(r"\.MDF$", "", name.strip(), flags=re.I).upper())
    if not hit:
        return None
    try:
        b = hit[0].read_bytes()
        n = struct.unpack_from("<I", b, hit[1])[0]
        return b[hit[1] + 4:hit[1] + 4 + n].decode("latin-1")
    except (OSError, struct.error):
        return None


# ---------------------------------------------------------------------------------------------------------------- model description files

def _unquote(v: str) -> str:
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def _split_csv(line: str) -> list[str]:
    """Comma split outside quotes and brackets; surrounding quotes are removed from each field."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    quoted = False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "([":
            depth += 1
        elif not quoted and ch in ")]":
            depth -= 1
        if ch == "," and not quoted and depth <= 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [f[1:-1] if len(f) >= 2 and f[0] == f[-1] == '"' else f for f in (x.strip() for x in out)]


@dataclass
class _NetGroup:
    name: str
    terminals: list[tuple[str, str]] = field(default_factory=list)              # (terminal name, IT/OT/BT/PT/GT/...)
    pins: list[tuple[str, str, str]] = field(default_factory=list)              # (part ref, pin type IP/OP/IO/PS/PP, pin name)


@dataclass
class _Mdf:
    defaults: dict[str, str] = field(default_factory=dict)
    key_spec: list[str] = field(default_factory=list)
    mappings: dict[str, dict[str, str]] = field(default_factory=dict)
    parts: list[tuple[str, str, str, dict[str, str]]] = field(default_factory=list)   # (ref, lib, value, props)
    nets: list[_NetGroup] = field(default_factory=list)


_SECTIONS = {"*PROPERTIES", "*MAPPINGS", "*MODELDEFS", "*PARTLIST", "*NETLIST", "*GATES"}


def parse_mdf(text: str) -> _Mdf:
    """``LISA MODEL DESCRIPTION FORMAT`` text (*PROPERTIES / *MAPPINGS / *PARTLIST / *NETLIST sections)."""
    m = _Mdf()
    section = ""
    group: _NetGroup | None = None
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        head = line.split(",")
        if head[0].upper() in _SECTIONS:                                           # "*V+,4" inside *NETLIST is a net name, not a section
            section = head[0].upper()
            if section == "*MAPPINGS" and len(head) > 2:
                m.key_spec = head[2].strip().split("+")
            group = None
            continue
        if section == "*NETLIST":
            if not line:
                group = None
                continue
            f = _split_csv(line)
            if group is None:
                group = _NetGroup(f[0])
                m.nets.append(group)
            elif len(f) == 2:
                group.terminals.append((f[0], f[1].upper()))
            elif len(f) >= 3:
                group.pins.append((f[0], f[1].upper(), ",".join(f[2:])))
            continue
        if not line or not section:
            continue
        if section == "*PROPERTIES" and "=" in line:
            k, v = line.split("=", 1)
            m.defaults[k.strip().upper()] = v.strip()
        elif section == "*MAPPINGS" and " : " in line:
            key, rest = line.split(" : ", 1)
            row: dict[str, str] = {}
            for fld in _split_csv(rest):
                if "=" in fld:
                    k, v = fld.split("=", 1)
                    row[k.strip().upper()] = _unquote(v)
            m.mappings[key.strip()] = row
        elif section == "*PARTLIST":
            f = _split_csv(line)
            if len(f) >= 3:
                props = {}
                for fld in f[3:]:
                    if "=" in fld:
                        k, v = fld.split("=", 1)
                        props[k.strip().upper()] = _unquote(v)
                m.parts.append((f[0], f[1], f[2], props))
    return m


# ---------------------------------------------------------------------------------------------------------------- component context

def _fold(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


@dataclass
class _Dev:
    ref: str
    device: str
    value: str
    props: dict[str, str]                                                       # merged, upper-case keys
    pins: dict[str, str]                                                        # pin name -> net
    pin_types: dict[str, str] = field(default_factory=dict)                     # pin name -> IP/OP/IO/PS (known inside model descriptions)
    variables: Mapping[str, str] = field(default_factory=dict)
    depth: int = 0

    @property
    def dom_type(self) -> tuple[str, str]:
        dom, _, typ = self.props.get("PRIMITIVE", "").partition(",")
        return dom.strip().upper(), typ.strip().upper()

    def net(self, *names: str) -> str | None:
        folded = {_fold(k): v for k, v in self.pins.items()}
        for n in names:
            if _fold(n) in folded:
                return folded[_fold(n)]
        return None

    def need(self, *names: str) -> str:
        """Net of the first named pin; an unconnected pin gets a private net (the spec has no 'open' pin)."""
        n = self.net(*names)
        return n if n is not None else re.sub(r"\W", "_", f"NC_{self.ref}_{names[0]}")

    def require(self, *names: str) -> str:
        n = self.net(*names)
        if n is None:
            raise DeviceError(f"{self.ref} ({self.device}): pin {'/'.join(names)} not in {sorted(self.pins)}")
        return n

    def pair(self) -> tuple[str, str]:
        """The two terminals of a two-pin part, in spec order."""
        for a, b in (("1", "2"), ("+", "-"), ("A", "K"), ("A", "B"), ("COM", "NO"), ("P", "N")):
            if self.net(a) is not None or self.net(b) is not None:
                return self.need(a), self.need(b)
        keys = list(self.pins)
        if len(keys) == 2:
            return self.pins[keys[0]], self.pins[keys[1]]
        return self.need("1"), self.need("2")                                    # both pins unconnected

    def text(self, s: str) -> str:
        """Resolve ``<NAME>`` placeholders (design variables) in a value; unresolved ones raise."""
        def sub(m: re.Match[str]) -> str:
            k = m.group(1).upper()
            v = self.variables.get(k) or self.variables.get(m.group(1)) or self.props.get(k)
            if v is None:
                raise UnsupportedDevice(f"{self.ref}: unresolved <{m.group(1)}> (design variable not supplied)")
            return v
        return re.sub(r"<([^<>]+)>", sub, s)


def _part(ref: str, kind: str, nodes: list[str], **fields: Any) -> Spec:
    d: Spec = {"ref": ref, "kind": kind, "nodes": nodes}
    d.update({k: v for k, v in fields.items() if v is not None and v != {} and v != ""})
    return d


def _value(dev: _Dev, *prop_names: str) -> float | int | str:
    for raw in (dev.value, *[dev.props.get(p, "") for p in prop_names]):
        if raw and raw.strip().upper() not in (dev.device.upper(), "RES", "RESISTOR", "CAP", "CAPACITOR", "IND", "INDUCTOR"):
            v = _n(dev.text(raw), dev.variables)
            if isinstance(v, str):
                raise UnsupportedDevice(f"{dev.ref} ({dev.device}): value {v!r} is not a number")
            if v is not None:
                return v
    raise DeviceError(f"{dev.ref} ({dev.device}): no value")


# ---------------------------------------------------------------------------------------------------------------- primitive handlers

Handler = Callable[[_Dev], list[Spec]]
PRIMITIVES: dict[str, Handler] = {}                                             # DSIM/ISIS primitive type -> handler
NAMES: dict[str, str] = {}                                                      # device name (upper) -> primitive type


def primitive(*types: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        for t in types:
            PRIMITIVES[t] = fn
        return fn
    return deco


NONSIM = {"NULL", "NULLL", "RTVPROBE", "RTDPROBE", "RTIPROBE", "RTVCHECK", "RTICHECK", "VUMETER", "RTIBREAK", "RTVBREAK"}
NONSIM_NAMES = {"VPROBE", "IPROBE", "APROBE", "DPROBE", "VSINE!", "DEFAULT", "INPUT", "OUTPUT", "BIDIR", "POWER", "GROUND", "BUS", "LABEL", "TEXT", "GRAPH"}


@primitive("RESISTOR", "CAPACITOR", "INDUCTOR")
def _rcl(d: _Dev) -> list[Spec]:
    kind = {"RESISTOR": "R", "CAPACITOR": "C", "INDUCTOR": "L"}[_prim_type(d)]
    a, b = d.pair()
    ic = _n(d.props.get("IC"))
    return [_part(d.ref, kind, [a, b], value=_value(d, "VALUE"), ic=ic if kind in ("C", "L") else None)]


@primitive("BATTERY")
def _battery(d: _Dev) -> list[Spec]:
    return [_part(d.ref, "V", [d.need("+"), d.need("-")], value=_value(d, "VALUE"))]


@primitive("POT")
def _pot(d: _Dev) -> list[Spec]:
    """Hardware-grade pot: two resistors split by the wiper (pin 3), as ISIS does (RV1_R1 pin 1..wiper = (1-pos)*R, RV1_R2 wiper..pin 2 = pos*R)."""
    if d.props.get("LAW", "0") not in ("", "0"):
        raise UnsupportedDevice(f"{d.ref}: potentiometer law {d.props['LAW']} (only linear)")
    total = isis_number(d.text(d.value or d.props.get("VALUE", "")))
    if total is None:
        raise DeviceError(f"{d.ref}: pot has no numeric value")
    pos = isis_number(d.props.get("POS"))
    frac = pos / 100.0 if pos is not None else (isis_number(d.props.get("STATE")) or 5.0) / 10.0
    frac = min(max(frac, 0.0), 1.0)
    rmin = isis_number(d.props.get("RMIN")) or 1e-3
    p1, p2, w = d.need("1"), d.need("2"), d.need("3")
    return [_part(f"{d.ref}_R1", "R", [p1, w], value=_n(f"{max((1 - frac) * total, rmin):.12g}")),
            _part(f"{d.ref}_R2", "R", [w, p2], value=_n(f"{max(frac * total, rmin):.12g}"))]


@primitive("RTSWITCH")
def _rtswitch(d: _Dev) -> list[Spec]:
    """Run-time switch / LDR / button: a resistor whose value is R(STATE) (state = the saved ``STATE`` property)."""
    state = int(isis_number(d.props.get("STATE")) or 0)
    raw = d.props.get(f"R({state})")
    if raw is None:
        raise DeviceError(f"{d.ref}: no R({state}) for STATE={state}")
    a, b = d.pair()
    return [_part(d.ref, "R", [a, b], value=_n(d.text(raw)))]


_DEVICE_PINS = {"DIODE": ("D", ("A", "K")), "NPN": ("Q", ("C", "B", "E")), "PNP": ("QP", ("C", "B", "E")), "NJFET": ("J", ("D", "G", "S")),
                "PJFET": ("JP", ("D", "G", "S")), "NMOSFET": ("M", ("D", "G", "S", "B")), "PMOSFET": ("MP", ("D", "G", "S", "B"))}
_FALLBACK_MODEL = {"DIODE": "DIODE", "NPN": "NPN", "PNP": "PNP"}
_INSTANCE_PARAMS = ("L", "W", "AD", "AS", "PD", "PS", "AREA", "M")


@cache
def _resolves(name: str) -> bool:
    try:
        return library.resolve(name) is not None
    except Exception:
        return False


def _model_name(d: _Dev, fallback: str | None) -> str | None:
    """The model name the spec translator can resolve: SPICEMODEL, then the library device (value, then device name), then the MODEL token.  When none
    resolves (library not installed / custom model) the most specific name is kept, generic parts (NPN, DIODE ...) fall back to the builtin model."""
    spice = d.props.get("SPICEMODEL", "").split(",")[0].strip().strip('"')
    value = d.value if d.value.upper() != d.device.upper() else ""
    model_tok = d.props.get("MODEL", "").split(",")[0].strip()
    cands = [c for c in (spice, value, d.device, model_tok) if c and not c.startswith("<")]
    for c in cands:
        if _resolves(c):
            return c
    specific = [c for c in (spice, value, "" if d.device.upper() in _DEVICE_PINS else d.device, model_tok) if c and not c.startswith("<")]
    return specific[0] if specific else fallback


@primitive("DIODE", "NPN", "PNP", "NJFET", "PJFET", "NMOSFET", "PMOSFET")
def _semiconductor(d: _Dev) -> list[Spec]:
    t = _prim_type(d)
    kind, pins = _DEVICE_PINS[t]
    nodes = [d.need(p) for p in (pins[:3] if kind in ("M", "MP") else pins)]
    if kind in ("M", "MP"):
        nodes.append(d.net("B") or nodes[2])                                        # 3-pin MOSFETs have the bulk tied to the source inside
    params = {k.lower(): _n(d.props[k]) for k in _INSTANCE_PARAMS if k in d.props and kind in ("M", "MP", "J", "JP")}
    return [_part(d.ref, kind, nodes, model=_model_name(d, _FALLBACK_MODEL.get(t)), params={k: v for k, v in params.items() if v is not None})]


@primitive("SUBCKT")
def _subckt(d: _Dev) -> list[Spec]:
    name = d.props.get("SPICEMODEL", "").split(",")[0].strip().strip('"')
    pins = [p.strip() for p in d.props.get("SPICEPINS", "").strip('"').split(",") if p.strip()]
    if not name or not pins:
        raise DeviceError(f"{d.ref}: SUBCKT without SPICEMODEL/SPICEPINS")
    nodes = [d.need(p) for p in pins]
    return [_part(d.ref, "X", nodes, subckt=name)]


_SOURCE_WAVES: dict[str, tuple[str, dict[str, str]]] = {                      # device -> (wave type, spec key -> ISIS property)
    "SINE": ("sine", {"offset": "VO", "amp": "VA", "freq": "FREQ", "delay": "TD", "damping": "THETA", "phase": "PHASE"}),
    "PULSE": ("pulse", {"low": "V1", "high": "V2", "delay": "TD", "rise": "TR", "fall": "TF", "width": "PW", "period": "PER"}),
    "EXP": ("exp", {"low": "V1", "high": "V2", "td1": "TD1", "tau1": "TAU1", "td2": "TD2", "tau2": "TAU2"}),
    "SFFM": ("sffm", {"offset": "VO", "amp": "VA", "carrier": "FC", "signal": "FS", "index": "MDI"}),
    "PWLIN": ("pwl", {}),
}


@primitive("VSOURCE", "VSINE", "VPULSE", "VEXP", "VSFFM", "VPWLIN", "ISOURCE", "ISINE", "IPULSE", "IEXP", "ISFFM", "IPWLIN")
def _source(d: _Dev) -> list[Spec]:
    t = _prim_type(d)
    kind, w = t[0], t[1:]
    plus, minus = d.need("+"), d.need("-")
    if w == "SOURCE":
        # ISIS's DC ISOURCE pushes current into '+'; the spec's I nodes are SPICE order (n+ -> source -> n-), see elements._source
        return [_part(d.ref, kind, [minus, plus] if kind == "I" else [plus, minus], value=_value(d, "VALUE"))]
    wtype, keys = _SOURCE_WAVES[w]
    wave: dict[str, Any] = {"type": wtype}
    for k, prop in keys.items():
        v = _n(d.text(d.props[prop])) if prop in d.props else None
        if v is not None:
            wave[k] = v
    if wtype == "pwl":
        pts = sorted(((isis_number(m.group(1)), isis_number(v)) for k, v in d.props.items() if (m := re.fullmatch(r"V\((.+)\)", k))), key=lambda p: p[0] or 0.0)
        if not pts:
            raise DeviceError(f"{d.ref}: PWL source without V(t)=v properties")
        wave["points"] = [[t_, v_] for t_, v_ in pts]
    return [_part(d.ref, kind, [plus, minus], wave=wave)]


@primitive("VCISOURCE", "VCCS")
def _vccs(d: _Dev) -> list[Spec]:
    """Voltage-controlled current source (pins + - P N).  ISIS pushes the current into '+', the spec's G is SPICE order (n+ -> source -> n-): swap."""
    return [_part(d.ref, "G", [d.need("-"), d.need("+"), d.need("P"), d.need("N")], value=_value(d, "VALUE"))]


@primitive("VCVS")
def _vcvs(d: _Dev) -> list[Spec]:
    return [_part(d.ref, "E", [d.need("+"), d.need("-"), d.need("P"), d.need("N")], value=_value(d, "VALUE"))]


@primitive("RTDSTATE")
def _logicstate(d: _Dev) -> list[Spec]:
    """LOGICSTATE toggle: constant logic level, modelled as a one-bit pattern generator."""
    high = (d.props.get("STATE", "0") or "0").strip() not in ("0", "")
    return [_part(d.ref, "DGEN", [d.need("Q0", "Q", "1")], pattern="1" if high else "0", init="HIGH" if high else "LOW", width=1)]


# --- digital primitives -------------------------------------------------------------------------------------------------

_GATE_RE = re.compile(r"(AND|NAND|OR|NOR|XOR|XNOR)_(\d+)")
_DELAY_KEYS = re.compile(r"T(?:D|G)[A-Z0-9]*")
_NOT_DSIM = {"PULSE", "CLOCK", "PATTERN", "DIGITAL", "SPICE", "LOGICSTATE", "LOGICPROBE", "LOGICTOGGLE", "DCLOCK", "DPATTERN"}
_NOT_PARAMS = {"PRIMITIVE", "PACKAGE", "PRIMTYPE", "EID", "MODFILE", "MODEL", "ITFMOD", "VOLTAGE", "VALUE", "STATE", "PINSWAP", "GROUP", "DESC", "CAT", "SUBCAT", "VCC", "VDD"}


def _pin_order(name: str) -> tuple[int, str]:
    m = re.search(r"(\d+)$", name)
    return (int(m.group(1)) if m else -1, name)


def _out_pin(name: str, dev_types: Mapping[str, str]) -> bool:
    return dev_types.get(name) == "OP" if name in dev_types else bool(re.fullmatch(r"!?\$?Q\$?[0-9A-Z]*|Y|OUT", name.upper())) and not name.upper().startswith("D")


def _digital_generic(d: _Dev, prim: str) -> list[Spec]:
    params: dict[str, Any] = {}
    delays: dict[str, Any] = {}
    for k, v in d.props.items():
        if k in _NOT_PARAMS or v in ("?", "[NULL]", "") or "<" in v:
            continue
        (delays if _DELAY_KEYS.fullmatch(k) else params)[k] = _n(v) if isis_number(v) is not None else v
    gm = _GATE_RE.fullmatch(prim)
    simple = not params and not any(p.upper().startswith("INVERT") for p in d.props)
    ins = sorted((p for p in d.pins if not _out_pin(p, d.pin_types)), key=_pin_order)
    outs = [p for p in d.pins if _out_pin(p, d.pin_types)]
    if simple and len(outs) == 1 and ((gm and len(ins) == int(gm.group(2))) or (prim in ("INVERTER", "BUFFER") and len(ins) == 1)):
        g = gm.group(1) if gm else {"INVERTER": "NOT", "BUFFER": "BUF"}[prim]
        return [_part(d.ref, "GATE", [d.pins[p] for p in ins] + [d.pins[outs[0]]], gate=g, delays=delays)]
    names = list(d.pins)
    pins = [f"{p}:{d.pin_types.get(p) or ('OP' if _out_pin(p, d.pin_types) else 'IP')}" for p in names]
    return [_part(d.ref, "DIGITAL", [d.pins[p] for p in names], primitive=prim, pins=pins, params=params, delays=delays)]


# ---------------------------------------------------------------------------------------------------------------- classification

def _prim_type(d: _Dev) -> str:
    """Primitive type of a component: ``PRIMITIVE=dom,TYPE``, else PRIMTYPE, else the device-name table."""
    typ = d.dom_type[1]
    if typ in PRIMITIVES or typ in NONSIM or d.dom_type[0] == "DIGITAL" and typ:
        return typ
    pt = d.props.get("PRIMTYPE", "").upper()
    if pt in PRIMITIVES:
        return pt
    n = d.device.upper()
    if d.dom_type[0] == "DIGITAL" and not d.dom_type[1] and re.fullmatch(r"[A-Z][A-Z0-9]*(?:_\d+)*", n) and n not in _NOT_DSIM:
        return n                                                                    # domain-only {PRIMITIVE=DIGITAL}: the device name is the DSIM primitive
    return _name_type(d.device)


_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:RES|RESISTOR|PULLUP|PULLDOWN)"), "RESISTOR"), (re.compile(r"(?:CAP|CAPACITOR)(?:[- ][A-Z0-9]+)?"), "CAPACITOR"),
    (re.compile(r"(?:IND|INDUCTOR)(?:[- ][A-Z0-9]+)?"), "INDUCTOR"), (re.compile(r"(?:BATTERY|CELL)"), "BATTERY"),
    (re.compile(r"(?:DIODE|NPN|PNP|NJFET|PJFET|NMOSFET|PMOSFET|VCCS|VCVS|VCISOURCE|[VI](?:SOURCE|SINE|PULSE|EXP|SFFM|PWLIN))"), ""),
    (re.compile(r"POT-HG"), "POT"), (re.compile(r"LOGICSTATE"), "RTDSTATE"),
]


def _name_type(device: str) -> str:
    n = device.upper()
    for rx, typ in _NAME_PATTERNS:
        if rx.fullmatch(n):
            return typ or n
    return ""


def _led(d: _Dev) -> list[Spec]:
    cands = [d.device, "DIODE-LED"]
    model = next((c for c in cands if _resolves(c)), "DIODE-LED")
    return [_part(d.ref, "D", [d.need("A"), d.need("K")], model=model)]


def _map(d: _Dev) -> list[Spec]:
    dom, typ = d.dom_type
    if d.device.upper() in NONSIM_NAMES or typ in NONSIM or dom in ("NULL", "NULLL"):
        return []
    t = _prim_type(d)
    if t in PRIMITIVES:
        return PRIMITIVES[t](d)
    if dom == "DIGITAL" and t and "MODDLL" not in d.props and "MODFILE" not in d.props:
        return _digital_generic(d, t)
    if re.fullmatch(r"LED(?:-[A-Z0-9]+)?", d.device.upper()) or d.props.get("MODFILE", "").upper() in ("LEDA", "LEDD"):
        return _led(d)
    mf = d.props.get("MODFILE", "")
    if mf and d.depth < 4:
        text = mdf_text(mf)
        if text is not None:
            return _expand_mdf(d, parse_mdf(text))
    if d.props.get("SPICEMODEL") and d.props.get("SPICEPINS"):
        return _subckt(d)
    if d.props.get("MODDLL"):
        raise UnsupportedDevice(f"{d.ref} ({d.device}): VSM model {d.props['MODDLL']} (no netlist equivalent)")
    if mf:
        raise UnsupportedDevice(f"{d.ref} ({d.device}): model file {mf} not found in DATA/MODELS/*.LML (Proteus not installed?)")
    raise UnsupportedDevice(f"{d.ref} ({d.device}): no mapping (PRIMITIVE={d.props.get('PRIMITIVE', '-')})")


# ---------------------------------------------------------------------------------------------------------------- model-description expansion

_POWER_NAMES = {"GND": "GND", "VSS": "GND", "0": "GND", "VCC": "VCC", "VDD": "VCC"}


def _mapping_row(d: _Dev, mdf: _Mdf) -> dict[str, str]:
    if not mdf.mappings:
        return {}
    keys = {"VALUE": d.value or d.device, "VOLTAGE": d.props.get("VOLTAGE", "5V")}

    def norm(s: str) -> str:
        v = isis_number(s)
        return f"{v:.9g}" if v is not None else _fold(s)

    for cand in ([d.value] if d.value else []) + [d.device]:
        want = "+".join(norm(cand if k == "VALUE" else d.props.get(k, keys.get(k, ""))) for k in mdf.key_spec)
        for key, row in mdf.mappings.items():
            if "+".join(norm(x) for x in key.split("+")) == want:
                return row
    return {}


def _expand_mdf(d: _Dev, mdf: _Mdf) -> list[Spec]:
    """Flatten a ``*.MDF`` description the way ISIS does: sub-parts named ``<ref>_<subref>``, internal nets private to the component."""
    row = _mapping_row(d, mdf)
    variables: dict[str, str] = {**{k: v for k, v in mdf.defaults.items() if v != "?"}, **{k: v for k, v in d.props.items() if k not in ("PRIMITIVE",)}, **row}
    variables["VALUE"] = d.value

    def subst(s: str) -> str | None:
        def one(m: re.Match[str]) -> str:
            v = variables.get(m.group(1).upper())
            if v is None or v in ("[NULL]", "?"):
                raise KeyError(m.group(1))
            return v
        try:
            return re.sub(r"<([^<>]+)>", one, s)
        except KeyError:
            return None

    sub_pins: dict[str, dict[str, str]] = {}
    sub_types: dict[str, dict[str, str]] = {}
    for g in mdf.nets:
        if g.terminals:
            net = next((n for n in (d.net(name) for name, _ in g.terminals) if n is not None), None)
            if net is None and g.terminals[0][1] in ("PT", "GT"):
                net = _POWER_NAMES.get(g.terminals[0][0].upper())
        else:
            net = _POWER_NAMES.get(g.name.upper()) or f"{d.ref}.{g.name}"
        if net is None:                                                            # unconnected terminal: a private floating net
            net = f"{d.ref}.{g.name}"
        for sref, ptype, pin in g.pins:
            sub_pins.setdefault(sref, {})[pin] = net
            sub_types.setdefault(sref, {})[pin] = ptype
    out: list[Spec] = []
    for sref, lib, value, props in mdf.parts:
        sprops: dict[str, str] = {}
        for k, v in props.items():
            r = subst(v)
            if r is not None and r not in ("?", "[NULL]"):
                sprops[k] = r
        sval = subst(value) if value else ""
        if sval is None:
            raise UnsupportedDevice(f"{d.ref}: model {d.props.get('MODFILE')} needs a value for {value!r}")
        for dsn_k in ("PACKAGE", "EID"):
            sprops.pop(dsn_k, None)
        sub = _Dev(f"{d.ref}_{sref}", lib, sval, {**{k: v for k, v in lib_component(lib).items() if k != "VALUE"}, **sprops}, sub_pins.get(sref, {}), sub_types.get(sref, {}),
                   d.variables, d.depth + 1)
        out.extend(_map_sub(sub, lib))
    return out


def _map_sub(sub: _Dev, lib: str) -> list[Spec]:
    """Sub-part of a model description: its LIB column is the primitive (RES, CAPACITOR, RTSWITCH, DTFF, NAND_2 ...)."""
    dom, typ = sub.dom_type
    up = lib.upper()
    if up in ("RES", "RESISTOR"):
        up = "RESISTOR"
    elif up in ("CAP",):
        up = "CAPACITOR"
    elif up in ("IND",):
        up = "INDUCTOR"
    if dom == "DIGITAL" and "MODDLL" not in sub.props:
        return _digital_generic(sub, up if not typ else typ)
    if up in PRIMITIVES:
        sub.props["PRIMITIVE"] = f"{dom or 'ANALOGUE'},{up}"
        return PRIMITIVES[up](sub)
    if up in NONSIM:
        return []
    if "MODDLL" in sub.props:
        raise UnsupportedDevice(f"{sub.ref}: model DLL {sub.props['MODDLL']}")
    raise UnsupportedDevice(f"{sub.ref}: primitive {lib} has no spec equivalent")


# ---------------------------------------------------------------------------------------------------------------- public API

def to_spec_parts(ref: str, device: str, value: str, props: Props, pin_nets: Mapping[str, str], *, variables: Mapping[str, str] | None = None,
                  strict: bool = True) -> list[Spec]:
    """One schematic component -> its circuit-spec parts (``[]`` for non-simulated ones).  Each dict validates against `models.Part`.

    ``variables`` supplies design-level ``<NAME>`` placeholders used in values.  With ``strict=False`` an `UnsupportedDevice` gives ``[]``.
    """
    inst = parse_props(props)
    value = inst.get("VALUE") or value                                               # old (v2) files keep the value in a VALUE property
    merged = {**lib_component(device), **inst}
    if "PRIMITIVE" in inst and "," not in inst["PRIMITIVE"] and "," in lib_component(device).get("PRIMITIVE", ""):
        merged["PRIMITIVE"] = lib_component(device)["PRIMITIVE"]                       # instance says only the domain, the library knows the type
    d = _Dev(ref, device, (value or "").strip(), merged, dict(pin_nets), {}, {k.upper(): v for k, v in (variables or {}).items()})
    try:
        return _map(d)
    except UnsupportedDevice:
        if strict:
            raise
        return []


def to_spec_part(ref: str, device: str, value: str, props: Props, pin_nets: Mapping[str, str], *, variables: Mapping[str, str] | None = None,
                 strict: bool = True) -> Spec | None:
    """`to_spec_parts` for components that map to exactly one part: the dict, or None for non-simulated components.  Pots, crystals and digital ICs that
    expand to several parts raise `DeviceError`: use `to_spec_parts`."""
    parts = to_spec_parts(ref, device, value, props, pin_nets, variables=variables, strict=strict)
    if len(parts) > 1:
        raise DeviceError(f"{ref} ({device}) expands to {len(parts)} spec parts: use to_spec_parts")
    return parts[0] if parts else None


@dataclass
class SpecResult:
    parts: list[Spec] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)                           # non-simulated components (refs)
    unsupported: dict[str, str] = field(default_factory=dict)                  # ref -> reason

    def as_spec(self, **extra: Any) -> dict[str, Any]:
        return {"parts": self.parts, **extra}


NetsArg = Mapping[str, Mapping[str, str]] | Iterable[tuple[str, str, str]]


def _group_nets(nets: NetsArg) -> dict[str, dict[str, str]]:
    if isinstance(nets, Mapping):
        return {str(k): dict(v) for k, v in nets.items()}
    out: dict[str, dict[str, str]] = {}
    for ref, pin, net in nets:                                                  # dsn_nets.named_pins() triples
        out.setdefault(ref, {})[pin] = net
    return out


def connect_groups(props: Props) -> list[list[str]]:
    """``{CONNECT=1,2}`` / ``{CONNECT=1A,1B;2A,2B}`` of jumpers and connectors (``PRIMITIVE=NULL``): groups of pin names that are one node."""
    raw = parse_props(props).get("CONNECT", "")
    return [[p.strip() for p in g.split(",") if p.strip()] for g in raw.split(";") if g.strip()]


def _merge_nets(parts: list[Spec], groups: list[list[str]]) -> None:
    """Rename nets so that every CONNECT group is a single net (ground names win, else the first one)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for grp in groups:
        for other in grp[1:]:
            a, b = find(grp[0]), find(other)
            if a != b:
                keep, drop = (b, a) if b.upper() in ("GND", "0") else (a, b)
                parent[drop] = keep
    for p in parts:
        p["nodes"] = [find(n) if n in parent else n for n in p["nodes"]]


def spec_from_parts(parts: Iterable[tuple[str, str, str, Props]], nets: NetsArg, *, variables: Mapping[str, str] | None = None) -> SpecResult:
    """Translate (ref, device, value, props) components with ``nets`` = {ref: {pin name: net}} or [(ref, pin name, net)] triples.  Jumpers
    (``CONNECT=`` groups of non-simulated parts) merge their nets."""
    by_ref = _group_nets(nets)
    res = SpecResult()
    merges: list[list[str]] = []
    for ref, device, value, props in parts:
        pn = by_ref.get(ref, {})
        for grp in connect_groups(props):
            nn = [pn[p] for p in grp if p in pn]
            if len(nn) > 1:
                merges.append(nn)
        try:
            got = to_spec_parts(ref, device, value, props, pn, variables=variables)
        except UnsupportedDevice as e:
            res.unsupported[ref] = str(e)
            continue
        except DeviceError as e:
            res.unsupported[ref] = f"error: {e}"
            continue
        (res.parts.extend(got) if got else res.ignored.append(ref))
    if merges:
        _merge_nets(res.parts, merges)
    return res


def _cdb_v2_components(d: bytes) -> list[tuple[str, str, str, str]]:
    """Version-2 ROOT.CDB (the old Graph Based Simulation samples): element table (u32 n, {serial, sheet, group, s8 ref, pins, blob, u32 part, i32 unit}),
    then (1, 1, 1, n_parts) and parts {u32 idx, 2 x u32, s8 ref, s8 value, s8 device, s8 package, u32 L, text[L-5], 0}."""
    def s8(p: int) -> tuple[str, int]:
        n = d[p]
        if p + 1 + n > len(d):
            raise ValueError("eof")
        return d[p + 1:p + 1 + n].decode("latin1"), p + 1 + n

    for start in range(8, min(len(d) - 4, 400)):
        n = struct.unpack_from("<I", d, start)[0]
        if not 0 < n < 5000:
            continue
        try:
            p = start + 4
            elems: list[tuple[str, int]] = []
            for _ in range(n):
                ref, p = s8(p + 12)
                npin = struct.unpack_from("<I", d, p)[0]
                p += 4
                if npin > 4096:
                    raise ValueError("npins")
                for _i in range(npin):
                    _, p = s8(p)
                    _, p = s8(p)
                bl = struct.unpack_from("<I", d, p)[0]
                if bl > len(d):
                    raise ValueError("blob")
                elems.append((ref, struct.unpack_from("<I", d, p + 4 + bl)[0]))
                p += 4 + bl + 8
            if struct.unpack_from("<3I", d, p) != (1, 1, 1):
                raise ValueError("no part table")
            n2 = struct.unpack_from("<I", d, p + 12)[0]
            p += 16
            parts: dict[int, tuple[str, str, str]] = {}
            refs: dict[int, str] = {}
            for _ in range(n2):
                idx = struct.unpack_from("<I", d, p)[0]
                pref, p = s8(p + 12)
                value, p = s8(p)
                device, p = s8(p)
                _, p = s8(p)
                ln = struct.unpack_from("<I", d, p)[0]
                text = d[p + 4:p + 4 + ln - 5].decode("latin1")
                p += 4 + ln - 4
                parts[idx] = (value, device, text)
                refs[idx] = pref
            out = [(ref, parts[i][1], parts[i][0], parts[i][2]) for ref, i in elems if i in parts]
            used = {i for _, i in elems}
            return out + [(refs[i], v[1], v[0], v[2]) for i, v in parts.items() if i not in used]
        except (ValueError, IndexError, struct.error):
            continue
    raise ValueError("no CDB v2 element/part table found")


def read_components(cdb_bytes: bytes) -> list[tuple[str, str, str, str]]:
    """ROOT.CDB (any version) -> [(element ref, device, value, props text)]; elements without a part record (terminals, wires) are left out.  A multi-unit
    package gives one entry per unit (``U6:A``) sharing the part's properties."""
    from ._gen import cdbfmt
    try:
        c = cdbfmt.parse_cdb(cdb_bytes)
    except ValueError:
        return _cdb_v2_components(cdb_bytes)
    parts = {p["idx"]: p for p in c["parts"]}
    out = [(e["ref"], parts[e["part"]]["device"], parts[e["part"]]["value"], parts[e["part"]]["props"]) for e in c["elements"] if e["part"] in parts]
    used = {e["part"] for e in c["elements"]}
    return out + [(p["ref"], p["device"], p["value"], p["props"]) for i, p in parts.items() if i not in used]


def spec_from_cdb(cdb_bytes: bytes, nets: NetsArg, *, variables: Mapping[str, str] | None = None) -> SpecResult:
    """ROOT.CDB (version 2-5) + pin nets (``dsn_nets.named_pins`` triples or {ref: {pin: net}}) -> `SpecResult`; every element is translated with its own
    pins and the properties of its part."""
    return spec_from_parts(read_components(cdb_bytes), nets, variables=variables)
