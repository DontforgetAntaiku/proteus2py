# Analyses and elements

```json
{"parts":[{"ref":"V1","kind":"V","nodes":["in","0"],"ac":true,"wave":{"type":"sine","amp":1,"freq":1000}},
          {"ref":"R1","kind":"R","nodes":["in","out"],"value":"1k"},
          {"ref":"C1","kind":"C","nodes":["out","0"],"value":"159n"}],
 "analysis":{"type":"ac","fstart":10,"fstop":"1M","points":20},
 "probes":["v(out)","i(R1)"]}
```
Specs are validated strictly (a typo such as `"ampl"` is an error with the exact path). Values: ISIS notation (`m` = milli, `M` = mega); SPICE decks
and `.MODEL` parameters use SPICE notation and are converted.

| analysis | fields | result |
|---|---|---|
| `op` | - | node voltages (`op`) |
| `transient` | `tstop`, `tstart`, `tmax` | statistics, table, CSV, digital transitions |
| `ac` | `fstart`, `fstop`, `points` (per decade), `mode` | gain/phase table, -3 dB |
| `dc` | `source`, `start`, `stop`, `step` (+ `source2`... for curve families) | table, `family` |
| `noise` | `output`, `input`, `fstart`, `fstop` | output and input-referred V/sqrt(Hz), totals |
| `distortion` | `output`, `source`, `f0`, `periods`, `settle` | HD2..HDn, THD |

| kind | notes |
|---|---|
| `R C L` | value |
| `V I` | DC value or `wave` (sine, pulse, exp, sffm, pwl); `ac: true` marks the AC reference |
| `D Q QP J JP M MP` | `model`: a Proteus library name (BC547, 1N4148 ...) or an entry of `models` |
| `E G F H` | controlled sources (`control` = V source ref for F/H); SPICE sign conventions |
| `K` | mutual inductance between two `L` parts (`inductors`) |
| `X` | SPICE subcircuit (library op-amps, or text in `subckts`) |
| `OPAMP` | ideal op-amp |
| `GATE` | digital gate (`gate`: AND NAND OR NOR XOR XNOR NOT BUF); inputs first, output last |
| `DGEN` | digital pattern generator (`pattern`, `width`, `init`) |
| `DIGITAL` | any DSIM primitive (`primitive`, `pins` like `["D:IP","Q:OP"]`) |

SPICE decks add `S`, `W` (current switch), `B` (behavioural, including `i()`), `.PARAM`, `.SUBCKT`, `.TRAN/.AC/.DC/.OP`. Probes: `v(node)`, `i(ref)`,
`d(node)` (digital). Convergence problems are retried automatically with looser solver options.

Interpolation: results are sparse solver points; `interp="linear"` (default, how Proteus draws traces) or `"hold"` (steps; use for pulse edges).
