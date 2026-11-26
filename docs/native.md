# Proteus-authored results

Some readers only trust what Proteus itself produced. Everything in this chapter is done by the real Proteus 8 GUI,
driven inside the same one-shot sandbox as the other GUI tools; Python only presses the buttons and checks the result.

```text
spec (parts, wires, graphs)  ->  build_native  ->  .pdsprj written by Proteus
                                       |
                                       v
                            evidence  ->  .dat from "Export Graph Data", graph views, screenshots, SHA-256 manifest
```

## build_native

```python
from proteus2py import native

native.build_native("rc", parts=[
    {"id": "G1", "kind": "generator", "device": "SINE", "x": 0, "y": 0, "name": "VIN", "amplitude": "1", "frequency": "1k"},
    {"id": "R1", "device": "RES", "x": 1500, "y": 0, "value": "1k"},
    {"id": "C1", "device": "CAP", "x": 3000, "y": -800, "value": "100n"},
    {"id": "GND", "kind": "terminal", "device": "GROUND", "x": 3000, "y": -1600},
    {"id": "P1", "kind": "probe", "device": "VOLTAGE", "x": 4500, "y": -300},
], wires=[
    {"from": "G1.0", "to": "R1.0"}, {"from": "R1.1", "to": "C1.0"}, {"from": "C1.1", "to": "GND.GND"}, {"from": "P1.0", "to": "C1.0"},
], graphs=[
    {"type": "ANALOGUE", "x1": 500, "y1": -2000, "x2": 5500, "y2": -4500, "title": "RC LOW-PASS", "stop": "5m",
     "traces": [{"name": "Vin", "probes": ["G1"]}, {"name": "Vout", "probes": ["P1"]}]},
])
```

Then call the `evidence` tool on `rc`: it opens the project in Proteus, simulates the graph, exports its data and pictures.

* `device` is the exact name in the Proteus library (any of the ~10 000 devices of the install): `RES`, `CAP`, `74HC00`, `LM358` ...
* Coordinates are thou. Keep clear of `(-1500, 1500)`, the logic state of the start project that is used to calibrate the view.
* Pins are addressed by name or index. Positions are asked from Proteus itself (it reports the real connection point of every pin).
* A run takes minutes (about 25 s per part, a few more per graph); nothing keeps running afterwards.

## How it works

`native/p2hook.dll` is injected into `PDS.EXE` (`p2launch.exe --attach`). It serves requests written as files
(`hook.req` / `hook.resp`): read memory, call a function, scan for objects. The Python side walks Proteus' live design
(the object list of the global DESIGN object), reads component positions and pins, and uses the GUI only for actions:
Pick Devices (sorted by name, then type-ahead), placement clicks, the wire tool (click pin, click pin), the context menu for
terminals, the Add Trace / Edit Graph dialogs and Space for the graph simulation. Every step is checked against the object
list; the saved project is read back with the package's own `.pdsprj` reader.

Limits: Linux sandbox only; edits of generators other than SINE and of most instruments are not scripted yet; the hook
relies on the layout of the Proteus 8.11 build it was developed against (`DESIGN_VT` in `native.py`); overlapping parts or
wires crossing other pins are not checked.

## Building the hook

```bash
sudo apt install gcc-mingw-w64-i686
make -C native            # development: p2hook.dll and p2launch.exe next to the sources
make -C native package    # copy into the package (the release workflow does this before building the wheel)
```

`proteus2py doctor` reports whether the hook is available.
