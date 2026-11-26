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
    x: float                                # absolute connection point (thou), as Proteus itself computes it
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

    def tip(self, pin: str | int) -> tuple[float, float]:
        """Absolute connection point of a pin (by name or index)."""
        if isinstance(pin, int):
            p = self.pins[pin]
        else:
            p = next((q for q in self.pins if q.name.upper() == pin.upper()), None)
            if p is None:
                raise NativeError(f"{self.ref or self.cls}: no pin {pin!r}; pins: {[q.name for q in self.pins]}")
        return p.x, p.y


REF_X, REF_Y = 776, 363                  # top-left of the logic-state body in the default view (see calibrate)
TOOL_ICON = {"generator": 377, "probe": 400, "instrument": 423}            # left toolbar (y of the icon)
GRAPHS = ["ANALOGUE", "DIGITAL", "MIXED", "FREQUENCY", "TRANSFER", "NOISE", "DISTORTION", "FOURIER", "AUDIO", "INTERACTIVE",
          "CONFORMANCE", "DC SWEEP", "AC SWEEP"]
GENERATORS = ["DC", "SINE", "PULSE", "EXP", "SFFM", "PWLIN", "FILE", "AUDIO", "DSTATE", "EDGE", "DCLOCK", "DPATTERN", "SCRIPTABLE"]
PROBES = ["VOLTAGE", "CURRENT", "TAPE"]
TERMINALS = ["DEFAULT", "INPUT", "OUTPUT", "BIDIR", "POWER", "GROUND", "CHASSIS", "DYNAMIC", "BUS", "NC"]
INSTRUMENTS = ["OSCILLOSCOPE", "LOGIC ANALYSER", "COUNTER TIMER", "VIRTUAL TERMINAL", "SPI DEBUGGER", "I2C DEBUGGER", "SIGNAL GENERATOR",
               "PATTERN GENERATOR", "DC VOLTMETER", "DC AMMETER", "AC VOLTMETER", "AC AMMETER", "WATTMETER"]


class Native:
    """A running Proteus (inside a one-shot sandbox session) with the hook injected."""

    def __init__(self, s: sess.BaseSession) -> None:
        if ws.IS_WINDOWS:
            raise NativeError("native building is implemented for the Linux sandbox only")
        self.s = s
        self.h = Hook()
        self.isis = 0
        self.design = 0
        self.ox = self.oy = 0
        self._attach()
        self.calibrate()

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
    def objects(self) -> list[Comp]:
        """Everything with connection points in the live design (components, generators, probes, terminals, instruments)."""
        h = self.h
        buf = int(h.req("scratch"), 16)
        head = h.u32(self.design + 0x25C)
        node, out = h.u32(head), []
        while node and len(out) < 5000:
            obj = node - 0x10
            cls = h.clsname(h.u32(obj))
            x, y = struct.unpack("<ii", h.peek(obj + 0x2C, 8))
            pins: list[Pin] = []
            try:
                n = h.vcall(obj, 0x8C)
            except NativeError:
                n = 0
            if 0 < n <= 512:
                for i in range(n):
                    nm = h.vcall(obj, 0x98, i)
                    r = h.vcall(obj, 0x94, buf, i)             # Proteus' own absolute pin position
                    px_, py_ = struct.unpack("<ii", h.peek(r, 8))
                    pins.append(Pin(h.cstr(nm, 24) if nm else "", px_ / 2540, py_ / 2540))
            ref = device = ""
            if cls in (".?AVCOMPONENT@@", ".?AVACTIVECOMPONENT@@"):
                ref = h.cstr(obj + 0x44, 16)
                device = h.cstr(h.u32(obj + 0x26C) + 4, 40)
            out.append(Comp(obj, cls, x / 2540, y / 2540, ref, pins, device))
            node = h.u32(obj + 0x10)
        return out

    def components(self) -> list[Comp]:
        return [c for c in self.objects() if c.cls in (".?AVCOMPONENT@@", ".?AVACTIVECOMPONENT@@")]

    def find_obj(self, x: float, y: float) -> Comp | None:
        return next((c for c in self.objects() if abs(c.x - x) < 1 and abs(c.y - y) < 1), None)

    def find(self, x: float, y: float) -> Comp | None:
        return next((c for c in self.components() if abs(c.x - x) < 1 and abs(c.y - y) < 1), None)

    # ---- GUI actions ----
    def px(self, x: float, y: float) -> tuple[int, int]:
        """Pixel of a point in thou, in the CURRENT view (the view pans, and is saved with the project)."""
        return round(PX0 + self.ox + PXPT * x), round(PY0 + self.oy - PXPT * y)

    def calibrate(self) -> None:
        """Find the view offset from the one object every start project has: the logic state S1 at (-1500, 1500) thou."""
        from PIL import Image, ImageChops
        png = self.s.shot("native_cal.png")
        im = Image.open(png).convert("RGB").crop((312, 140, 1630, 940))
        r, g, b = im.split()
        mask = ImageChops.multiply(ImageChops.multiply(b.point(lambda v: 255 if v > 180 else 0), r.point(lambda v: 255 if v < 60 else 0)),
                                   g.point(lambda v: 255 if v < 60 else 0))
        small = mask.resize((mask.width // 13, mask.height // 13), Image.BOX)       # density map: the filled logic state is the densest cell
        px_ = small.load()
        best = max(((px_[x, y], x, y) for x in range(small.width) for y in range(small.height)))
        if best[0] < 60:
            raise NativeError("view calibration failed: the start project's logic state is not on screen")
        cx, cy = best[1] * 13 + 6, best[2] * 13 + 6
        m = mask.load()
        pts = [(x, y) for x in range(max(cx - 30, 0), min(cx + 30, mask.width)) for y in range(max(cy - 30, 0), min(cy + 30, mask.height)) if m[x, y]]
        x0, y0 = min(p[0] for p in pts) + 312, min(p[1] for p in pts) + 140
        self.ox, self.oy = x0 - REF_X, y0 - REF_Y

    def _xdo(self, cmd: str) -> None:
        self.s.sh("xdotool " + cmd, 30)

    def _arrow_tool(self) -> None:
        self.s.click(170, 152)
        self.s.sleep(0.5)
        self.calibrate()

    def _pick(self, device: str, flip_sort: bool = False) -> None:
        """Pick Devices: search, sort by name, jump to the exact name with type-ahead, OK."""
        s = self.s
        s.key("p")
        s.sleep(2.5)
        s.type(device)
        s.sleep(3)
        for _ in range(2 if flip_sort else 1):         # header "Device": sort by name (the order may persist between dialogs)
            s.click(410, 141)
            s.sleep(2)
        s.click(420, 159)
        s.sleep(0.8)
        s.key("Down")                                  # type-ahead searches after the current row and wraps: start from row 2
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
            self._pick(device, flip_sort=attempt == 2)
            X, Y = self.px(x, y)
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
        self.calibrate()
        x, y = self.px(c.x, c.y)
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

    def _drop(self, x: float, y: float, what: str) -> Comp:
        """Click on the canvas until the object count grows (the first click after a menu or dialog only focuses it)."""
        before = len(self.objects())
        X, Y = self.px(x, y)
        for i in range(4):
            self._xdo(f"mousemove {X + i % 2} {Y} sleep 0.5 click 1")
            self.s.sleep(1.3)
            objs = self.objects()
            if len(objs) > before:
                new = min(objs, key=lambda o: abs(o.x - x) + abs(o.y - y))
                self.s.key("Escape")
                self.s.sleep(0.4)
                self._arrow_tool()
                return new
        raise NativeError(f"could not place {what} at ({x},{y})")

    def _list_tool(self, kind: str, names: list[str], name: str) -> None:
        if name.upper() not in names:
            raise NativeError(f"unknown {kind} {name!r}; choose from {names}")
        self._arrow_tool()
        self.s.click(800, 800)
        self.s.sleep(0.6)
        self.s.click(170, TOOL_ICON[kind])
        self.s.sleep(1)
        self.s.click(210, 271 + 14 * names.index(name.upper()))
        self.s.sleep(1)

    def place_generator(self, kind: str, x: float, y: float) -> Comp:
        self._list_tool("generator", GENERATORS, kind)
        return self._drop(x, y, f"{kind} generator")

    def place_probe(self, kind: str, x: float, y: float) -> Comp:
        self._list_tool("probe", PROBES, kind)
        return self._drop(x, y, f"{kind} probe")

    def place_instrument(self, kind: str, x: float, y: float) -> Comp:
        self._list_tool("instrument", INSTRUMENTS, kind)
        return self._drop(x, y, kind)

    def place_terminal(self, kind: str, x: float, y: float) -> Comp:
        """Terminal (GROUND, POWER, INPUT ...) through the canvas context menu: Place > Terminal > kind."""
        if kind.upper() not in TERMINALS:
            raise NativeError(f"unknown terminal {kind!r}; choose from {TERMINALS}")
        s = self.s
        self._arrow_tool()
        s.click(800, 800)
        s.sleep(0.6)
        self._xdo("mousemove 800 500 sleep 0.4 click 3")
        s.sleep(1.5)
        s.click(843, 504)                                   # Place
        s.sleep(1)
        s.click(1000, 617)                                  # Terminal
        s.sleep(1)
        s.click(1150, round(618 + 20.8 * TERMINALS.index(kind.upper())))
        s.sleep(1.5)
        return self._drop(x, y, f"{kind} terminal")

    def _select_at(self, inside: tuple[float, float]) -> None:
        """Select the object at a point: first deselect (clicking an already selected object opens its editor)."""
        self.calibrate()
        self.s.click(500, 300)
        self.s.sleep(0.6)
        self.s.click(*self.px(*inside))
        self.s.sleep(0.8)

    def label(self, c: Comp) -> str:
        """The name Proteus shows for a probe/generator in the graph trace dialogs, e.g. 'C1(2)'."""
        return self.h.cstr(c.obj + 0x48, 24)

    def place_graph(self, kind: str, x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
        """Draw a graph (ANALOGUE, DIGITAL, MIXED, FREQUENCY, TRANSFER ...) as a box with corners (x1,y1)-(x2,y2); returns a point inside it."""
        if kind.upper() not in GRAPHS:
            raise NativeError(f"unknown graph {kind!r}; choose from {GRAPHS}")
        s = self.s
        self._arrow_tool()
        s.click(800, 800)
        s.sleep(0.6)
        s.click(170, 333)
        s.sleep(1)
        s.click(210, 271 + 14 * GRAPHS.index(kind.upper()))
        s.sleep(1)
        (a, b), (c, d) = self.px(x1, y1), self.px(x2, y2)
        self._xdo(f"mousemove {a} {b} sleep 0.5 click 1 sleep 0.5 mousemove {(a + c) // 2} {(b + d) // 2} sleep 0.5 mousemove {c} {d} sleep 0.5 click 1")
        s.sleep(2)
        s.key("Escape")
        s.sleep(0.8)
        self._arrow_tool()
        return (x1 + x2) / 2, (y1 + y2) / 2

    def add_trace(self, inside: tuple[float, float], probes: list[str], name: str = "", expression: str = "",
                  labels: list[str] | None = None, axis: str = "Left") -> None:
        """Select the graph (click inside it) and fill Add Transient Trace: up to 4 probes (by their Proteus label, e.g. 'C1(2)')."""
        s = self.s
        if labels is None:
            labels = sorted(self.label(c) for c in self.objects() if c.cls in (".?AVVPROBE@@", ".?AVGENERATOR@@", ".?AVIPROBE@@"))
        self.calibrate()
        for attempt in range(3):
            self._select_at((inside[0], inside[1] - attempt * 50))
            s.key("ctrl+t")
            s.sleep(2.5)
            if any("Add" in w and "Trace" in w for w in s.windows()):
                break
        else:
            s.shot("native_trace_fail.png")
            raise NativeError("Add Trace dialog did not open (see exchange/native_trace_fail.png)")
        if name:
            s.click(874, 424)
            s.type(name)
        for i, pr in enumerate(probes[:4]):
            if pr not in labels:
                raise NativeError(f"probe {pr!r} not found; available: {labels}")
            s.click(980, 458 + 26 * i)
            s.sleep(1)
            s.click(870, round(476 + 26 * i + 14.5 * (labels.index(pr) + 1)))
            s.sleep(0.8)
        if expression:
            s.click(870, 561)
            s.type(expression)
        if axis.lower() == "right":
            s.click(1018, 562)
        s.click(989, 611)                                   # OK
        s.sleep(1.5)

    def edit_generator(self, c: Comp, name: str | None = None, offset: str | None = None, amplitude: str | None = None,
                       frequency: str | None = None, phase: str | None = None) -> None:
        """Sine Generator Properties (other generator types have other dialogs and are not scripted yet)."""
        s = self.s
        self.calibrate()
        x, y = self.px(c.x, c.y)
        s.double_click(x - 12, y - 10)
        s.sleep(2.5)
        if not any("Generator Properties" in w for w in s.windows()):
            raise NativeError("generator dialog did not open")
        if "Sine" not in " ".join(s.windows()):
            raise NativeError("only sine generators can be edited so far")
        for value, (fx, fy) in ((name, (766, 319)), (offset, (1035, 302)), (amplitude, (1035, 352)),
                                (frequency, (1035, 456)), (phase, (1035, 583))):
            if value is not None:
                s.click(fx, fy)
                s.key("ctrl+a")
                s.type(value)
        s.click(977, 724)                                   # OK
        s.sleep(1.5)

    def edit_graph(self, inside: tuple[float, float], title: str | None = None, start: str | None = None, stop: str | None = None) -> None:
        """Edit Transient Graph: title and time range."""
        s = self.s
        self._select_at(inside)
        s.key("ctrl+e")
        s.sleep(2.5)
        if not any("Edit Transient Graph" in w for w in s.windows()):
            raise NativeError("graph dialog did not open")
        for value, (fx, fy) in ((title, (825, 394)), (start, (763, 420)), (stop, (763, 446))):
            if value is not None:
                s.click(fx, fy)
                s.key("ctrl+a")
                s.type(value)
        s.click(1058, 640)                                  # OK
        s.sleep(1.5)

    def simulate_graph(self, inside: tuple[float, float], wait: float = 10.0) -> None:
        """Select the graph and press Space: Proteus simulates it."""
        self._select_at(inside)
        self.s.key("space")
        self.s.sleep(wait)

    def wire(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        """Draw a wire between two points on pin lines with the real wire tool (click, click)."""
        (x1, y1), (x2, y2) = self.px(*a), self.px(*b)
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


def build_native(name: str, parts: list[dict[str, Any]], wires: list[dict[str, Any]], graphs: list[dict[str, Any]] | None = None, *, base: str | None = None,
                 progress: Callable[[str], None] | None = None, max_seconds: float = 900) -> NativeResult:
    """Create `name`.pdsprj with Proteus itself.

    parts : [{"id": "U1", "device": "74HC00", "x": 2000, "y": -100, "value": "10k", "ref": "R1", "props": {"TOL": "1%"}}, ...]
            "kind": "generator" (device SINE, DC, PULSE ...), "probe" (VOLTAGE, CURRENT), "terminal" (GROUND, POWER ...) or "instrument"
            place those Proteus tools; pins are addressed by name or index ("G1.0", "GND1.GND")
            (device = Proteus library device name; value/ref/props optional)
    wires : [{"from": "U1.Y", "to": "U2.A"}, ...]
    graphs: [{"type": "ANALOGUE", "x1": 500, "y1": -2000, "x2": 5500, "y2": -4500, "stop": "5m",
              "traces": [{"name": "Vout", "probes": ["P1"]}]}]   (probes = ids of probe/generator parts)
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
            kind = p.get("kind", "part")
            say(f"place {p['id']} = {kind} {p['device']}")
            if kind == "generator":
                placed[p["id"]] = nat.place_generator(p["device"], p["x"], p["y"])
            elif kind == "probe":
                placed[p["id"]] = nat.place_probe(p["device"], p["x"], p["y"])
            elif kind == "terminal":
                placed[p["id"]] = nat.place_terminal(p["device"], p["x"], p["y"])
            elif kind == "instrument":
                placed[p["id"]] = nat.place_instrument(p["device"], p["x"], p["y"])
            else:
                placed[p["id"]] = nat.place(p["device"], p["x"], p["y"])
            if kind == "generator" and any(k in p for k in ("name", "offset", "amplitude", "frequency", "phase")):
                nat.edit_generator(placed[p["id"]], p.get("name"), p.get("offset"), p.get("amplitude"), p.get("frequency"), p.get("phase"))
                placed[p["id"]] = nat.find_obj(p["x"], p["y"]) or placed[p["id"]]
            if kind == "part" and any(k in p for k in ("value", "ref", "props")):
                nat.edit(placed[p["id"]], p.get("value"), p.get("ref"), p.get("props"))
                placed[p["id"]] = nat.find(p["x"], p["y"]) or placed[p["id"]]
        for w in wires:
            (ia, pa), (ib, pb) = (w["from"].split(".", 1), w["to"].split(".", 1))
            say(f"wire {w['from']} -> {w['to']}")
            nat.wire(placed[ia].tip(int(pa) if pa.isdigit() else pa), placed[ib].tip(int(pb) if pb.isdigit() else pb))
        for g in graphs or []:
            say(f"graph {g.get('type', 'ANALOGUE')}")
            inside = nat.place_graph(g.get("type", "ANALOGUE"), g["x1"], g["y1"], g["x2"], g["y2"])
            if any(k in g for k in ("title", "start", "stop")):
                nat.edit_graph(inside, g.get("title"), g.get("start"), g.get("stop"))
            for t in g.get("traces", []):
                nat.add_trace(inside, [nat.label(placed[pid]) for pid in t["probes"]], t.get("name", ""), t.get("expression", ""))
            if g.get("simulate", True):
                nat.simulate_graph(inside)
        nat.save()
    problems: list[str] = []
    r = reader.parse_dsn(zipfile.ZipFile(out).read("ROOT.DSN"))
    if r.error:
        problems.append(f"DSN read error: {r.error}")
    import collections
    have = collections.Counter(o.get("kind") for o in r.objects)
    want: collections.Counter[str] = collections.Counter({"COMPONENT": 1})              # + the start project's logic state
    for p in parts:
        want[{"generator": "PROBE", "probe": "PROBE", "terminal": "TERMINAL"}.get(p.get("kind", "part"), "COMPONENT")] += 1
    want["GRAPH"] += len(graphs or [])
    for kind, n in want.items():
        if have[kind] < n:
            problems.append(f"{n} {kind} requested, {have[kind]} in the saved project")
    if have["WIRE"] < len(wires):
        problems.append(f"{len(wires)} wires requested, {have['WIRE']} in the saved project")
    for p in parts:
        if p.get("kind", "part") == "part" and not any(abs(o["cts"]["x"] - p["x"]) < 1 and abs(o["cts"]["y"] - p["y"]) < 1
                                                      for o in r.objects if o.get("kind") == "COMPONENT"):
            problems.append(f"{p['id']} missing in the saved project")
    nwire = have["WIRE"]
    return NativeResult(name, {i: c.ref for i, c in placed.items()}, nwire, problems, str(out))
