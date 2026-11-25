# proteus2py MCP server

`uv run proteus2py-mcp` (see `.mcp.json`). Two independent halves, both running Proteus **inside a bubblewrap + Wine sandbox**
(no network, empty `$HOME`, only `sandbox/prefix-work` and `sandbox/exchange` writable; your files are copied in, never modified).

## 1. Numbers: headless ProSPICE (fast, ~2 s per run, no GUI)

Proteus's own simulation engine (`PROSPICE.EXE`, the engine behind ISIS graph simulation) is driven directly with generated
`ISIS SCHEMATIC DESCRIPTION FORMAT` netlists (`src/proteus/sdf.py`); its binary result files are decoded (`pdf.py`).

| tool | what |
|---|---|
| `simulate(spec)` | run a JSON circuit; OP / transient / AC / DC sweep (incl. nested DC); returns stats, a table, a CSV path |
| `simulate_spice(deck)` | same from a SPICE deck (`.model .subckt .param .op .tran .ac .dc .print`) |
| `measure(name, what, probe)` | stats, value at x, -3 dB bandwidth, THD/harmonics, rise time/overshoot on a finished run |
| `sweep(spec, target, values, metric)` | re-simulate over a part value / temperature / wave parameter |
| `monte_carlo(spec, tolerances, metric)` | component-tolerance statistics |
| `derive(name, expr)` | expressions of probes: `db(v(out)/v(in))`, `v(in)/i(R1)` (input impedance), power ... |
| `plot_run(name)` | PNG (AC: gain+phase) |
| `simulate_project(name)` | GUI -> numbers: open a real `.pdsprj`, press Space, capture the ISIS netlist, re-run it headless (measure/derive/plot work on it) |
| `search_models(q)`, `model_text(name)` | 4000+ library diodes/BJTs/MOSFETs/op-amps from Proteus's own `.SML` libraries |

### spec
```json
{"parts":[{"ref":"V1","kind":"V","nodes":["in","0"],"ac":true,"wave":{"type":"sine","amp":1,"freq":1000}},
          {"ref":"R1","kind":"R","nodes":["in","out"],"value":"1k"},
          {"ref":"C1","kind":"C","nodes":["out","0"],"value":"159n"}],
 "analysis":{"type":"ac","fstart":10,"fstop":"1M","points":20},
 "probes":["v(out)","i(R1)"]}
```
Kinds: `R C L` (2 nodes), `V I` (`value` = DC, `wave` = sine|pulse|exp|sffm|pwl, `ac:true` marks the AC source),
`D` [A,K] `Q`/`QP` [C,B,E] `J`/`JP` [D,G,S] `M`/`MP` [D,G,S,B] (`model` = library name or entry of `spec.models`),
`E`/`G` [out+,out-,ctrl+,ctrl-], `F`/`H` [out+,out-] + `control` (V source ref), `K` (`inductors`:[L1,L2], value = k), `X` SPICE subcircuit (library name or `spec.subckts`, nodes in port order), `OPAMP` ideal [in+,in-,out]. Node `0` is ground. `i(R1)` inserts a current probe in series
(current **into** pin 1; for sources use SPICE direction).

Value notation in `spec` is **ISIS** (`m`=milli, `M`=mega). SPICE decks and `.MODEL` parameters use **SPICE** notation
(`m`=milli, `MEG`=mega); the importer converts everything to plain numbers.

Convergence failures are retried automatically with looser solver options (`solver_options` in the answer shows what was used).

### Verified against analytic results (2026-11)
divider OP, RC step response (0.03 %), RC AC (-3 dB at 1000.09 Hz, -45 deg), diode I-V sweep, BJT bias point, ideal and real
op-amps (OP07 library subcircuit x11 = 5.5002 V; LM324 follower with ITL1 relaxation), Monte-Carlo sigma, library flattening.

### More verified results
nested DC sweeps (BJT output characteristics, one curve per outer step), thermal noise (R1||R2 = 9.104 nV/rtHz, 1e-4 off theory;
complex result: real = output noise, imag = input-referred), transformers (`K`), current-controlled sources (`F`,`H`), switches (`S`),
behavioural sources (`B ... V={expr}`), library subcircuits with their own F/H elements (TL072), generic Proteus parts
(BC547, 2N2222, 1N4007 ... resolve through the device library to their LX_* generic models).

### Not supported yet
`W` (current switch), `i()` inside `B`, `.INCLUDE/.LIB` of host files (clear error), `.MEAS/.FOUR/.NOISE/.TF` cards (use `measure`,
`analysis: noise`), distortion analysis (use `measure harmonics` on a transient), digital / mixed-mode parts, VSM microcontroller models.

## 2. Pictures: the real Proteus GUI (one-shot, slow: 15-40 s per call)

Xvfb, Wine and Proteus run **only inside one tool call** and live in the bwrap sandbox (own pid namespace, `--die-with-parent`);
they are shut down before the call returns, so nothing keeps running between requests.
`gui_run(project, actions)` does a whole task in one call: simulate (graph/animated, pick graph #n), `view_graph` (Proteus's own
full-size graph), clicks, keys, typing, window list, screenshots (always one final screenshot). `simulate_project` is the GUI -> numbers bridge.
`build_design` writes `.pdsprj` files from seed templates (6116, 74HC374, DIPSW_8, 7-seg, LOGICSTATE only; see `docs/HANDOFF.md`).

## Setup
`proteus2py setup <wine prefix with Proteus 8>` once (about 2 GB; `proteus2py doctor` checks the environment), then the MCP config from `.mcp.json.example`. Tests:
`uv run --with pytest pytest` (unit) and `PROTEUS_INTEGRATION=1 PROTEUS2PY_HOME=$PWD/sandbox uv run --with pytest pytest` (needs Wine).
Never run two sessions on one prefix: use another `PROTEUS2PY_HOME` copy (`cp -a sandbox/prefix-pristine other/prefix-pristine`).
