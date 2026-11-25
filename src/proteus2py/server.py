"""MCP server for Proteus 8. Everything runs in a bwrap+Wine sandbox; host files are copied in, never modified."""
from __future__ import annotations

import functools
import hashlib
import json
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import gen, gui, prospice, session, sim, spice
from . import workspace as ws
from .models import GenResult, MonteCarloResult, ProjectRunResult, SimResult, SweepResult

mcp = MCPServer("proteus")


def tool() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """@tool() that reports every failure to the model (the SDK hides the text of non-ToolError exceptions)."""
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def inner(*a: Any, **kw: Any) -> Any:
            try:
                return fn(*a, **kw)
            except ToolError:
                raise
            except Exception as e:                      # noqa: BLE001
                raise ToolError(f"{type(e).__name__}: {e}") from e
        return mcp.tool()(inner)
    return deco


_LOCK = threading.RLock()                 # one Proteus at a time
# default view after load (thou -> screen px), measured at the default zoom; verify with a screenshot
_PX0, _PY0, _K = 971, 541, 0.112


# ---- files -------------------------------------------------------------------------------------
@tool()
def list_projects() -> list[str]:
    """Projects (.pdsprj) in the sandbox exchange folder."""
    return sorted(p.stem for p in ws.exchange().glob("*.pdsprj"))


@tool()
def import_project(src_path: str, name: str | None = None) -> str:
    """Copy a host .pdsprj into the sandbox (read-only access to the original). Returns the name for other tools."""
    src = Path(src_path).expanduser()
    if src.suffix.lower() != ".pdsprj" or not src.is_file():
        raise ValueError("need an existing .pdsprj file")
    n = ws.name_ok(name or "".join(c if c.isalnum() or c in "_.-" else "_" for c in src.stem))
    shutil.copyfile(src, ws.project(n))
    return n


@tool()
def export_project(name: str, dst_path: str) -> str:
    """Copy a sandbox project to a NEW host path. Refuses to overwrite."""
    dst = Path(dst_path).expanduser()
    if dst.exists():
        raise FileExistsError(dst)
    shutil.copyfile(ws.project(name), dst)
    return str(dst)


@tool()
def project_info(name: str) -> dict:
    """Parts (ref, device, pins, properties) read from ROOT.CDB of a project."""
    return gen.inspect(ws.project(name))


# ---- generation (template-based; limited device set) ----------------------------------------------
@tool()
def build_design(name: str, parts: list[dict[str, Any]], wires: list[dict[str, Any]]) -> GenResult:
    """Generate a project from seed templates. parts: [{"id":"U1","kind":"6116","x":-3400,"y":1000,"state":0}] (thou).
    kinds: LS(pin Q) 6116(A0-A10 CE OE WE D0-D7) 74HC374(D0-D7 Q0-Q7 OE CLK) DIPSW_8(L0-L7 R0-R7) BCD BLUE GRN(P0-P3).
    wires: [{"a":"S1.Q","b":"U1.A0","net":"A0","via":[[x,y],...],"jog_x":-5000}]. Without via a 2-bend route is made.
    Check `conflicts` is empty (wires of different nets touching/overlapping = merged nets in Proteus).
    Experimental: new designs may fail to load in Proteus; verify with open_project + screenshot."""
    with _LOCK:
        return gen.build(ws.project(name), parts, wires)


# ---- GUI (one-shot: Xvfb + Wine + Proteus live inside the sandbox only for the duration of ONE call) ------------------
def _image(p: Path, trim: bool = True, crop: list[int] | None = None) -> list[Any]:
    for _ in range(40):
        if p.exists():
            break
        time.sleep(0.25)
    time.sleep(0.3)
    if crop:
        gui.crop(p, tuple(crop))
    elif trim:
        gui.trim(p)
    return [str(p), Image(path=str(p))]


@tool()
def gui_run(project: str, actions: list[dict[str, Any]], load_wait: float = 40, max_seconds: float = 300) -> list[Any]:
    """Open a Proteus project in the sandbox, perform `actions`, return the screenshots, then shut EVERYTHING down (Wine, Xvfb, Proteus)
    before the call returns: nothing keeps running between requests. Startup costs 15-40 s per call (raise load_wait for big designs),
    so put all steps of a task into one call. actions (in order), each a dict:
      {"do":"wait","s":5} | {"do":"click","x":1200,"y":560} | {"do":"click_design","x":-1900,"y":-1000} (thou; toggles a LOGICSTATE/DIP switch)
      {"do":"key","keys":"F12"} | {"do":"type","text":"..."} | {"do":"simulate","mode":"graph|animated","graph":2,"wait":15}
      {"do":"view_graph","index":0,"name":"g"} (double-click the graph title bar: full-size Proteus graph; picture returned)
      {"do":"close_graph"} | {"do":"windows"} | {"do":"dismiss","keys":"Return"} (close an error/recovery dialog) | {"do":"screenshot","name":"shot","trim":true,"crop":[x0,y0,x1,y1]}
    Screen is 1920x1080. A screenshot is always appended at the end."""
    with _LOCK:
        log: list = []
        imgs: list = []
        n = 0
        with session.once(project, load_wait, max_seconds) as s:
            dlg = s.dialogs()
            if dlg:
                log.append({"dialogs_after_load": dlg})
                n += 1
                imgs += _image(s.shot(f"{project}_dialog"))
            for a in actions:
                do = a.get("do")
                if do == "wait":
                    s.sleep(float(a.get("s", 1)))
                elif do == "click":
                    s.click(int(a["x"]), int(a["y"]))
                elif do == "click_design":
                    s.click(round(_PX0 + _K * a["x"]), round(_PY0 - _K * a["y"]))
                elif do == "key":
                    s.key(a["keys"])
                elif do == "type":
                    s.type(a["text"])
                elif do == "dismiss":
                    s.key(a.get("keys", "Return"))
                    s.sleep(1)
                elif do == "windows":
                    log.append({"windows": s.windows()})
                elif do == "simulate":
                    s.click(1200, 560)
                    s.sleep(1)
                    if a.get("graph"):
                        s.select_graph(int(a["graph"]))
                    s.key("F12" if a.get("mode", "graph") == "animated" else "space")
                    s.sleep(float(a.get("wait", 15)))
                elif do == "view_graph":
                    probe = s.shot("_find_graph")
                    s.sleep(0.5)
                    bars = gui.find_graph_bars(probe)
                    i = int(a.get("index", 0))
                    if i >= len(bars):
                        raise RuntimeError(f"graph #{i} not found ({len(bars)} on the schematic); simulate first")
                    s.double_click(bars[i][0], bars[i][1])
                    s.sleep(4)
                    n += 1
                    imgs += _image(s.shot(a.get("name", f"{project}_graph{n}")))
                elif do == "close_graph":
                    s.click(*gui.TAB_SCHEMATIC)
                    s.sleep(1)
                elif do == "screenshot":
                    n += 1
                    imgs += _image(s.shot(a.get("name", f"{project}_shot{n}")), a.get("trim", True), a.get("crop"))
                else:
                    raise ValueError(f"unknown action {a!r}")
            n += 1
            imgs += _image(s.shot(f"{project}_final"))
        return [json.dumps({"log": log, "closed": True}), *imgs]


@tool()
def simulate_project(name: str, key: str = "space", wait: float = 30, load_wait: float = 40, graph: int | None = None, max_seconds: float = 300,
                     use_cache: bool = True) -> ProjectRunResult:
    """Bridge GUI -> numbers: open a Proteus project, trigger its simulation (space = checked graph or `graph` #n, F12 = animated), capture the
    netlist(s) ISIS hands to ProSPICE, close everything, then re-run them headless. Each captured partition becomes a run `<name>_p0`,
    `<name>_p1`... usable with measure / derive / plot_run / sweep. A screenshot `<name>_graph.png` of Proteus's own graph is saved too.
    The captured netlists are cached by project content: repeating the call for an unchanged project skips the GUI entirely (use_cache=False to force)."""
    with _LOCK:
        key_hash = hashlib.sha256(ws.project(name).read_bytes() + f"|{key}|{graph}".encode()).hexdigest()[:16]
        cache = ws.exchange() / "sdfcache" / key_hash
        files = sorted(cache.glob("LISA*.SDF")) if use_cache and cache.exists() else []
        cached = bool(files)
        shot = ws.exchange() / f"{name}_graph.png"
        if not files:
            with session.once(name, load_wait, max_seconds) as s:
                files = s.capture_sdf(key, wait, tag=name, graph=graph)
                s.shot(f"{name}_graph")
            if files:
                cache.mkdir(parents=True, exist_ok=True)
                for f in files:
                    shutil.copy(f, cache / f.name)
        runs: list[SimResult | dict[str, Any]] = []
        for i, f in enumerate(files):
            try:
                runs.append(sim.run_sdf(f, f"{name}_p{i}"))
            except Exception as e:                      # noqa: BLE001
                runs.append({"name": f"{name}_p{i}", "error": str(e)[:300], "sdf": str(f)})
        return ProjectRunResult(captured=[str(f) for f in files], runs=runs, screenshot=str(shot) if shot.exists() else "", from_cache=cached)


@tool()
def run_sdf(sdf_name: str, name: str | None = None) -> SimResult:
    """Headless-run an ISIS SDF netlist already in the sandbox (e.g. a cap_<project>/LISA*.SDF from simulate_project) and decode it."""
    with _LOCK:
        p = next(iter(ws.exchange().rglob(sdf_name)), None)
        if p is None:
            raise FileNotFoundError(sdf_name)
        return sim.run_sdf(p, name or p.stem)


@tool()
def reset_sandbox() -> str:
    """Discard the working Wine prefix; re-created from the pristine snapshot on next use."""
    with _LOCK:
        ws.reset()
        return "ok"


# ---- numeric simulation (headless PROSPICE, no GUI) ---------------------------------------------------------
@tool()
def simulate(spec: dict[str, Any], name: str = "sim", max_rows: int = 40) -> SimResult:
    """Simulate a circuit headless with Proteus's own engine (ProSPICE) and return numbers. Fast (~2 s), no GUI.
    spec = {"parts":[{"ref":"R1","kind":"R","nodes":["in","out"],"value":"1k"}, ...],
            "analysis": {"type":"op"} | {"type":"transient","tstop":"5m","tmax":"5u"} |
                        {"type":"ac","fstart":10,"fstop":"1M","points":20,"mode":"dec"} |
                        {"type":"dc","source":"V1","start":0,"stop":5,"step":0.1},
            "probes":["v(out)","i(R1)"],            # default: all node voltages; i(ref) inserts a current probe (current INTO pin 1)
            "models":{"MYD":{"type":"D","params":{"IS":"1n"}}}, "temp":27}
    kinds: R C L (value: 1k 10u 2.2M... ISIS notation: m=milli, M=mega) | V I (value=DC; "wave":{"type":"sine","amp":1,"freq":1000}
    | pulse{low,high,delay,rise,fall,width,period} | exp | sffm | pwl{points:[[t,v]]}; "ac":true marks the AC source) |
    D Q QP J JP M MP (nodes D:[A,K] Q:[C,B,E] J:[D,G,S] M:[D,G,S,B]; "model": a Proteus library name like 2N3904, 1N4148 or one in spec.models) |
    E G (nodes [out+,out-,ctrl+,ctrl-], value=gain) | OPAMP (ideal, nodes [in+,in-,out], optional gain/rout) |
    X (SPICE subcircuit: "subckt":"TL072" from Proteus's libraries or text in spec["subckts"]; nodes in the subcircuit's port order,
    e.g. op-amps [in+, in-, V+, V-, out]). Node "0" is ground. SPICE sign conventions for I and G.
    Returns summary numbers (stats / gain / op), a downsampled table and a full CSV path; use measure() for more."""
    with _LOCK:
        return sim.simulate(spec, name, max_rows)


@tool()
def simulate_spice(deck: str, name: str = "sim", analysis_index: int = 0, max_rows: int = 40) -> SimResult:
    """Simulate a SPICE deck (R C L V I D Q J M E G X, .MODEL .SUBCKT .PARAM, .OP/.TRAN/.AC/.DC, .PRINT/.PROBE) with Proteus's
    engine. Library models/subcircuits (e.g. 2N3904, 1N4148, LM324, OP07 as X) are resolved from Proteus's libraries. SPICE number
    notation (m=milli, MEG=mega). Pick which analysis card to run with analysis_index. Not supported yet: F H K S W B, .INCLUDE."""
    with _LOCK:
        spec = spice.parse(deck)
        if spec["analyses"]:
            spec["analysis"] = spec["analyses"][analysis_index]
        return sim.simulate(spec, name, max_rows)


@tool()
def measure(name: str, what: str, probe: str, f0: float | None = None, x: float | None = None, periods: int = 5,
            nharm: int = 10, ref: str = "first", after: float = 0.0, interp: str = "linear") -> dict[str, Any]:
    """Measure on a finished simulate() run. what: stats (min/max/mean/rms) | at (value at x) | bandwidth (-3 dB of an AC probe;
    ref=first|peak) | harmonics (THD of a transient probe at fundamental f0 over the last `periods` periods) | edge (10-90 % time, overshoot).
    interp: "linear" (default, how Proteus draws traces) or "hold" (step between solver samples; use for pulse edges and digital-like signals)."""
    kw = {k: v for k, v in dict(f0=f0, x=x, periods=periods, nharm=nharm, ref=ref, after=after, interp=interp).items() if v is not None}
    return sim.measure_run(name, what, probe, **kw)


@tool()
def derive(name: str, expr: str, points: int = 40, interp: str = "linear") -> dict[str, Any]:
    """Evaluate an expression of a finished run's probes on a grid, e.g. "db(v(out)/v(in))" (gain), "v(in)/i(R1)" (input impedance),
    "v(a)-v(b)", "v(out)*i(R2)" (power). AC probes are complex (answer gives mag/dB/deg). measure() accepts such expressions as `probe`."""
    with _LOCK:
        return sim.derive(name, expr, points, interp)


@tool()
def sweep(spec: dict[str, Any], target: str, values: list[Any], metric: dict[str, Any], name: str = "sweep") -> SweepResult:
    """Parameter sweep: re-simulate `spec` for each value of `target` ("R1" = part value, "temp", "V1.wave.freq", "C1.value") and
    collect one number per run. metric: {"what":"stats","probe":"v(out)","field":"rms"} | {"what":"op","probe":"v(c)"} |
    {"what":"bandwidth","probe":"v(out)","field":"f_high_3db"} | {"what":"harmonics","probe":"v(out)","f0":1000,"field":"thd_pct"}."""
    with _LOCK:
        return sim.sweep(spec, target, values, metric, name)


@tool()
def monte_carlo(spec: dict[str, Any], tolerances: dict[str, Any], metric: dict[str, Any], runs: int = 20, seed: int = 1, distribution: str = "gauss",
                name: str = "mc") -> MonteCarloResult:
    """Monte Carlo over component tolerances: tolerances {"R1":"5%","C1":"10%"} (gauss: sigma = tol/3; uniform: +-tol).
    metric as in sweep(). Returns mean/std/min/max and every run."""
    with _LOCK:
        return sim.monte_carlo(spec, tolerances, metric, runs, seed, distribution, name)


@tool()
def plot_run(name: str, probes: list[str] | None = None, file_name: str = "plot") -> list:
    """Plot a finished simulate() run (AC: gain + phase; others: traces) as a PNG in sandbox/exchange."""
    p = sim.plot(name, probes, file_name)
    return [str(p), Image(path=str(p))]


@tool()
def search_models(query: str, limit: int = 30) -> dict:
    """Search Proteus's libraries by name/description words: 4000+ SPICE models/subcircuits (op-amps, MOSFETs, diodes...) and 37k
    device-library parts (BC547, 2N2222, 1N4007... most map to a generic model usable as `model` in simulate)."""
    return sim.search_models(query, limit)


@tool()
def model_text(name: str) -> str:
    """SPICE text (.MODEL or .SUBCKT) of a Proteus library entry."""
    return sim.model_text(name)


# ---- headless PROSPICE (raw SDF, experimental) ------------------------------------------------------------------
@tool()
def import_sdf(src_path: str, name: str | None = None) -> str:
    """Copy a host .SDF netlist (ISIS SCHEMATIC DESCRIPTION FORMAT) into the sandbox."""
    src = Path(src_path).expanduser()
    n = ws.name_ok(name or src.name)
    shutil.copyfile(src, ws.exchange() / n)
    return n


@tool()
def write_sdf(name: str, text: str) -> str:
    """Write an SDF netlist (sections *PROPERTIES *MODELDEFS *PARTLIST *NETLIST) into the sandbox."""
    n = ws.name_ok(name if name.upper().endswith(".SDF") else name + ".SDF")
    (ws.exchange() / n).write_text(text)
    return n


@tool()
def prospice_run(sdf_name: str, timeout: int = 120) -> dict:
    """Run PROSPICE.EXE headless on an SDF in the sandbox. Experimental: results are binary .PDF (not decoded yet)."""
    with _LOCK:
        return prospice.run(sdf_name, timeout)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
