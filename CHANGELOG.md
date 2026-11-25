# Changelog

## 0.1.0
First release.
- Headless simulation through Proteus's own engine (ProSPICE): OP, transient, AC, DC sweep (nested), noise; SPICE decks and JSON circuits;
  library models/subcircuits (4000+ SPICE models, 37k device-library parts); measurements, sweeps, Monte Carlo, plots.
- One-shot GUI tools (Xvfb + Wine + Proteus start and stop inside a single call): screenshots, graph simulation, Proteus's own graph view,
  GUI -> numbers bridge (`simulate_project`).
- MCP server (`proteus2py-mcp`) and CLI (`proteus2py doctor|setup|spice`).
- Template-based `.pdsprj` generator for a small device set.
