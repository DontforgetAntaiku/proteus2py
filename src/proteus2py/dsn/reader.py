"""Generic reader for the ISIS object stream of ROOT.DSN (Proteus 8, file versions 800..828).

Object layouts come from the Read() methods (vtable slot 0xf0) of the object classes in ISIS.DLL; a few fields the decompiler dropped (8 bytes after
graph types 0x2a/0x2e/0x81, 24 bytes after 0x2b) were found by experiment. 624/624 projects of the Proteus sample corpus parse to the end.
Wires are not separate objects: a wire body follows the object that completes a pair of equal link keys (see docs/PDSPRJ.md).
"""
# ruff: noqa
from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from typing import Any

from .._gen import pdsfmt
from .._gen.pdsfmt import Err, R, cts, dpkb, label

class V(R):
    ver = 828

def textblock(r):
    lim = 0x32 if r.ver >= 690 else (0x1e if r.ver >= 504 else 0x0f)
    return pdsfmt.textblock(r, lim)

def box(r): return [r.i32() for _ in range(4)]

def read_component(r, t):
    o = pdsfmt.Obj(type=t, kind='COMPONENT', _off=r.p-1)
    o['id'] = label(r); o['value'] = label(r); o['subckt'] = label(r)
    o['props'] = textblock(r)
    o['npins'] = r.u16(); o['device'] = r.pstr(); o['cts'] = cts(r)
    o['serial'] = r.u32(); o['aux'] = r.u32(); o['flags'] = r.u8()
    if r.ver > 0x32c: o['extra'] = r.u32()
    o['slots'] = [r.u32() for _ in range(o['npins'])]
    return o

def simple(kind, fn):
    def rd(r, t):
        o = pdsfmt.Obj(type=t, kind=kind, _off=r.p-1, slots=[])
        o['data'] = fn(r); return o
    return rd

def rd2(r): c = cts(r); return (c, textblock(r))
def rd5(r): b = box(r); n = r.u16(); return (b, n, label(r), label(r), textblock(r))
def rd6(r): return dpkb(r)
def rd7(r): return box(r)
def rd20(r): c = cts(r); return (c, dpkb(r))
def rd21(r):
    c = cts(r); x = r.i32(); y = r.i32(); n = r.u32(); return (c, x, y, r.raw(n))
def rd22(r): b = box(r); return (b, label(r))

READERS = dict(pdsfmt.READERS)
READERS.update({0: read_component, 8: read_component,
                2: simple('TEXT', rd2), 5: simple('BOXTEXT', rd5), 6: simple('PACKET', rd6), 7: simple('BOX', rd7),
                0x20: simple('PRIM', rd20), 0x21: simple('BLOB', rd21), 0x22: simple('BOXLABEL', rd22)})


def u16b(r): return r.u8() | (r.u8() << 8)

def trace(r):
    label(r); r.pstr()
    for _ in range(4): r.pstr(); r.pstr()
    r.u16(); el = r.u16(); n = r.u32()
    for _ in range(n):
        size = r.u32(); r.raw(size * el)

def traces(r):
    for _ in range(r.u16()):
        t = r.u8()
        if t > 4: raise Err('bad trace type %d' % t)
        trace(r)

def graph_base(r):
    r.pstr(); dpkb(r); r.pstr(); box(r); r.raw(16); r.raw(16); r.raw(32); label(r); label(r)
    r.u8(); r.u8()
    if r.ver > 0x2a7: r.u8(); r.u8()
    traces(r)

def g28(r):
    graph_base(r); r.u8(); r.u8()
    if r.ver > 0x1c1: r.u8()

def g29(r):
    graph_base(r)
    if r.ver > 0x1a5:
        r.pstr(); r.pstr(); r.u8(); r.raw(4)
    r.u8()

def rd28(r): g28(r); return None
def rd29(r): g29(r); return None
def rd2a(r): r.u8(); g28(r); r.u16(); r.raw(8)
def rd2b(r): r.u8(); g29(r); r.pstr(); r.u16(); r.raw(24)      # three doubles the decompiler lost (verified on Sweep.pdsprj)
def rd2c(r):
    r.u8(); g29(r)
    if r.ver > 0x1a5: r.u8(); r.u8()
def rd2d(r): r.u8(); g28(r); r.raw(32); r.u16(); r.u16(); r.u16()
def rd2e(r):
    r.u8(); g28(r); r.u16(); r.u16(); r.u16()
    if r.ver >= 0x19a: r.u16()
    r.raw(8)
def rd80(r):
    r.u8(); g28(r); r.pstr(); r.pstr(); r.raw(8); r.raw(8); r.u16(); r.pstr(); r.pstr(); r.raw(8); r.raw(8); r.u16()
def rd81(r): r.u8(); g29(r); r.raw(8)
def rd82(r): r.u8(); g28(r)
def rd83(r): r.u8(); g28(r); traces(r)

def probe_like(r):
    """types 0x30-0x32 and the generic 0x38+/0xb8+ class: CTS, u16, 2 strings, label, textblock, 2 bytes, then 2 link slots."""
    c = cts(r); r.u16(); r.pstr(); dev = r.pstr(); lab = label(r); tb = textblock(r); r.u8(); r.u8()
    return dict(cts=c, device=dev, label=lab, props=tb)

def probe_obj(generic):
    def rd(r, t):
        o = pdsfmt.Obj(type=t, kind='PROBE', _off=r.p-1)
        o.update(probe_like(r))
        o['slots'] = [r.u32(), r.u32()]                    # the two link slots are read at the end of FUN_1002c810
        if generic:
            o['flag'] = r.u8()
            if r.ver > 0x2d9: dpkb(r)
        return o
    return rd

for t_, fn in {0x28: rd28, 0x29: rd29, 0x2a: rd2a, 0x2b: rd2b, 0x2c: rd2c, 0x2d: rd2d, 0x2e: rd2e,
               0x80: rd80, 0x81: rd81, 0x82: rd82, 0x83: rd83}.items():
    READERS[t_] = simple('GRAPH', fn)
for t_ in (0x30, 0x31, 0x32): READERS[t_] = probe_obj(False)
for t_ in list(range(0x38, 0x42)) + list(range(0xb8, 0xc1)): READERS[t_] = probe_obj(True)



def header_version(d: bytes) -> int:
    return struct.unpack_from('<H', d, 167 + 2)[0]


@dataclass
class DsnResult:
    objects: list[dict[str, Any]]
    version: int
    error: tuple[Any, int, str] | None = None      # (object type | 'wire' | 'eof', offset, message) when the stream could not be read to its end
    ok: bool = field(init=False)

    def __post_init__(self) -> None:
        self.ok = self.error is None


def parse_dsn(d: bytes) -> DsnResult:
    r = V(d, pdsfmt.find_stream(d))
    r.ver = header_version(d)
    objs: list[dict[str, Any]] = []
    pending: dict[int, tuple[int, int]] = {}
    queue: list[tuple[tuple[int, int], tuple[int, int], int]] = []

    def reg(oi: int, si: int, key: int) -> None:
        if key == 0:
            return
        if key in pending:
            queue.append((pending.pop(key), (oi, si), key))
        else:
            pending[key] = (oi, si)
    while True:
        try:
            t = r.u8()
        except Exception:
            return DsnResult(objs, r.ver, ('eof', r.p, ''))
        if t == 0xff:
            return DsnResult(objs, r.ver)
        if t not in READERS:
            return DsnResult(objs, r.ver, (t, r.p - 1, 'no reader'))
        try:
            o = READERS[t](r, t)
        except (Err, struct.error, IndexError) as e:
            return DsnResult(objs, r.ver, (t, r.p, str(e)))
        objs.append(o)
        oi = len(objs) - 1
        for si, k in enumerate(o.get('slots', [])):
            reg(oi, si, k)
        while queue:
            a, b, key = queue.pop(0)
            try:
                w = pdsfmt.read_wire_body(r)
            except (Err, struct.error, IndexError) as e:
                return DsnResult(objs, r.ver, ('wire', r.p, str(e)))
            w['a'] = a
            w['b'] = b
            objs.append(w)


def read_pdsprj(path: Any) -> DsnResult:
    return parse_dsn(zipfile.ZipFile(path).read('ROOT.DSN'))
