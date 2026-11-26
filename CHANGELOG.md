# Changelog

## Unreleased
- **Proteus-authored schematics** (`build_native`, `proteus2py.native`): an injected hook (`native/`, i686 mingw DLL, file transport) lets Proteus itself
  place any library device (real Pick Devices), draw wires with its wire tool, place generators/probes/terminals, edit values and sine generators,
  draw graphs with traces and simulate them. Every step is verified through Proteus' own object model and the saved `.pdsprj`.
- `evidence` tool: results exactly as Proteus produces them (Export Graph Data `.dat`, graph views, schematic screenshots, SHA-256 manifest).
- Typed core: pydantic circuit spec (strict validation) and typed results; element/analysis/SPICE-element registries; `py.typed`; ruff incl. annotations.
- New: distortion analysis, `W` switches, `i()` inside `B` sources, digital gates / generic DSIM parts / pattern generators / `d()` probes,
  `interp="hold"`, numpy-accelerated harmonics, netlist cache for `simulate_project`.
- GUI: hard session deadline + watchdog, load detection by screen stability, dialog detection; Windows support (headless native, experimental GUI driver).
- `.pdsprj` generator refactored (byte-identical output), automatic channel router for wires.
- Generic `.pdsprj` reader (Ghidra-decoded object types; all 624 sample projects parse), nets and device mapping, `project_netlist` / `simulate_project_nogui`: simulate a Proteus project without starting Proteus.
- Docs site (mkdocs), golden SDF tests that run in CI without Proteus.

## 0.1.0
First release.
- Headless simulation through Proteus's own engine (ProSPICE): OP, transient, AC, DC sweep (nested), noise; SPICE decks and JSON circuits;
  library models/subcircuits (4000+ SPICE models, 37k device-library parts); measurements, sweeps, Monte Carlo, plots.
- One-shot GUI tools (Xvfb + Wine + Proteus start and stop inside a single call): screenshots, graph simulation, Proteus's own graph view,
  GUI -> numbers bridge (`simulate_project`).
- MCP server (`proteus2py-mcp`) and CLI (`proteus2py doctor|setup|spice`).
- Template-based `.pdsprj` generator for a small device set.
