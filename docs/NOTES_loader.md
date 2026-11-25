# Proteus 8.11 ROOT.DSN reverse-engineering notes

(Static analysis of ISIS.DLL / LXLCORE.dll / PRIMS.dll / VGDVC.DLL with Ghidra headless.
Scripts: scripts/Dec.java + scripts/run.sh, decompile dumps in gh/*.txt. Parser: pdsfmt.py)

## ANSWER (problem 2) -- why w1-w3 crash and r4/p2 say "Bad object record"

**Wires are NOT free-standing objects and the pin-slot u32 is NOT a pointer that is dereferenced.**
The u32 in a pin slot is an opaque *pairing key* (Proteus writes the file offset of the wire body, but the
reader only compares keys for equality; any unique non-zero u32 works).

Reader algorithm (ISIS.DLL `FUN_1002bc50` object loop, `FUN_1005e330` link read, `FUN_1008e640` wire body):

1. Read objects one by one. For every object, read its link slots in slot order (`FUN_1005e330`):
   * key == 0: nothing.
   * key seen before (in a global pending list): a connection (wire) is created between the earlier
     (owner, pin position) and this one, and queued.
   * otherwise the key is put on the pending list (waiting for its partner).
2. **Immediately after each object, the reader reads one wire body for EVERY connection that this object just
   completed** (loop `while (queue) FUN_1008e640(file)`), in the order the pairs were completed (= slot order of
   the *later* end).  So the wire body must sit in the stream right after the object that carries the SECOND
   occurrence of its key.  (Only components/junctions/terminals consume the queue; there is no type byte on wires.)
3. Anything else in that position is parsed as a wire body -> desync -> garbage type byte -> error 0x241c
   "Bad object record - circuit data lost." (type byte not in the switch) or a crash inside LXLCORE (VFILE/DPKB read
   with a garbage length, e.g. `access violation ... LXLCORE.DLL [00003042]`).

Consequences:

* **(a) w1/w2/w3 crash**: the old generator put all wire bodies at the very end of the object stream.  That only
  works if every wire's second endpoint belongs to the LAST object of the file (w4: RAM is last, so both pairs complete
  at RAM).  In w1-w3 the RAM comes first and the LOGICSTATEs after it, so the pairs complete at the 2nd object (LS1)
  where the reader expects a wire body but finds LS2's record -> desync.  Fix: emit each wire body right after the
  later of its two endpoint owners (pdsfmt.assemble() does this).  Equivalent fix without reordering parts: none.
* **(b) r4/p2 "Bad object record"**: a pin that has 2+ wires was encoded as "pin slot -> zero-length wire", but a
  wire needs a key on BOTH ends and every end must be a link slot (component pin, junction slot or terminal).
  The extra wires had only one key occurrence (second end = empty slot), so pairs never completed at the expected
  places; wire bodies were read at wrong positions -> desync.  **Real encoding of a dot/junction is a JUNCTION
  object, type byte 0x01**: `01 <x i32> <y i32> <4 x u32 link slots>` (ISIS.DLL `FUN_1002b9e0`, class size 0x74).
  Each wire connects exactly two link slots (pin<->pin, pin<->junction slot, junction<->junction).  Example from
  GUI-saved m1.pdsprj (all coordinates in thousandth-inch):
  `JUNCTION(400,500) slots=[w1,w2,w3,0]`, w1 = U4.pin0 -> J.slot0 (pts (1400,500)->(400,500)),
  w2 = U3.pin14 -> J.slot1 (**zero-length wire** (400,500),(400,500): the pin tip sits exactly on the junction),
  w3 = U4.pin2 -> J.slot2 (pts (1400,400),(400,400),(400,500)).  The junction object comes AFTER all its
  pin owners in the stream, so its 3 wire bodies follow the junction record, in J.slot order.
  A junction has 4 slots -> max 4 wire ends per dot; chain junctions for more.

Further rules established while reading the code:

* Wire body = `u8 kind(0=wire,1=bus)`, `GSTYLE DPKB (u32 size incl. itself; payload for normal wire is the 25 bytes
  "00 c0 9e 00 00 00 40 00 00 01 ff ff ff 00 ff ff ff 00 02 7f 'WIRE' 00")`, `u16 nlabels`, labels, `u16 npts`,
  `npts x (i32 x, i32 y)` (units thousandth-inch*2540).  There is no flag byte (VFILE flag 2 is clear in 8.11 files).
* **The first and last polyline points are overwritten by the reader** with the real pin-tip positions
  (`FUN_1008e640`: `pts[0]=start`, `pts[n-1]=end`), then `FUN_1005ed80` checks that each end coincides with a pin of the
  owner; if not (or pin already used) the wire is silently deleted.  Start = the owner whose slot was read FIRST in the
  file, end = the owner that completes the pair.  So pts[0] must belong to the earlier owner; if you write the
  polyline in reverse order, the middle points end up in reversed order (wrong geometry with >=4 points).
* n >= 2 points always (zero-length wire = 2 equal points is fine).
* Slot arrays: COMPONENT has one u32 per LIBRARY pin (`RLIB_ENTRY+0x8c` pin count); if the file's pin count `u16` is
  larger than the library's -> error 0x241d ("Pinout mismatch for type '%s'. Wiring will be lost."); device missing
  -> error 0x241e ("Device '%s' used but not in library.") and the record is skipped/deleted (its slots are still read).
* Object order matters ONLY through the wire-after-completing-object rule above (no other ordering constraint).
  A junction/terminal/component can be anywhere as long as its wires follow the later endpoint.
* A key must appear at most twice.  A key that appears once just stays pending (harmless, wire is simply missing).

## Object stream grammar (verified by pdsfmt.py on w4,k2,r1,g5,m1; w1/r4/p2 reproduce the desync)

Header: file version at ROOT.DSN[0x15+0x94] (u16) is 828 (0x33c) in all 8.11 files; reader accepts 300..0x33c.
Stream starts after the 12 bytes `OBJECT DATA\0`; loop `type=u8; 0xff => end`.

| type | class (ctor) | body (read fn) |
|------|--------------|----------------|
| 0x00 | COMPONENT (`FUN_100604a0`, 700 B) | COMPONENT body |
| 0x08 | COMPONENT variant (`FUN_10003550`, 0x38c B; written when lib device `+0x50`!=0, e.g. LOGICSTATE, LOGICPROBE, DIPSW, 7SEG; read = COMPONENT read then drops props ACTIVE/ACTUATOR/INDICATOR) | COMPONENT body |
| 0x01 | JUNCTION (`FUN_10060680`) | `x i32, y i32, 4 x u32 slot` |
| 0x02 | TEXT SCRIPT? (`FUN_10060b90`) | `CTS(12), TEXTBLOCK` |
| 0x05 | `FUN_10060d90` | `BOX(16), u16, LABEL, LABEL, TEXTBLOCK` |
| 0x06 | `FUN_10060990` | DPKB blob |
| 0x07 | `FUN_10060040` | `BOX` |
| 0x10 | TERMINAL (`FUN_10060fd0`) | `CTS, pstr name, u16 type, u32, LABEL, 1 slot` |
| 0x20,0x21,0x22,0x28-0x2e,0x30-0x32,0x38-0x41,0x80-0x83,0xb8-0xc0 | graphics/other (not decoded) | see `FUN_1002bc50` switch |
| 0xff | end | |

Anything else => errbox(0x241c) "Bad object record - circuit data lost." (this is the ONLY place that string is used).

COMPONENT body (`FUN_1002b490`), all little endian:
```
LABEL id, LABEL value, LABEL subckt-name, TEXTBLOCK props("PROPERTIES")
u16 npins            # pins stored in file
pstr device name     # u8 len + chars (vfgetstr), looked up in the design's device library
CTS(12)              # x i32, y i32, u16 rotation/orientation word, u16 flags
u32 serial           # (+12 from the CTS start; the "serial" the CDB keys on)
u32 aux
u8  flags            # bit0..5 = [0xa0..0xa5] booleans
u32 extra            # present for file version > 0x32c && !(vfile flag 0x10)
u32 slot[npins]      # link keys, library pin order
(no trailing byte: the optional pin-name table exists only if VFILE flag 2, which is clear)
LABEL  = 0xff, pstr text, CTS(12), u8 b44(0x80=utf8), u8 b45, DPKB tstyle(u32 size incl itself, payload: font height,
         width, ... 'Default Font\0' '<style name>\0 ...')       (PRIMS.dll LABEL::read)
TEXTBLOCK = LABEL, u32 len, [len bytes of text only if len > 0x32; shorter text lives in the label text]
```
Coordinates are i32 in units of `fileunit` (here 2540 per thousandth inch... i.e. value/2540 = 1/1000 inch grid used in
the tables of this project).

## Open / not yet done
see bottom sections (CDB, PROJECT.XML, interfaces) - filled in below as they are established.

## Confirmed by d1/d3 (two wires on one pin tip, no dot)
pdsfmt.py on d1/d3: the pin has ONE slot, so only the first wire gets a key on that end. The 2nd LS<->pin wire
(d1: key 0x216ef on LS2 only; U1 pin11 holds 0x21731 which has no partner) never completes a pair, so no wire body is
expected where the generator wrote it and the reader hits the stray body (`00 1d 00 00 00 ...`) as a type-0 COMPONENT
-> LABEL expects 0xff, got 0x1d -> desync -> "Bad object record". Same cause as r4/p2: a dot wire is not a fix.
Rule: >=2 wires on a pin tip => put a JUNCTION object (type 1) at the tip; pin slot -> zero-length wire -> J slot;
each other wire goes J slot <-> its other pin. Use pdsfmt.assemble()/make_junction()/splice_stream().

## Interfaces found (problem 4, partial)
* IMPORTER.dll: ECAD import "PDIF PROJECT 1.0 (*.pdif)" and OrCAD EDIF 2 (*.edf/.edif). PDIF is XML (error text
  "Parsing stopped at line/column"; root tag PDIF, vendor Labcenter). Schematic importer builds the design through ISIS
  itself (no binary pitfalls). Tag vocabulary seen: part, element, gate, pin, sheet, module, circuit, subcircuit, node,
  wire, route, junction, tap, connector, terminal, text, font, bounds, style, PROPS, title, uid, units(THOU), scope
  (GLOBAL/PARENT), range. Attribute grammar NOT yet decoded (would need PDIFSchematicImporter decompile).
* Other BIN files: DSNCVT40.DLL/IDSCVT40/LYTCVT (legacy converters), LUA.DLL, PYPROC.exe (sandboxed python for RPi),
  CONSOLE.EXE, GETSPICE/PUTSPICE, OBJITF.DLL (.NET stub), SDFGEN. ISIS has "ASCII Data Import" (ADI), NETLIST export
  (NETLIST.dll: NETLIST::write, parse_*). PDS.EXE prints "Invalid command-line arguments ignored"/"filename and/or path
  ignored" and accepts an IDE .INI argument; switch names not yet decoded (strings are in a table, no direct xrefs).
* CDB: CDBCORE::load/save live in NETLIST.dll (gh/n1.txt, gh/n2.txt). Layout: u32 version(5); u32 n + modules; sheets
  (u32 id,u32,u32,pstr name,u32,u32); elements (readelement: u32 id,u32 link,u32 link,u32,pstr ref,u32 npins x
  (pstr name,pstr num),u32 blen+blob,u32 link,u32); u32 n; parts (readpart); snippets. It is a derived cache; an empty
  116-byte CDB loads fine, so keep using the empty one.
* PROJECT.XML: TIMESTAMP RELEASE=811 FILEVER=828 MODIFIED/CREATED unix times, CUSTID; VARIANTS; SCRIPTS/STRING.
* Sheet table after the last circuit section: u32 before the first 'ISIS CIRCUIT FILE' points to it; entries
  (id, pstr name, u16, u32 offset of that sheet's section); splice_stream() shifts them.
