"""Declarative design -> .pdsprj, on top of the vendored _gen package (template-based generator; devices limited to the seed set).

kinds: LS (LOGICSTATE, pin Q) | 6116 (A0..A10 CE OE WE D0..D7) | 74HC374 (D0..D7 Q0..Q7 OE CLK) |
       DIPSW_8 (L0..L7 left, R0..R7 right) | BCD | BLUE | GRN (7-seg BCD, pins P0..P3)
Coordinates are thou (1/1000 inch). Wire endpoints are "partid.pin".
"""
from __future__ import annotations

import os
import zipfile

from ._gen import cdbfmt, design, dsn, dsn2, layout

LS_P = lambda st: ["{PRIMITIVE=DIGITAL,RTDSTATE}", "{STATE=%d}" % st]
SEG = [("D3", ""), ("D2", ""), ("D1", ""), ("D0", "")]
DSW_PINS = [("COM%d" % i, str(i)) for i in range(1, 9)] + [("NO%d" % i, str(17 - i)) for i in range(1, 9)]
DSW_PROPS = ["{MODFILE=DIPSW}", "{ELEMENTS=8}", "{SETPOINT=0}", "{SWTIME=1m}", "{RON=1}", "{ROFF=100M}"]
R6116 = list(zip(["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "$CE$", "$OE$", "$WE$", "D0", "D1", "D2",
                  "D3", "D4", "D5", "D6", "D7", "VCC", "GND"],
                 map(str, [8, 7, 6, 5, 4, 3, 2, 1, 23, 22, 19, 18, 20, 21, 9, 10, 11, 13, 14, 15, 16, 17, 24, 12])))
H374 = list(zip(["D0", "Q0", "D1", "Q1", "D2", "Q2", "D3", "Q3", "D4", "Q4", "D5", "Q5", "D6", "Q6", "D7", "Q7", "$OE$", "CLK",
                 "VCC", "GND"], map(str, [3, 2, 4, 5, 7, 6, 8, 9, 13, 12, 14, 15, 17, 16, 18, 19, 1, 11, 20, 10])))
P6116 = ["{MODFILE=6116.MDF}", "{ITFMOD=NMOS}", "{TAA=100ns}", "{TCE=100ns}", "{TOE=50ns}", "{TOD=35ns}", "{TWP=60ns}",
         "{PACKAGE=DIL24}"]
P374 = ["{MODFILE=74XX374.MDF}", "{PACKAGE=DIL20}", "{INIT=0}", "{ITFMOD=TTLHC}"]
KINDS = sorted(dsn.PINS)


def _cdb_entry(p) -> tuple:
    k, ser = p["kind"], p["serial"]
    ref = p["ref"] or ""
    if k == "LS":
        return (ser, ref, "LOGICSTATE", [("Q0", "")], LS_P(p.get("state") or 0), {"unit": 0})
    if k == "6116":
        return (ser, ref, "6116", R6116, P6116, {"unit": 0})
    if k == "74HC374":
        return (ser, ref, "74HC374", H374, P374, {"unit": 0})
    if k == "DIPSW_8":
        return (ser, ref, "DIPSW_8", DSW_PINS, DSW_PROPS, {"unit": 0})
    return (ser, "", {"BCD": "7SEG-BCD", "BLUE": "7SEG-BCD-BLUE", "GRN": "7SEG-BCD-GRN"}[k], SEG, [], {})


def build(path: str | os.PathLike, parts: list[dict], wires: list[dict]) -> dict:
    """Write a .pdsprj. Returns {parts, wires, conflicts} (conflicts = geometric wire clashes, must be empty)."""
    d = dsn.Design()
    byid = {}
    for p in parts:
        k = p["kind"]
        if k not in dsn.PINS:
            raise ValueError(f"kind {k!r} unsupported; use one of {KINDS}")
        if p["id"] in byid:
            raise ValueError(f"duplicate id {p['id']}")
        ref = p.get("ref", p["id"] if k in ("LS", "6116", "74HC374", "DIPSW_8") else None)
        part = d.add(k, int(p["x"]), int(p["y"]), ref=ref)
        part["state"] = int(p.get("state", 0))
        if k == "LS":
            part["blob"] = design.ls_blob(part["state"])
        byid[p["id"]] = part
    for w in wires:
        (ia, pa), (ib, pb) = w["a"].split(".", 1), w["b"].split(".", 1)
        A, B = byid[ia], byid[ib]
        for part, pin in ((A, pa), (B, pb)):
            if pin not in dsn.PINS[part["kind"]]:
                raise ValueError(f"{part['kind']} has no pin {pin}; pins: {sorted(dsn.PINS[part['kind']])}")
        a, b = d.tip(A, pa), d.tip(B, pb)
        via = [tuple(v) for v in w.get("via", [])]
        if not via and a[0] != b[0] and a[1] != b[1]:
            xm = w.get("jog_x", (a[0] + b[0]) // 2)
            via = [(xm, a[1]), (xm, b[1])]
        nw = d.wire([a, *via, b], (A, pa), (B, pb))
        nw["net"] = w.get("net", f"{ia}.{pa}")
    conflicts = layout.check(d)
    data = dsn2.build(d)               # sorts parts, assigns serials
    entries = [_cdb_entry(p) for p in d.parts]
    entries = [(*e[:5], {**e[5], "idx": i + 1}) if e[1] else e for i, e in enumerate(entries)]
    cdb = cdbfmt.build_cdb(entries)
    src = zipfile.ZipFile(dsn.SEEDS + "/seed4.pdsprj")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            z.writestr(n, data if n == "ROOT.DSN" else cdb if n == "ROOT.CDB" else src.read(n))
    return {"parts": len(d.parts), "wires": len(d.wires), "conflicts": [str(c) for c in conflicts][:30]}


def inspect(path: str | os.PathLike) -> dict:
    """Parts (from ROOT.CDB) and file members of a .pdsprj."""
    z = zipfile.ZipFile(path)
    out = {"members": z.namelist(), "parts": []}
    try:
        c = cdbfmt.parse_cdb(z.read("ROOT.CDB"))
    except Exception as e:                      # noqa: BLE001
        out["cdb_error"] = repr(e)
        return out
    byidx = {p["idx"]: p for p in c["parts"]}
    for e in c["elements"]:
        p = byidx.get(e["part"])
        out["parts"].append({"serial": e["serial"], "ref": e["ref"], "device": p["device"] if p else "",
                             "pins": [n for n, _ in e["pins"]], "props": (p["props"].split("\n")[:-1] if p else [])})
    return out
