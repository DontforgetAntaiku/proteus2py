"""Proteus SPICE model libraries (DATA/MODELS/*.SML): name -> .MODEL / .SUBCKT text.

SML layout: "SPICE LIBRARY" + u32 header; at 0x10 u32 entry-table size?, entries of 0x20 bytes from 0x20:
name[20] (NUL padded), u32 offset @+0x14, u32 timestamp, u32 aux. At the offset: u32 text length + plain SPICE text.
"""
from __future__ import annotations

import re
import struct
from functools import cache
from pathlib import Path

from . import workspace as ws

MODELS_REL = "drive_c/Program Files (x86)/Labcenter Electronics/Proteus 8 Professional/DATA/MODELS"


def models_dir() -> Path:
    return ws.home() / "prefix-pristine" / MODELS_REL


@cache
def _index() -> dict[str, tuple[Path, int, int]]:
    idx: dict[str, tuple[Path, int, int]] = {}
    d = models_dir()
    if not d.is_dir():
        return idx
    for f in sorted(d.glob("*.SML")):
        b = f.read_bytes()
        if not b.startswith(b"SPICE LIBRARY") or len(b) < 0x20:
            continue
        p = 0x20
        while p + 0x20 <= len(b):
            name = b[p:p + 0x14].split(b"\0")[0].decode("latin-1")
            off, _ts, ln = struct.unpack_from("<III", b, p + 0x14)
            if not name or not name.isprintable() or off == 0 or off + 4 > len(b):
                break
            idx.setdefault(name.upper(), (f, off, ln))
            p += 0x20
    return idx


def names() -> list[str]:
    return sorted(_index())


def get(name: str) -> str | None:
    e = _index().get(name.upper())
    if not e:
        return None
    f, off, _ = e
    b = f.read_bytes()
    n = struct.unpack_from("<I", b, off)[0]                  # each record is u32 length + text
    return b[off + 4:off + 4 + n].decode("latin-1")


def _joined(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if ln.startswith("+") and out:
            out[-1] += " " + ln[1:].strip()
        elif ln and not ln.startswith("*"):
            out.append(ln.strip())
    return out


def parse_models(text: str) -> dict[str, tuple[str, dict[str, str]]]:
    """.MODEL lines -> {name: (type, {param: value})}."""
    res = {}
    for ln in _joined(text):
        m = re.match(r"\.MODEL\s+(\S+)\s+(\w+)\s*\(?(.*?)\)?\s*$", ln, re.I)
        if m:
            params = dict((k.upper(), v) for k, v in re.findall(r"(\w+)\s*=\s*([^\s,()]+)", m.group(3)))
            res[m.group(1).upper()] = (m.group(2).upper(), params)
    return res


def model(name: str) -> tuple[str, dict[str, str]] | None:
    """Single .MODEL of a library entry (entry name == model name, or the only model in it)."""
    t = get(name)
    if not t:
        return None
    ms = parse_models(t)
    if name.upper() in ms:
        return ms[name.upper()]
    if len(ms) == 1:
        return next(iter(ms.values()))
    return None


# ---- generic model definitions (`NAME : P=V,...` text inside the .LML files) and device -> MODEL mapping (.LIB files) ----
@cache
def generic_models() -> dict[str, dict[str, str]]:
    """LX_NPN_SSLF, LX_PNP_MPLF, DIODE ... (ISIS notation values; converted by the caller)."""
    out: dict[str, dict[str, str]] = {}
    d = models_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.LML")):
        txt = f.read_bytes().decode("latin-1")
        for m in re.finditer(r"^([A-Za-z0-9_+\-]+) : ((?:[A-Za-z0-9_+\-]+=[^,\r\n]*,?\s*)+)$", txt, re.M):
            out.setdefault(m.group(1).upper(), dict((k.upper(), v.strip()) for k, v in re.findall(r"([A-Za-z0-9_+\-]+)=([^,\r\n]*)", m.group(2))))
    return out


def _lib_dir() -> Path:
    return models_dir().parent / "LIBRARY"


@cache
def device_models() -> dict[str, tuple[str, str]]:
    """Device name (BC547, 1N4148, 2N7000 ...) -> (primitive type, MODEL name) from Proteus's device libraries."""
    out: dict[str, tuple[str, str]] = {}
    d = _lib_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.LIB")):
        b = f.read_bytes()
        if not b.startswith(b"DEVICE LIBRARY"):
            continue
        offs = []
        p = 0x20
        while p + 0x50 <= len(b):                              # DEVICE LIBRARY entries: name[0x40], u32 offset, ts, aux, pad
            name = b[p:p + 0x40].split(b"\0")[0].decode("latin-1")
            off = struct.unpack_from("<I", b, p + 0x40)[0]
            if not name or not name.isprintable() or off == 0 or off + 4 > len(b):
                break
            offs.append((name, off))
            p += 0x50
        for name, off in offs:
            n = struct.unpack_from("<I", b, off)[0]
            blob = b[off + 4:off + 4 + min(n, 20000)]
            mm = re.search(rb"\{MODEL=([A-Za-z0-9_+\-]+)(?:,[^}]*)?\}", blob)
            pm = re.search(rb"\{PRIMITIVE=[A-Z]+,([A-Z0-9_]+)\}", blob)
            dm = re.search(rb"\{DESC=([^}]*)\}", blob)
            cm = re.search(rb"\{CAT=([^}]*)\}", blob)
            if mm and pm:
                out.setdefault(name.upper(), (pm.group(1).decode(), mm.group(1).decode()))
            DESCRIPTIONS.setdefault(name.upper(), ((dm.group(1) if dm else b"").decode("latin-1"), (cm.group(1) if cm else b"").decode("latin-1")))
    return out


DESCRIPTIONS: dict[str, tuple[str, str]] = {}


def search_devices(query: str, limit: int = 30) -> list[dict]:
    """Proteus device-library search (name or description), e.g. 'npn low power', 'BC54'."""
    device_models()
    words = query.upper().split()
    res = []
    for n, (desc, cat) in DESCRIPTIONS.items():
        hay = f"{n} {desc.upper()} {cat.upper()}"
        if all(w in hay for w in words):
            dm = device_models().get(n)
            res.append({"name": n, "desc": desc, "category": cat, "model": dm[1] if dm else None, "type": dm[0] if dm else None})
            if len(res) >= limit:
                break
    return res


def resolve(name: str) -> tuple[str, dict[str, str]] | None:
    """Best SPICE-ish model for a device name: SML library entry, else Proteus device-library MODEL, else None."""
    m = model(name)
    if m:
        return m
    dm = device_models().get(name.upper())
    if dm:
        prim, mname = dm
        gm = generic_models().get(mname.upper())
        if gm:
            typ = {"NPN": "NPN", "PNP": "PNP", "DIODE": "D", "NJFET": "NJF", "PJFET": "PJF", "NMOSFET": "NMOS", "PMOSFET": "PMOS"}.get(prim, prim)
            return typ, gm
    return None
