"""Locate component records in a ROOT.DSN by their device-name header (used to cut templates out of a GUI-saved seed project)."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

THOU = 2540                       # file units per thou (1/1000 inch)
DEVICE_NAMES = (b"LOGICSTATE", b"LOGICPROBE (BIG)", b"AND", b"OR", b"6116", b"74HC374", b"DIPSW_8", b"7SEG-BCD-BLUE", b"7SEG-BCD-GRN", b"7SEG-BCD")


@dataclass
class Located:
    name: str
    off: int           # offset of the device-name pstr
    start: int         # offset of the first byte of the component record
    x: int             # position in thou
    y: int


def locate_components(dsn: bytes) -> list[Located]:
    """All component records in file order. The record start is found through the 'COMPONENT ID' marker that precedes the name."""
    first = dsn.find(b"OBJECT DATA")
    found: list[Located] = []
    for nm in DEVICE_NAMES:
        pat = bytes([len(nm)]) + nm
        for m in re.finditer(re.escape(pat), dsn[first:]):
            o = first + m.start()
            if o == 0 or dsn[o - 1] == 0xFF:
                continue
            x, y = struct.unpack("<ii", dsn[o + 1 + len(nm):o + 9 + len(nm)])
            if x % THOU or y % THOU:
                continue
            found.append(Located(nm.decode(), o, -1, x // THOU, y // THOU))
    found.sort(key=lambda d: d.off)
    for d in found:
        p = dsn.rfind(b"COMPONENT ID\x00", 0, d.off)
        for ln in range(10):
            q = p - 49 - ln
            if dsn[q] == 0xFF and dsn[q + 1] == ln:
                d.start = q - 1
                break
        else:
            raise ValueError(f"no record start for {d.name} at {d.off:#x}")
    return found
