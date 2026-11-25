"""Component templates cut from a GUI-saved seed project (`seeds/seed4.DSN`) and the pin geometry of each supported device.

A template is the unwired instance record of one device. To place a part, `builder` copies the record and patches the position
fields (`xo`/`yo` offsets), the serial, the reference text and clears the link slots.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

from .scan import Located, locate_components

SEEDS = Path(__file__).parent / "seeds"
Pin = tuple[int, tuple[int, int]]            # (link slot index, (dx, dy) of the pin tip from the part origin, in thou)


@dataclass(frozen=True)
class Template:
    kind: str
    blob: bytes
    x0: int
    y0: int
    xo: tuple[int, ...]                      # byte offsets of x coordinates inside the record
    yo: tuple[int, ...]
    serial_off: int
    ref: tuple[int, int] | None              # (offset of the reference text, its original length) or None when the device has no reference text
    npins: int
    pins: dict[str, Pin] = field(default_factory=dict)

    def pin_tip(self, pin: str, x: int, y: int, flip: int = 0) -> tuple[int, int]:
        dx, dy = self.pins[pin][1]
        return (x + (-dx if flip & 1 else dx), y + (-dy if flip & 2 else dy))

    def with_state(self, state: int) -> Template:
        """LOGICSTATE: set the initial logic level stored in the PROPERTIES text."""
        b = bytearray(self.blob)
        j = bytes(b).find(b"{STATE=0}")
        if j < 0:
            raise ValueError(f"{self.kind}: no {{STATE=}} property")
        b[j:j + 9] = b"{STATE=%d}" % state
        return replace(self, blob=bytes(b))


def _pins_6116() -> dict[str, Pin]:
    p: dict[str, Pin] = {f"A{k}": (k, (-200, -100 - 100 * k)) for k in range(11)}
    for name, (idx, row) in {"CE": (11, 12), "OE": (12, 13), "WE": (13, 14)}.items():
        p[name] = (idx, (-200, -100 - 100 * row))
    p.update({f"D{k}": (14 + k, (1000, -100 - 100 * k)) for k in range(8)})
    return p


def _pins_374() -> dict[str, Pin]:
    p: dict[str, Pin] = {}
    for k in range(8):
        p[f"D{k}"] = (2 * k, (-200, -100 - 100 * k))
        p[f"Q{k}"] = (2 * k + 1, (800, -100 - 100 * k))
    p["OE"] = (16, (-200, -100 - 100 * 9))
    p["CLK"] = (17, (-200, -100 - 100 * 10))
    return p


def _pins_dipsw() -> dict[str, Pin]:
    p: dict[str, Pin] = {}
    for k in range(8):
        p[f"L{k}"] = (8 + k, (-100, -100 - 100 * k))       # NO(16-k): left side, row k
        p[f"R{k}"] = (k, (500, -100 - 100 * k))            # COM(1+k): right side row k
    return p


def _pins_segment() -> dict[str, Pin]:
    return {f"P{k}": (k, (100 + 150 * k, -1050)) for k in range(4)}


# kind -> (index of the component in the seed, record length, xo, yo, serial offset, reference (offset, length), pins, pin table)
_SPEC = {
    "6116": (18, 0x201, (5, 75, 148, 213, 392), (9, 79, 152, 217, 396), 404, (3, 2), 24, _pins_6116),
    "74HC374": (14, 462, (5, 78, 154, 219, 357), (9, 82, 158, 223, 361), 369, (3, 2), 20, _pins_374),
    "DIPSW_8": (12, 460, (7, 80, 156, 221, 371), (11, 84, 160, 225, 375), 383, (3, 4), 16, _pins_dipsw),
    "BCD": (15, 358, (3, 69, 146, 240, 317), (7, 73, 150, 244, 321), 329, (3, 0), 4, _pins_segment),
    "BLUE": (16, 368, (3, 69, 151, 245, 327), (7, 73, 155, 249, 331), 339, (3, 0), 4, _pins_segment),
    "GRN": (17, 366, (3, 69, 150, 244, 325), (7, 73, 154, 248, 329), 337, (3, 0), 4, _pins_segment),
}


@cache
def seed_dsn() -> bytes:
    return (SEEDS / "seed4.DSN").read_bytes()


@cache
def seed_components() -> list[Located]:
    return locate_components(seed_dsn())


@cache
def templates() -> dict[str, Template]:
    seed, comps = seed_dsn(), seed_components()
    out: dict[str, Template] = {}
    for kind, (i, length, xo, yo, ser, ref, n, pins) in _SPEC.items():
        c = comps[i]
        out[kind] = Template(kind, seed[c.start:c.start + length], c.x, c.y, xo, yo, ser, ref if ref[1] or ref[0] else None, n, pins())
    ls = (SEEDS / "inst_ls.bin").read_bytes()                  # LOGICSTATE: unnamed instance, originally at (-1900, -1000)
    out["LS"] = Template("LS", ls, -1900, -1000, (3, 69, 148, 252, 331), (7, 73, 152, 256, 335), 343, (3, 0), 1, {"Q": (0, (200, 0))})
    return out


KINDS = ("LS", "BCD", "BLUE", "GRN", "DIPSW_8", "74HC374", "6116")           # also the order in which parts are written to the file
