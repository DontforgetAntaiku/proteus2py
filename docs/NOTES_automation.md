# Proteus 8.11 automation routes (re/auto) -- findings

Test rig: `sbx.sh` (bwrap + throwaway prefix copy `prefix/`), `guirun.sh` (private Xvfb :98 inside bwrap, runs PDS.EXE, screenshots to work/NAME.png),
`run_sdf.sh` (headless ProSPICE), `run_prospice.sh`. Captured samples in `work/cap*/` (real LISA*.SDF written by ISIS).
Strings/PE dumps in `str/`, `pe/`, annotated disassembly in `dis/` (PDS.EXE only; Ghidra NOT run -- other agents had 2 JVMs going).

## Ranked routes

1. **PROSPICE.EXE on a text SDF netlist -- VERIFIED headless, no X, ~1 s startup.**
   `wine BIN\PROSPICE.EXE -M:<DATA\MODELS> file.SDF` with env `RESULTS=<dir>` runs the whole ngspice-derived analog/digital
   engine on "ISIS SCHEMATIC DESCRIPTION FORMAT 8.0" text (sections *PROPERTIES, *MODELDEFS, *PARTLIST, *NETLIST).
   - ISIS itself writes this file as `LISA<n>.SDF` when a simulation starts (deleted right after; captured with a polling copy loop). Samples: work/cap/LISA3988.SDF.* (op-amp, SIMTYPE=TRANSIENT, tstop absent = interactive), work/cap2/LISA4096.SDF.* (graph sim: TSTOP=1, VPULSE! generator `G...`, APROBE probes). Also shipped: DATA/VSM Studio/controls/*/LISA1.SDF (MCU design).
   - Part line: `REF,LIBNAME,VALUE,PROP=..,PRIMITIVE=PASSIVE|ANALOG|ACTIVE|PROBE|DIGITAL,PRIMTYPE=RESISTOR|CAPACITOR|...`; net: `NAME,count` then `REF,PINTYPE,PINNAME` (PS passive, IP/OP/IO, PP power pin, PT/GT/PR terminals, IT/OT).
     Subcircuit devices (741) are already flattened by ISIS into U1_* parts. Device types known from `PROSPICE.EXE -T` dump (work/tables.txt: 1354 lines of analyses OP/DC/AC/TRAN/NOISE/... and device params; PRIMTYPE vocabulary RESISTOR CAPACITOR INDUCTOR DIODE NPN PNP NJF NMOS.. VSOURCE VSINE VPULSE ISOURCE BATTERY VCISOURCE SUBCKT VSWITCH ...).
   - Result: `RESULTS=C:\work\res` -> `res/<PARTITION>.PDF` (3 KB for 3 probes; header "LISA PARTITION DATA FILE", probe names, doubles). Binary layout NOT decoded yet. RESULTS with a trailing file name -> access violation (#X), directory form works.
   - PROSPICE.EXE switches (disasm main @0x401000): `-M:dir` (sets MODELS env), `-W:n`, `-T` (dump tables), first non-switch arg = netlist (default ext .SDF). Messages prefixed `#I:` `#F:` `#X:`; rc 0 ok, 3 on exception.
   - Missing model DLL (e.g. MMC.DLL) gives `#F:External model DLL ... not found` unless -M points at DATA\MODELS.
   - ESTABLISHED: runs, parses, loads models, writes PDF. GUESSED: that a hand-written SDF for new circuits works (not yet written from scratch; the captured ones were only replayed), time-limit for interactive (no TSTOP) sims.
   - Text-import value: a generator can emit SDF directly (no .DSN) for **simulation numbers**; it does NOT give a schematic picture/animation.

2. **`PDS.EXE <file.pdsprj>` opens a project from the command line -- VERIFIED** (window title "asim1 - Proteus 8 Professional - Schematic Capture" after ~35 s under Xvfb).
   Switch parser at PDS.EXE 0x414410 (tokens starting `-` or `/`, letter via toupper): `-A` and `-H` (set no-startup/hidden-home flags 0xc58, A also 0x2120), `-P[:file]` (IDE ini; default PROTON.INI), `-Q` (clear 0xc54), `-R` (0xc50=1), `-T[:file]`, `-activex`/`-Embedding` (OLE launch, 0x2124). Unknown switch -> modal "Invalid command-line arguments ignored: <rest>" (verified with -zzz; dialog needs OK). Only -P and plain file path verified; A/H/Q/R/T semantics are guesses from flag use.
   Output: keyboard drive after load works: click canvas then `xdotool key F12` (run animated sim, creates Simulation Results\<PART>.PDF and LISA*.SDF) and `space` (graph sim; traces drawn). Screenshot = `import -window root`. Keys without a prior canvas click did not work (`--window` form fails under Wine).

3. **IDE interface via INI: `PDS.EXE -P:C:\x.ini` -- VERIFIED parse/load.** INI `[PROTEUS]` keys (PDS.EXE 0x415b90): DESIGNFILE, PROGRAM, CLOCK, DEVICE, FAMILY (default PIC), NEWDESIGN, AUTORUN, VDMPORT. DESIGNFILE/PROGRAM/CLOCK must all be set (default "None" aborts). Test with a non-MCU design gave "program written for a AVR type ATMEGA328P and a matching processor cannot be found on the schematic" => path is MCU-centric; with a matching MCU in the schematic AUTORUN=1 should start the sim (code sends command 0x248 to the VSMDEBUG module) -- UNTESTED. VDMPORT starts the Keil "Virtual Debug Monitor" TCP server (VSMDEBUG.DLL strings: "LABCENTER-ISIS-VDM", "VDM Port", "Enable Remote Debug Monitor ... Keil uVision2"); protocol unreversed.

4. **ECAD importer (IMPORTER.dll): EDIF 2 0 0 (Orcad) and PDIF project text -> schematic/.pdsprj -- static evidence only.** Strings: file filter "PDIF PROJECT 1.0 (*.pdif);;ORCAD EDIF 2 (*.edf *.edif)", "Parsing schematic page", "Placing sheet '%1': Elements/Nets", "Schematic saved successfully", "Comparing netlists". Reached from PDS.EXE File/Home "Import ECAD Files" (GUI wizard: source file, Import, Save) -- no CLI switch found. Components come from EDIF cell symbol views (device created via `{*DEVICE}` template with PREFIX/PACKAGE), so result is likely simulation-less symbols; untested. Same DLL exports a Qt COM LocalServer32 `ImportServer` (CLSID {1D9928BD-4453-4bdd-903D-E525ED17FDE5}, methods enumerateLibraries/setTargetLibrary/authorizeImport/itemExists/importPart(path,format)/getLog) for LIBRARY part import only. Internal C++ interfaces IAUTOHOST/IAUTODRAW (ISIS.DLL, ARES.DLL) and IAUTOCLIENT (IMPORTER) are what the importer uses to place parts -- in-process vtables, no external COM registration seen.
   "Import Legacy Project" (LegacyImportWizard in PDS.EXE) takes binary Proteus 7 DSN/LYT via DSNCVT40/IDSCVT40/LYTCVT (binary->binary, not useful for text).

5. DDE: WINCORE.dll exports ddesvr_*/ddeclnt_* wrappers (DDESERVER), ISIS uses DDE only as client to a source-code editor ("DDE Interface: Service name / Open command / Save command" dialog) and to launch ARES. No ISIS DDE server service name found. Low value.

6. Other tools in BIN: SDFGEN.EXE (other netlist formats -> SDF 3.0, old ARES-style SDF, `*GATES`), GETSPICE/PUTSPICE/GETMDF/PUTMDF (SPICE .LIB <-> PROSPICE.SML, MODELS.LML library managers; CLI with -L= -A -D), PYPROC.exe / Tools\Python (python36) / Tools\VSMPY\mkpython, LUA.DLL + LUAC.EXE (used for VSM Studio controls JS/Lua, no ISIS scripting hook found), IFLIST.EXE (pcap interface list), CONSOLE.EXE == PROSPICE.EXE (byte-identical). No ISIS scripting facility (no VBA/Lua entry) was found in strings; `SCRIPTS` in PDS.EXE is a project-zip entry name, `Edit Script`/`Assigned Script` are design-notes scripts.

## Not done / next steps
- Decode res/*.PDF (probe traces) and write a from-scratch SDF (R + VSOURCE) to prove text-to-sim end-to-end; then an MCP tool `simulate_sdf`.
- Try EDIF import through GUI with a minimal EDIF; test `-A/-H` on startup; test AUTORUN with an MCU sample (Arduino sample under DATA/VSM Studio/devboards).
- Ghidra on PDS.EXE (small, 1.3 MB) once other JVMs finish, to confirm switch semantics.
- Export bitmap/PDF: ISIS dialogs "Export Bitmap/Metafile/PDF/SVG" exist (ISIS.DLL resources 1062-1094) but are menu-only; no CLI. Screenshot via X is the verified path.
