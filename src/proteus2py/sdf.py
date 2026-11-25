"""Write ISIS SDF netlists ("ISIS SCHEMATIC DESCRIPTION FORMAT 8.0") that PROSPICE.EXE simulates headless.

Grammar learned from netlists ISIS writes itself (re/captures): *PROPERTIES key=value (options + analysis), *MODELDEFS
`NAME : P=V,...`, *PARTLIST `REF,LIB,VALUE,prop=..,PRIMITIVE=..`, *NETLIST `net,count` then `REF,PINTYPE,PIN`.
Generators (sources driving one net against ground) are PROBE-class parts `G..,VSINE!,<net>`; probes are `P..,APROBE|IPROBE`.
Values use ISIS notation (m = milli, M = mega).
"""
from __future__ import annotations

import binascii
import os
from dataclasses import dataclass, field

HEADER = """ISIS SCHEMATIC DESCRIPTION FORMAT 8.0
=====================================
Design:   {title}
Doc. no.: <NONE>
Revision: <NONE>
Author:   <NONE>
Created:  1/1/2026
Modified: 1/1/2026
"""

OPTIONS = {"ABSTOL": "1e-012", "BADMOS3": "No", "BYPASS": "Yes", "CHGTOL": "1e-014", "DEFAD": "0", "DEFAS": "0",
           "DEFL": "0.0001", "DEFW": "0.0001", "GLEAK": "1e-012", "GMIN": "1e-012", "GMINSTEPS": "10", "ITL1": "100",
           "ITL2": "50", "ITL4": "10", "MAXORD": "2", "METHOD": "Gear", "NOOPITER": "No", "OLDLIMIT": "No", "PIVREL": "0.001",
           "PIVTOL": "1e-013", "RELTOL": "0.0001", "SRCSTEPS": "10", "TDLOWER": "0.9", "TDSCALE": "1", "TDUPPER": "1.1",
           "TEMP": "27", "TNOM": "27", "TRTOL": "7", "TRYTOCOMPACT": "No", "VNTOL": "1e-006"}

# kind -> (LIB name, PRIMITIVE, PRIMTYPE or None, pin names in order of `nodes`)
KINDS = {
    "R": ("RES", "PASSIVE", None, ("1", "2")),
    "C": ("CAP", "PASSIVE", "CAPACITOR", ("1", "2")),
    "L": ("IND", "PASSIVE", "INDUCTOR", ("1", "2")),
    "D": ("DIODE", "ACTIVE", None, ("A", "K")),
    "Q": ("NPN", "ACTIVE", None, ("C", "B", "E")),          # nodes: collector, base, emitter
    "QP": ("PNP", "ACTIVE", None, ("C", "B", "E")),
    "J": ("NJF", "ACTIVE", None, ("D", "G", "S")),
    "JP": ("PJF", "ACTIVE", None, ("D", "G", "S")),
    "M": ("NMOS", "ACTIVE", None, ("D", "G", "S", "B")),
    "MP": ("PMOS", "ACTIVE", None, ("D", "G", "S", "B")),
}


def num(x) -> str:
    """Number or ISIS-notation string -> text for the netlist."""
    if isinstance(x, bool):
        raise TypeError("bool is not a value")
    if isinstance(x, (int, float)):
        return repr(float(x)) if not float(x).is_integer() or abs(x) >= 1e15 else str(int(x))
    return str(x)


_SPICE_MULT = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "mil": 25.4e-6, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}


def spice_number(x) -> float:
    """SPICE notation (case-insensitive: M = milli, MEG = mega) -> float. Library .MODEL values use this."""
    import re
    if isinstance(x, (int, float)):
        return float(x)
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*", str(x))
    if not m:
        raise ValueError(f"bad SPICE number {x!r}")
    suf = m.group(2).lower()
    if not suf:
        return float(m.group(1))
    for k in ("meg", "mil", "t", "g", "k", "m", "u", "n", "p", "f"):
        if suf.startswith(k):
            return float(m.group(1)) * _SPICE_MULT[k]
    return float(m.group(1))              # trailing unit letters (V, A, OHM ...)


def q(s: str) -> str:
    return f'"{s}"' if any(c in s for c in ' ,"') else s


@dataclass
class Part:
    ref: str
    line_lib: str
    value: str
    props: list[str]
    pins: list[tuple[str, str, str]]            # (net, pin type, pin name)


@dataclass
class Sdf:
    title: str = "proteus2py"
    partition: str = field(default_factory=lambda: binascii.hexlify(os.urandom(4)).decode().upper())
    parts: list[Part] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    props: dict[str, str] = field(default_factory=dict)
    nets: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    termtype: dict[str, str] = field(default_factory=dict)
    _n: int = 0
    _gen: int = 0

    def net(self, name: str) -> str:
        if name in ("0", "gnd", "GND"):
            name = "GND"
            self.termtype["GND"] = "GT"
        self.nets.setdefault(name, [])
        return name

    def connect(self, net: str, ref: str, ptype: str, pin: str) -> None:
        self.nets[self.net(net)].append((ref, ptype, pin))

    def add_part(self, ref: str, lib: str, value: str, props: list[str], pins: list[tuple[str, str, str]]) -> None:
        self.parts.append(Part(ref, lib, value, props, pins))
        for net, pt, pn in pins:
            self.connect(net, ref, pt, pn)

    def text(self) -> str:
        out = [HEADER.format(title=self.title)]
        pr = dict(OPTIONS)
        pr["PARTITION"] = self.partition
        pr.update(self.props)
        out.append(f"*PROPERTIES,{len(pr)}")
        out += [f"{k}={v}" for k, v in sorted(pr.items())]
        out += ["", f"*MODELDEFS,{len(self.models)}"]
        out += [f"{k} : {v}" for k, v in self.models.items()]
        out += ["", f"*PARTLIST,{len(self.parts)}"]
        for p in sorted(self.parts, key=lambda p: p.ref):
            out.append(",".join([p.ref, q(p.line_lib), q(p.value), *p.props]))
        out += ["", f"*NETLIST,{len(self.nets)}"]
        ids = {}
        for i, (n, pins) in enumerate(self.nets.items()):
            label = "GND" if n == "GND" else n
            ids[n] = label
            out.append(f"{label},{len(pins) + (1 if n in self.termtype else 0)}")
            if n in self.termtype:
                out.append(f"{label},{self.termtype[n]}")
            out += [",".join([r, t, q(pn)]) for r, t, pn in pins]
            out.append("")
        return "\n".join(out) + "\n"
