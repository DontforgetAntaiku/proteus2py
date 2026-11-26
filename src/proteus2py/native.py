"""Proteus-authored schematics: Proteus itself places the parts and draws the wires, an injected hook reads its object model back.

Why: results are only trusted when Proteus produced them. `build_native` drives the real Proteus GUI (Pick Devices, component placement,
wire tool) inside the same bubblewrap/Xvfb/Wine sandbox as everything else, and verifies every step through `p2hook.dll`, a small DLL
injected into PDS.EXE that serves requests as files (hook.req / hook.resp). The saved .pdsprj is therefore written by Proteus.

Linux sandbox only for now. Coordinates are thou (1/1000 inch), as everywhere else in the package.
"""
from __future__ import annotations

import shutil
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import session as sess
from . import workspace as ws

PX0, PY0, PXPT = 971, 541, 0.112          # pixel of thou (0,0) in the default view and pixels per thou
PIN_LEN = 200                              # standard pin length (the symbol pin record holds the pin base, the tip is further out)
HOOK_FILES = ("p2hook.dll", "p2launch.exe")
DESIGN_VT = 0x9B8D8                        # RVA of the DESIGN vtable in ISIS.DLL (Proteus 8.11 build used for development)


class NativeError(RuntimeError):
    pass


def px(x: float, y: float) -> tuple[int, int]:
    return round(PX0 + PXPT * x), round(PY0 - PXPT * y)


def native_dir() -> Path:
    """Folder with the built hook (package data first, then the repository's native/ folder)."""
    for d in (Path(__file__).parent / "_native", Path(__file__).resolve().parents[2] / "native"):
        if all((d / f).exists() for f in HOOK_FILES):
            return d
    raise NativeError("p2hook.dll / p2launch.exe not found: run `make -C native` (needs i686-w64-mingw32-gcc)")


class Hook:
    """Client of the injected DLL: request/response files in the exchange folder."""

    def __init__(self) -> None:
        self.ex = ws.exchange()
        self.n = 0

    def req(self, text: str, timeout: float = 20.0) -> str:
        self.n += 1
        resp, tmp, req = self.ex / "hook.resp", self.ex / "hook.tmp", self.ex / "hook.req"
        resp.unlink(missing_ok=True)
        tmp.write_text(f"{self.n} {text}\n")
        tmp.rename(req)
        end = time.time() + timeout
        while time.time() < end:
            if resp.exists():
                parts = resp.read_text().split(" ", 2)
                kind, body = parts[1], (parts[2] if len(parts) > 2 else "")
                if kind != "ok":
                    raise NativeError(f"hook: {text}: {body}")
                return body
            time.sleep(0.05)
        raise NativeError(f"hook timeout: {text}")

    def peek(self, addr: int, n: int = 4) -> bytes:
        return bytes.fromhex(self.req(f"peek {addr:x} {n}"))

    def u32(self, addr: int) -> int:
        return struct.unpack("<I", self.peek(addr, 4))[0]

    def cstr(self, addr: int, n: int = 64) -> str:
        return self.peek(addr, n).split(b"\0")[0].decode("latin1")

    def vcall(self, obj: int, off: int, *args: int) -> int:
        fn = self.u32(self.u32(obj) + off)
        return int(self.req(f"call {fn:x} {obj:x} " + " ".join(f"{a:x}" for a in args)).strip() or "0", 16)

    def clsname(self, vtable: int) -> str:
        col = self.u32(vtable - 4)
        return self.cstr(self.u32(col + 12) + 8, 60)


@dataclass
class Pin:
    name: str
    x: float                                # pin base relative to the part origin (thou)
    y: float


@dataclass
class Comp:
    obj: int
    cls: str
    x: float
    y: float
    ref: str
    pins: list[Pin] = field(default_factory=list)
    device: str = ""

    def tip(self, pin: str, along: float = PIN_LEN) -> tuple[float, float]:
        """Absolute point on the pin line `along` thou outside the pin base (the end of the line is the connection point)."""
        p = next((q for q in self.pins if q.name.upper() == pin.upper()), None)
        if p is None:
            raise NativeError(f"{self.ref}: no pin {pin!r}; pins: {[q.name for q in self.pins]}")
        xs, ys = [q.x for q in self.pins], [q.y for q in self.pins]
        dx = dy = 0.0
        if min(xs) < max(xs) and p.x in (min(xs), max(xs)) and (p.x != 0 or len(set(xs)) > 1):
            dx = -1.0 if p.x == min(xs) else 1.0
        if dx == 0.0 and min(ys) < max(ys):
            dy = 1.0 if p.y == max(ys) else -1.0 if p.y == min(ys) else 0.0
        if dx == 0.0 and dy == 0.0:
            dx = 1.0
        return self.x + p.x + dx * along, self.y + p.y + dy * along


class Native:
    """A running Proteus (inside a one-shot sandbox session) with the hook injected."""

    def __init__(self, s: sess.BaseSession) -> None:
        if ws.IS_WINDOWS:
            raise NativeError("native building is implemented for the Linux sandbox only")
        self.s = s
        self.h = Hook()
        self.isis = 0
        self.design = 0
        self._attach()

    # ---- start-up ----
    def _attach(self) -> None:
        d = native_dir()
        for f in HOOK_FILES:
            shutil.copy2(d / f, ws.exchange() / f)
        for f in ("hook.req", "hook.resp"):
            (ws.exchange() / f).unlink(missing_ok=True)
        self.s.sh("wine 'C:\\\\exchange\\\\p2launch.exe' --attach PDS.EXE 'C:\\\\exchange\\\\p2hook.dll' > /home/sbx/exchange/inject.txt 2>&1", 60)
        self.h.req("ping")
        self.isis = int(self.h.req("mod ISIS.DLL"), 16)
        if not self.isis:
            raise NativeError("ISIS.DLL not loaded")
        # the global DESIGN object (vtable RVA 0x9b8d8 of this ISIS.DLL build) lives in the module's own data section
        hits = self.h.req(f"scanvt {self.isis + DESIGN_VT:x} {self.isis + DESIGN_VT + 1:x} 50").split()
        inside = [int(a, 16) for a in hits[::2] if self.isis <= int(a, 16) < self.isis + 0x400000]
        if not inside:
            raise NativeError("DESIGN object not found (unsupported Proteus build?)")
        self.design = inside[0]

    # ---- reading Proteus' own object model ----
    def components(self) -> list[Comp]:
        h = self.h
        head = h.u32(self.design + 0x25C)
        node, out = h.u32(head), []
        while node and len(out) < 5000:
            obj = node - 0x10
            cls = h.clsname(h.u32(obj))
            if cls in (".?AVCOMPONENT@@", ".?AVACTIVECOMPONENT@@"):
                x, y = struct.unpack("<ii", h.peek(obj + 0x2C, 8))
                n = h.vcall(obj, 0x8C)
                sym = h.u32(obj + 0x26C)
                arr = h.u32(sym + 0x90)
                pins = []
                for i in range(n):
                    pp = h.u32(arr + 4 * i)
                    px_, py_ = struct.unpack("<ii", h.peek(pp + 4, 8))
                    pins.append(Pin(h.cstr(h.vcall(obj, 0x98, i)), px_ / 2540, py_ / 2540))
                ref = h.cstr(obj + 0x44, 16)
                out.append(Comp(obj, cls, x / 2540, y / 2540, ref, pins, h.cstr(sym + 4, 40)))
            node = h.u32(obj + 0x10)
        return out

    def find(self, x: float, y: float) -> Comp | None:
        return next((c for c in self.components() if abs(c.x - x) < 1 and abs(c.y - y) < 1), None)

    # ---- GUI actions ----
    def _xdo(self, cmd: str) -> None:
        self.s.sh("xdotool " + cmd, 30)

    def _arrow_tool(self) -> None:
        self.s.click(170, 152)
        self.s.sleep(0.5)

    def _pick(self, device: str, flip_sort: bool = False) -> None:
        """Pick Devices: search, sort by name, jump to the exact name with type-ahead, OK."""
        s = self.s
        s.key("p")
        s.sleep(2.5)
        s.type(device)
        s.sleep(3)
        if flip_sort:                                  # the sort order may persist between dialogs
            s.click(410, 141)
            s.sleep(1.5)
        s.click(410, 141)                              # header "Device": sort by device name
        s.sleep(2)
        s.click(420, 159)
        s.sleep(0.8)
        s.type(device)                                 # type-ahead selects the first row starting with this name
        s.sleep(1.5)
        s.click(1711, 916)                             # OK
        s.sleep(2)

    def place(self, device: str, x: float, y: float) -> Comp:
        """Pick `device` from Proteus' library (Pick Devices dialog) and place it with its origin at (x, y) thou."""
        s = self.s
        for attempt in range(3):
            before = len(self.components())
            self._arrow_tool()
            s.click(800, 800)                         # deselect: a pick silently does nothing while something is selected
            s.sleep(0.8)
            self._pick(device, flip_sort=attempt == 1)
            X, Y = px(x, y)
            c = None
            for i in range(4):                        # the first click after the dialog only focuses the canvas
                self._xdo(f"mousemove {X + i % 2} {Y} sleep 0.5 click 1")
                s.sleep(1.5)
                c = self.find(x, y)
                if c is not None and len(self.components()) > before:
                    break
            else:
                raise NativeError(f"could not place {device} at ({x},{y})")
            s.key("Escape")
            s.sleep(0.5)
            self._arrow_tool()
            if c.device.upper() == device.upper():
                return c
            s.key("ctrl+z")                           # wrong device picked: undo and try the other sort order
            s.sleep(1.5)
        raise NativeError(f"Proteus placed {c.device!r} instead of {device!r}")

    def edit(self, c: Comp, value: str | None = None, ref: str | None = None, props: dict[str, str] | None = None) -> None:
        """Edit Component dialog: part reference, part value and 'other properties' (NAME=value lines)."""
        s = self.s
        x, y = px(c.x, c.y)
        for dx, dy in ((0, 0), (-25, 0), (0, 12), (25, 0), (0, -12)):
            s.click(x + dx, y + dy)                    # select the part, then Edit Properties
            s.sleep(0.8)
            s.key("ctrl+e")
            s.sleep(2.5)
            if any("Edit Component" in w for w in s.windows()):
                break
            if any(w.startswith("Edit ") for w in s.windows()):
                s.key("Escape")                     # some other editor (e.g. the label) opened
                s.sleep(1)
        else:
            raise NativeError(f"no Edit Component dialog for {c.ref}")
        if ref is not None:
            s.click(870, 363)
            s.key("ctrl+a")
            s.type(ref)
        if value is not None:
            s.click(870, 386)
            s.key("ctrl+a")
            s.type(value)
        if props:
            s.click(840, 555)
            s.key("ctrl+End")
            s.type("".join(f"\n{k}={v}" for k, v in props.items()))
        s.click(1097, 365)                            # OK
        s.sleep(1.5)

    def wire(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        """Draw a wire between two points on pin lines with the real wire tool (click, click)."""
        (x1, y1), (x2, y2) = px(*a), px(*b)
        self._arrow_tool()
        self._xdo(f"mousemove {x1} {y1} sleep 0.5 click 1 sleep 0.8 mousemove {x1 + 2} {y1 - 5} sleep 0.3 mousemove {x2} {y2} sleep 0.8 click 1")
        self.s.sleep(1.2)
        self.s.key("Escape")
        self.s.sleep(0.5)

    def save(self) -> None:
        self.s.key("ctrl+s")
        self.s.sleep(4)


@dataclass
class NativeResult:
    project: str
    parts: dict[str, str]                     # id -> reference Proteus assigned
    wires: int
    problems: list[str]
    path: str


def build_native(name: str, parts: list[dict[str, Any]], wires: list[dict[str, Any]], *, base: str | None = None,
                 progress: Callable[[str], None] | None = None, max_seconds: float = 900) -> NativeResult:
    """Create `name`.pdsprj with Proteus itself.

    parts : [{"id": "U1", "device": "74HC00", "x": 2000, "y": -100, "value": "10k", "ref": "R1", "props": {"TOL": "1%"}}, ...]
            (device = Proteus library device name; value/ref/props optional)
    wires : [{"from": "U1.Y", "to": "U2.A"}, ...]
    base  : an existing, nearly empty project to start from (default: a one-part template written by the generator)
    """
    from . import gen
    from .dsn import reader
    import zipfile

    out = ws.project(name)
    src = ws.project(base) if base else ws.exchange() / "_native_base.pdsprj"
    if not base:
        gen.build(src, [{"id": "S1", "kind": "LS", "x": -1500, "y": 1500}], [])
    shutil.copy2(src, out)
    say = progress or (lambda m: None)
    placed: dict[str, Comp] = {}
    with sess.once(name, load_wait=40, max_seconds=max_seconds) as s:
        nat = Native(s)
        for p in parts:
            say(f"place {p['id']} = {p['device']}")
            placed[p["id"]] = nat.place(p["device"], p["x"], p["y"])
            if any(k in p for k in ("value", "ref", "props")):
                nat.edit(placed[p["id"]], p.get("value"), p.get("ref"), p.get("props"))
                placed[p["id"]] = nat.find(p["x"], p["y"]) or placed[p["id"]]
        for w in wires:
            (ia, pa), (ib, pb) = w["from"].split("."), w["to"].split(".")
            say(f"wire {w['from']} -> {w['to']}")
            nat.wire(placed[ia].tip(pa), placed[ib].tip(pb))
        nat.save()
    problems: list[str] = []
    r = reader.parse_dsn(zipfile.ZipFile(out).read("ROOT.DSN"))
    if r.error:
        problems.append(f"DSN read error: {r.error}")
    comps = [o for o in r.objects if o.get("kind") == "COMPONENT"]
    nwire = sum(1 for o in r.objects if o.get("kind") == "WIRE")
    for p in parts:
        if not any(abs(o["cts"]["x"] - p["x"]) < 1 and abs(o["cts"]["y"] - p["y"]) < 1 for o in comps):
            problems.append(f"{p['id']} missing in the saved project")
    if nwire < len(wires):
        problems.append(f"{len(wires)} wires requested, {nwire} in the saved project")
    return NativeResult(name, {i: c.ref for i, c in placed.items()}, nwire, problems, str(out))
