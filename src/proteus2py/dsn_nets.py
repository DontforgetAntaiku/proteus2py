"""Electrical nets of a Proteus ``.pdsprj`` without the GUI: union-find over the wire graph of ROOT.DSN.

Input is the object list of the DSN object-stream reader (dicts with ``kind``):

* ``COMPONENT``: ``serial``, ``id`` (label dict with ``text``), ``slots`` (one link key per library pin, in library order);
* ``JUNCTION``: 4 slots, electrically one node;
* ``TERMINAL``: ``name`` (``$TERGROUND``, ``$TERPOWER``, ``$TERDEFAULT``, ``$TERINPUT``, ``$TEROUTPUT``, ``$TERBIDIR``, ...), ``ttype``
  (13 = ground/power/chassis, 9 = default, 10 = input, 11 = output, 12 = bidirectional, 15 = sub-circuit port), ``label`` (its text);
* ``WIRE``: ``a`` / ``b`` = (object index, slot index) of both ends, ``labels`` (net labels placed on the wire), ``bus`` (1 = bus wire).

Naming rules: ground terminals (``$TERGROUND``) all join the global net ``GND``; power terminals with the same text join (an unnamed one is ``VCC``); default / input
/ output / bidirectional / port terminals with the same text join; wire labels with the same text join. Bus wires carry no pin
connections of their own and are ignored (ripper wires reach the bus only through their label).
Pins that no wire touches do not appear in any net.
"""
from __future__ import annotations

import struct
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from os import PathLike
from typing import Any

Obj = Mapping[str, Any]
Pin = tuple[int, int]                    # (index of the COMPONENT in the object list, pin index = slot index = library order)

GROUND = "GND"
POWER = "VCC"
TTYPE_NAMES = {9: "default", 10: "input", 11: "output", 12: "bidir", 13: "power/ground/chassis", 15: "port"}
SERIES_PROBES = ("$IPROBE",)             # current probes sit in series: their two slots are different nodes; every other PROBE object is one node


@dataclass
class Nets:
    """``nets``: net id -> pins; ``names``: net id -> every name the net carries (first = the preferred one)."""

    nets: dict[int, list[Pin]] = field(default_factory=dict)
    names: dict[int, list[str]] = field(default_factory=dict)
    probe_nets: dict[Pin, int] = field(default_factory=dict)     # (probe object, slot) -> net id (generators and voltage probes sit on a net)

    def name(self, net: int) -> str:
        n = self.names.get(net)
        return n[0] if n else "#%05d" % net

    def net_of(self, pin: Pin) -> int | None:
        if pin in self.probe_nets:
            return self.probe_nets[pin]
        for k, pins in self.nets.items():
            if pin in pins:
                return k
        return None

    def by_name(self, name: str) -> int | None:
        for k, ns in self.names.items():
            if name in ns:
                return k
        return None

    def partition(self) -> set[frozenset[Pin]]:
        return {frozenset(p) for p in self.nets.values()}


class _UF:
    def __init__(self) -> None:
        self.p: dict[Any, Any] = {}

    def find(self, x: Any) -> Any:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: Any, b: Any) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _text(label: Any) -> str:
    return (label.get("text") or "").strip() if isinstance(label, Mapping) else ""


def build_nets(objects: Sequence[Obj]) -> Nets:
    """Union-find over wire ends. Returns the nets in order of their first object, named per the module rules."""
    uf = _UF()

    def node(end: Pin) -> Any:
        oi, si = end
        o = objects[oi]
        k = o.get("kind")
        if k == "JUNCTION" or (k == "PROBE" and o.get("device") not in SERIES_PROBES):
            return (oi, -1)
        return (oi, si)

    wires: list[tuple[Any, list[str]]] = []
    for w in objects:
        if w.get("kind") != "WIRE" or w.get("bus") or "a" not in w or "b" not in w:
            continue
        a, b = node(w["a"]), node(w["b"])
        uf.union(a, b)
        wires.append((a, [t for t in (_text(lb) for lb in w.get("labels") or ()) if t]))

    # (rank, text) name claims per node; same-text claims of one family merge the nets
    claims: dict[Any, list[str]] = {}
    keyed: dict[tuple[str, str], Any] = {}          # (family, text) -> first node: the global-name merge

    def claim(n: Any, family: str, text: str) -> None:
        uf.find(n)
        k = (family, text)
        if k in keyed:
            uf.union(keyed[k], n)
        else:
            keyed[k] = n
        claims.setdefault(n, []).append(text)

    for oi, o in enumerate(objects):
        if o.get("kind") != "TERMINAL" or not o.get("slots"):
            continue
        n = (oi, 0)
        kind, text = str(o.get("name") or ""), _text(o.get("label"))
        if kind == "$TERGROUND" or (kind == "$TERCHASSIS" and not text):
            claim(n, "g", GROUND)
        elif kind == "$TERPOWER":
            claim(n, "p", text or POWER)               # an unnamed power terminal is the default rail
        elif kind == "$TERCHASSIS":
            claim(n, "p", text)
        elif text:
            claim(n, "t", text)
    for n, texts in wires:
        for t in texts:
            claim(n, "t", t)                         # wire labels and default terminals share one namespace

    # collect pins per root, after all merges
    groups: dict[Any, list[Pin]] = {}
    for oi, o in enumerate(objects):
        if o.get("kind") != "COMPONENT":
            continue
        for si in range(len(o.get("slots") or ())):
            if (oi, si) in uf.p:
                groups.setdefault(uf.find((oi, si)), []).append((oi, si))
    names_by_root: dict[Any, list[str]] = {}
    for n, texts in claims.items():
        lst = names_by_root.setdefault(uf.find(n), [])
        for t in texts:
            if t not in lst:
                lst.append(t)
    res = Nets()
    root_net: dict[Any, int] = {}
    for k, (root, pins) in enumerate(sorted(groups.items(), key=lambda kv: min(kv[1])), start=1):
        root_net[root] = k
        res.nets[k] = sorted(pins)
        nm = names_by_root.get(root, [])
        res.names[k] = sorted(nm, key=lambda s: (s != GROUND,)) if nm else []
    for oi, o in enumerate(objects):
        if o.get("kind") == "PROBE":
            for si in range(len(o.get("slots") or ())):
                n = node((oi, si))
                if n in uf.p and uf.find(n) in root_net:
                    res.probe_nets[(oi, si)] = root_net[uf.find(n)]
    return res


def _cdb_v2(d: bytes) -> list[dict[str, Any]]:
    """CDB version 2 (old samples): element table = u32 n, then n x {serial, sheet, group, s8 ref, u32 npins, npins x (s8 name, s8 number), u32 bloblen, blob, u32 part, i32 unit}."""
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
            p, els = start + 4, []
            for _ in range(n):
                serial, _sheet, _group = struct.unpack_from("<3I", d, p)
                ref, p = s8(p + 12)
                npin = struct.unpack_from("<I", d, p)[0]
                p += 4
                if npin > 4096:
                    raise ValueError("npins")
                pins = []
                for _i in range(npin):
                    a, p = s8(p)
                    b, p = s8(p)
                    pins.append((a, b))
                bl = struct.unpack_from("<I", d, p)[0]
                if bl > len(d):
                    raise ValueError("blob")
                p += 4 + bl + 8
                els.append({"serial": serial, "ref": ref, "pins": pins})
            if struct.unpack_from("<3I", d, p) != (1, 1, 1):          # the part table follows
                raise ValueError("no part table")
            return els
        except (ValueError, IndexError, struct.error):
            continue
    raise ValueError("no CDB element table found")


def read_cdb_pins(cdb: bytes) -> dict[int, tuple[str, list[tuple[str, str]]]]:
    """ROOT.CDB -> {component serial: (reference, [(pin name, pin number), ...] in library = slot order)}; version 2 files are handled too."""
    from ._gen import cdbfmt
    try:
        els = cdbfmt.parse_cdb(cdb)["elements"]
    except ValueError:
        els = _cdb_v2(cdb)
    return {e["serial"]: (e["ref"], [(str(a), str(b)) for a, b in e["pins"]]) for e in els}


def pins_of(o: Obj, info: Mapping[int, tuple[str, list[tuple[str, str]]]]) -> tuple[str, list[tuple[str, str]]]:
    """(reference, library pins) of one COMPONENT: the CDB element whose reference equals the DSN label (serials differ between CDB and DSN in old
    files), else the one with the same serial, else (label, [])."""
    ref = _text(o.get("id"))
    for r, pl in info.values():
        if ref and r == ref:
            return r, pl
    r, pl = info.get(o.get("serial") or -1, (ref, []))
    return (ref or r), pl


def named_pins(nets: Nets, objects: Sequence[Obj], cdb: bytes | None = None) -> list[tuple[str, str, str]]:
    """[(ref, pin name, net name)] for every connected component pin; names from ROOT.CDB when given, else ``#<slot+1>``."""
    try:
        info = read_cdb_pins(cdb) if cdb else {}
    except ValueError:                               # no known layout
        info = {}
    out: list[tuple[str, str, str]] = []
    for k, pins in nets.nets.items():
        for oi, si in pins:
            ref, plist = pins_of(objects[oi], info)
            out.append((ref or "#%d" % oi, plist[si][0] if si < len(plist) else "#%d" % (si + 1), nets.name(k)))
    return sorted(out)


def nets_of_project(path: str | PathLike[str], objects: Sequence[Obj] | None = None) -> tuple[Nets, list[tuple[str, str, str]]]:
    """Convenience: read the project zip, build nets (objects from ``objects`` or the generic ``_gen.pdsfmt`` reader) and name the pins."""
    z = zipfile.ZipFile(path)
    dsn = z.read("ROOT.DSN")
    if objects is None:
        from ._gen import pdsfmt
        objects = pdsfmt.parse_objects(dsn)[0]
    n = build_nets(objects)
    cdb = z.read("ROOT.CDB") if "ROOT.CDB" in z.namelist() else None
    return n, named_pins(n, objects, cdb)
