"""Design model (parts + wires in thou) and the ROOT.DSN writer built on `pdsfmt.assemble`."""
from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import pdsfmt
from .scan import THOU
from .templates import KINDS, Template, seed_components, seed_dsn, templates


@dataclass(eq=False)
class Part:
    kind: str
    x: int
    y: int
    ref: str | None = None
    state: int = 0
    flip: int = 0                       # bit0 mirror X, bit1 mirror Y
    serial: int = 0
    index: int = -1                     # position in the object stream (set by build_dsn)

    @property
    def template(self) -> Template:
        return templates()[self.kind]


@dataclass(eq=False)
class Wire:
    pts: list[tuple[int, int]]
    a: tuple[Part, str]
    b: tuple[Part, str]
    net: str = ""


@dataclass
class Design:
    parts: list[Part] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)

    def add(self, kind: str, x: int, y: int, ref: str | None = None, state: int = 0, flip: int = 0) -> Part:
        if kind not in templates():
            raise ValueError(f"kind {kind!r} unsupported; one of {sorted(templates())}")
        part = Part(kind, x, y, ref, state, flip)
        self.parts.append(part)
        return part

    def tip(self, part: Part, pin: str) -> tuple[int, int]:
        if pin not in part.template.pins:
            raise ValueError(f"{part.kind} has no pin {pin}; pins: {sorted(part.template.pins)}")
        return part.template.pin_tip(pin, part.x, part.y, part.flip)

    def wire(self, pts: list[tuple[int, int]], a: tuple[Part, str], b: tuple[Part, str], net: str = "") -> Wire:
        w = Wire(pts, a, b, net)
        self.wires.append(w)
        return w


def _patch_record(p: Part) -> tuple[bytearray, int, Template]:
    """Copy of the template record with this part's position, serial and reference text; returns (record, slot array offset, template)."""
    t = p.template
    b = bytearray(t.with_state(p.state).blob if p.kind == "LS" else t.blob)
    xo, yo, ser = list(t.xo), list(t.yo), t.serial_off
    if p.ref and t.ref:
        o, old = t.ref
        txt = p.ref.encode()
        d = len(txt) - old
        b[o - 1] = len(txt)
        b[o:o + old] = txt
        xo = [v + d if v >= o else v for v in xo]
        yo = [v + d if v >= o else v for v in yo]
        if ser >= o:
            ser += d
    dx, dy = p.x - t.x0, p.y - t.y0
    for o in xo:
        b[o:o + 4] = struct.pack("<i", (struct.unpack("<i", b[o:o + 4])[0] // THOU + dx) * THOU)
    for o in yo:
        b[o:o + 4] = struct.pack("<i", (struct.unpack("<i", b[o:o + 4])[0] // THOU + dy) * THOU)
    b[ser:ser + 4] = struct.pack("<I", p.serial)
    if p.flip:
        struct.pack_into("<H", b, xo[-1] + 10, p.flip)
    so = len(b) - 4 * t.npins
    b[so:] = b"\0" * (4 * t.npins)
    return b, so, t


def build_dsn(d: Design) -> bytes:
    """Write ROOT.DSN: the seed header, the object stream (parts, junctions, wires placed after the object that completes them) and the seed tail."""
    d.parts.sort(key=lambda p: KINDS.index(p.kind))
    for i, p in enumerate(d.parts):
        p.serial = 7 + i
    seed, comps = seed_dsn(), seed_components()
    pre = seed[:comps[0].start]
    items: list[dict[str, Any]] = []
    for p in d.parts:
        b, so, t = _patch_record(p)
        p.index = len(items)
        items.append({"blob": b, "slots_off": so, "nslots": t.npins, "kind": "COMPONENT"})
    ends: dict[tuple[int, str], list[tuple[Wire, str]]] = defaultdict(list)       # (id(part), pin) -> wire ends
    for w in d.wires:
        for e, (part, pin) in (("a", w.a), ("b", w.b)):
            ends[(id(part), pin)].append((w, e))
    final: list[dict[str, Any]] = []
    endpoint: dict[tuple[int, str], tuple[int, int]] = {}                        # (id(wire), 'a'|'b') -> (object, slot)
    byid = {id(p): p for p in d.parts}
    for (pid, pin), lst in ends.items():
        p = byid[pid]
        tip = d.tip(p, pin)
        slot = (p.index, p.template.pins[pin][0])
        if len(lst) == 1:
            w, e = lst[0]
            endpoint[(id(w), e)] = slot
            continue
        rem, prev = list(lst), slot                       # a pin with several wires gets junction objects (4 slots each) at its tip
        while True:
            ji = len(items)
            items.append(pdsfmt.make_junction(*tip))
            final.append({"a": prev, "b": (ji, 0), "pts": [tip, tip]})
            if len(rem) <= 3:
                for k, (w, e) in enumerate(rem):
                    endpoint[(id(w), e)] = (ji, 1 + k)
                break
            for k, (w, e) in enumerate(rem[:2]):
                endpoint[(id(w), e)] = (ji, 1 + k)
            rem, prev = rem[2:], (ji, 3)
    for w in d.wires:
        a, b = endpoint.get((id(w), "a")), endpoint.get((id(w), "b"))
        if not (a and b):
            raise ValueError(f"wire {w.net!r} has an unconnected end")
        final.append({"a": a, "b": b, "pts": w.pts})
    objs, _keys = pdsfmt.assemble(items, final, len(pre))
    last = seed.rfind(b"ISIS CIRCUIT FILE")
    tail = seed[last - 1:]
    delta = len(objs) - ((last - 1) - comps[0].start)
    new = bytearray(pre + objs + tail)
    first = seed.find(b"ISIS CIRCUIT FILE")
    v = struct.unpack("<I", new[first - 4:first])[0]
    new[first - 4:first] = struct.pack("<I", v + delta)
    j = bytes(new).find(struct.pack("<I", last), len(pre) + len(objs) + 1)
    if j < 0:
        raise ValueError("cannot patch the tail section pointer")
    new[j:j + 4] = struct.pack("<I", last + delta)
    return bytes(new)
