# Proteus 8 `.pdsprj` format notes and the template generator

Findings from generating Proteus 8.11 projects programmatically (`proteus2py.gen`, built on `proteus2py/_gen`). Interoperability notes only;
no Labcenter code is included.

## File format (details: NOTES_loader.md, NOTES_cdb.md)
- `.pdsprj` = zip: `PROJECT.XML`, `ROOT.CDB`, `ROOT.DSN`, `SCRIPTS/PWRRAILS.DAT`. The CDB is a derived cache but a **full CDB is required**
  (an empty one gives "Duplicate part reference" at netlist compile). Generate with `cdbfmt.build_cdb`.
- `ROOT.DSN`: design config + device-library blocks (copied verbatim from a GUI-saved seed containing each device) + `ISIS CIRCUIT FILE ... OBJECT DATA`
  + object stream + tail. Two u32 section pointers (before the first marker; inside the tail) shift with the object-stream size.
- Coordinates: int32 = thou x 2540. Component main record: pstr device name, x, y, ..., serial at x_off+12, one u32 link slot per library pin at the end.
- **Wires are not independent objects**: the wire body must be placed immediately after the object carrying the 2nd occurrence of its key; pin tips overwrite
  the first/last points on load. A pin with two or more wires needs a JUNCTION object (type 1) and a zero-length wire pin -> junction. Violations give
  "Bad object record - circuit data lost" or access violations. `pdsfmt.assemble()` does this correctly.
- Part orientation: u16 at (main x offset + 10): bit0 = mirror X, bit1 = mirror Y.
- Part templates: copy an unwired instance record from a GUI-saved seed, patch coordinate fields, serial, optional reference text (shift later offsets), PROPERTIES text.

## Supported devices (`gen.build`)
`LS` (LOGICSTATE), `6116`, `74HC374`, `DIPSW_8`, `BCD`/`BLUE`/`GRN` (7-segment). Other devices need a GUI-saved seed that contains them (the device-library block
grammar is not fully decoded) or the PDIF/EDIF importer (GUI only).

## Automation facts
`PDS.EXE file.pdsprj` opens a project; one canvas click, then F12 (animated) or Space (graph). There is no scripting/COM/DDE server; ProSPICE is driven headless instead
(see NOTES_prospice.md). Opening a large design under Wine can take minutes; the first load after a start is flaky, so verify with a screenshot.
