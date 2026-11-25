# ProSPICE headless: SDF in, PDF out (reverse-engineered, 2026-11)

Evidence: netlists ISIS itself writes (`re/captures/*/LISA*.SDF`, captured with `tools/capture_sdf.py`), strings of
`PROSPICE.DLL/EXE` (`re/str`), experiments through `src/proteus/prospice.py`. Everything below was verified by running it.

## Run
`wine BIN\PROSPICE.EXE -M:DATA\MODELS x.SDF` with env `RESULTS=C:\exchange\res`; stdout lines `#I:` info, `#W:` warning, `#F:`/`#X:` fatal.
Result: `res/<PARTITION>.PDF` where PARTITION is a `*PROPERTIES` entry (8 hex chars). ~1.5 s startup, no X server needed.

## SDF
```
ISIS SCHEMATIC DESCRIPTION FORMAT 8.0 / =====  / Design: ... (8 header lines)
*PROPERTIES,n        KEY=VALUE   options + analysis + PARTITION
*MODELDEFS,n         NAME : P=V,P=V       (type comes from the part that references it)
*PARTLIST,n          REF,LIB,VALUE,prop=val,...,PRIMITIVE=PASSIVE|ANALOG|ANALOGUE|ACTIVE|PROBE|DIGITAL[,PRIMTYPE=..]
*NETLIST,n           NETNAME,<pin count>  then  REF,PINTYPE,PINNAME   (GND net also lists `GND,GT`)
```
Part types seen/used: RES CAP(PRIMTYPE=CAPACITOR) IND(PRIMTYPE=INDUCTOR) DIODE NPN PNP NJF PJF NMOS PMOS VSOURCE ISOURCE BATTERY VSINE VPULSE
VEXP VSFFM VPWLIN ISINE IPULSE VCISOURCE (output `+`,`-`; control `P`,`N`) CCVS/CCCS (`PROBE=<V source ref>`) ACS (arbitrary current, expression value),
SUBCKT (`SPICEMODEL="NAME,LIBFILE"`, `SPICEPINS="a,b,c"`, pins named by SPICEPINS), generators `G..,VSINE!|VPULSE!|VDC!|IDC!|...,PRIMITIVE=PROBE`,
probes `APROBE` (node, pin `IP,*`), `VPROBE`, `IPROBE` (pins `+`,`-`), `DPROBE` (digital).

Analyses (`*PROPERTIES`): see `src/proteus/circuit.py` (transient / AC `SIMTYPE=FREQUENCY` + `FREQREF` / DC `ANALYSIS=DC` + NAME1..STOP2).
Known unknowns: noise, distortion, Fourier cards (ISIS runs those from separate graphs), mutual inductors (`MUTUAL_<x>=k` on INDUCTOR parts
did not couple in our SDFs), digital/mixed-mode parts, `DPROBE` result encoding.

## PDF (`src/proteus/pdf.py`)
`"LISA PARTITION DATA FILE"`; u32@0x28 probe-table offset, u16@0x2c probe count, u32@0x30 data start; records `FF FF <f64 x>` + `<u16 idx><f64>`
changes (type-16 probes: `<u16><f64 re><f64 im>`), `FE FF` ends; probe table `pstr id, pstr name, u16 1, u16 type(8 real, 16 complex)`.

## Libraries (`src/proteus/library.py`)
`DATA/MODELS/*.SML`: "SPICE LIBRARY", 0x20 header, entries 0x20 bytes (`name[20] u32 off u32 ts u32 aux`), record = u32 length + SPICE text (4100+ models).
`DATA/MODELS/*.LML`: same container for VSM models, but plain `NAME : P=V,..` generic model lines (LX_NPN_SSLF ...) are embedded in the text.
`DATA/LIBRARY/*.LIB`: "DEVICE LIBRARY", entries 0x50 bytes (`name[0x40] u32 off ts aux pad`), record = u32 length + `{*DEVICE}..{*COMPONENT}{MODEL=LX_..,BIPOLAR}{PRIMITIVE=ANALOGUE,NPN}` text
(device name -> generic model; 37k devices with descriptions).
