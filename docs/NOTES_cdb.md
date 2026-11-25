# ROOT.CDB and PROJECT.XML (Proteus 8.11, FILEVER 828)

Files here: `cdbfmt.py` (parser, writer, `build_cdb`, `build_project_xml`, self-tests), `test_seeds.py`.
Method: data analysis of 666 projects (624 DATA/SAMPLES + user files). ISIS.DLL strings were used only for naming (see "Meaning").
No Ghidra, no SSH, sandbox untouched.

Run: `python3 cdbfmt.py dump X.pdsprj`, `python3 cdbfmt.py selftest *.pdsprj`, `python3 test_seeds.py`.

## Test results (all reproducible)
- Structural parse -> write round-trip, byte-exact: 459/459 CDBs of versions 3, 4, 5.
- `build_cdb(parts_from_cdb(parse))` byte-exact: 90/90 single-sheet v5 "Master Sheet" projects.
- `build_cdb(hand-written parts)` byte-exact for the GUI-saved seed, m1, k1 (3004 B), g1 (789 B), g5 (268 B), and `build_cdb([])` == the 116-byte CDB of lab4 (`test_seeds.py`).
- `build_project_xml(**parse_project_xml(x))` byte-exact: 666/666 PROJECT.XML.
- Not parsed: 208 files with CDB version 2 (old layout), 1 odd v3 file (`Arduino TFT LCD`).

## ROOT.CDB grammar (little endian; S8 = u8 length + bytes, no NUL, latin1/ANSI)
```
u32 version      2..5. Tracks FILEVER: 2 (800), 3 (805), 4 (806), 5 (>=813). Write 5.
u32 nmodules     1 for a flat design (53 in Shiftpcb: module tree, see Preamble)
PREAMBLE         opaque, ends with the 8-byte marker  0a 00 00 00 00 00 00 00
u32 n_elements
ELEMENT x n:     u32 serial, u32 sheet_id, u32 group, u32 key,
                 S8 ref, u32 npins, npins x (S8 name, S8 number),
                 u32 bloblen, blob[bloblen],
                 u32 part_idx (0 = no PART record), i32 unit
u32 1, u32 1, u32 1, u32 n_parts
PART x n:        u32 idx, u32 words[4] (v3/v4: 3 words) = [1, sheetno, 0, interactive],
                 S8 ref, S8 value, S8 device, S8 package,
                 u32 L, text[L-5], u8 0
u32 n_sheetitems                      (the last 4 bytes of "L+5" are really this/next idx; same bytes)
SHEETITEM x n:   21 x i32 header, then payload of hdr[15] bytes
```
`L = len(text)+5`. Evidence: in every record the 5 bytes after the text are `00` + the next record's idx (or the final u32), checked on all 459 files.
The file ends right after the last SHEETITEM (empty design: `... 01 01 01 00 | 00 00 00 00`, 116 bytes).

### Preamble
- Every single-sheet project saved by 8.x with the default title is the same 85 bytes (258/258 files): `01 00 00 00 | 00 00 00 00 | S8 "ROOT" | 00 | 00 00 00 00 | 01 01 02 01 02 00000000 (u32 each) | S8 "Master Sheet" | 0a 0 | 02 03 01 (u32) | 00 | marker`.
  `_preamble()` in cdbfmt.py emits this; changing the title is a guess (title position is certain, other words may depend on it).
- Other single-sheet designs use a different pattern: `.. 01 01 Y A B C | S8 title`, then sheet records `[0a*k 0][id][prev][flag] S8 title` and a trailer record `(id, prev, 0, "")`. Y = number of sheets + 1.
  A/B/C and the trailer ids vary (Random Voltage: 2,1,1 / (3,2,0); Bloom: 1,1,1 / (2,2,0); gsm: 2,2,1 / (4,3,0)). Not understood.
- Hierarchical designs (nmodules > 1): after "ROOT" comes one record per module `u32 id/def pair, S8 name, u8, u32 N, u32 0` (Shiftpcb: SR1, FF0, MASTER, SLAVE...), then the sheet records. N looks like the element serial of the module instance in its parent. Parsed only as opaque bytes (round-trip is exact).

### ELEMENT fields
- `serial`: 1..n in file order, normally == `key`. Differs in multi-sheet IOT samples (key = object id; serial = enumeration) and in g5 (serial 1/key 7, serial 2/key 1). Generator: key defaults to serial.
- `sheet_id` (2nd word): id of the SHEET the element is on. 2 for every "Master Sheet" file; 1..54 in hierarchical ones.
- `group` (3rd word): 0 normally. 1..5 in IOT/Raspberry Pi samples, one value per pasted block of LED+R+GPIO ("snippet", CDBCORE::getsnippetelements). Guess.
- `ref`: part reference. `''` for devices that have no reference/PART: LOGICPROBE (pin D0), 7SEG-BCD (D3..D0), text/graphics (no pins). Gate sections are `U7:A` etc. with a shared PART `U7` (several elements point to one `part_idx`).
- `pins`: (name, number). Number is `''` for primitives without pin numbers (LOGICSTATE `Q0`, gates `D0 D1 Q`). Multi-number pins use `6/9/14`. Order = device pin order as in the library (DIPSW_8: COM1..8 then NO1..8 with numbers 16..9).
- `blob`: normally empty. Non-empty = part memory image: 1024 (or 4096/512/9) bytes of `ff` = erased EEPROM of ATmega etc., 64 B for RTC/EEPROM contents, 2 MiB for flash. Seen only on microcontroller/memory parts.
- `part_idx`: index into PART.idx (sparse; gaps survive deletions). PART order in the file = ascending idx; in the seed it is `U1,U2,A,B,C,DSW1,...` while ELEMENTs are in serial order `A,B,C,U1,U2,...` -> idx is the order in which parts got their reference, not serial order. Not derivable; generator takes `idx` from the caller (default: running count in element order).
- `unit`: i32, 0 or -1 for ordinary parts, 0..n-1 = gate/section number of multi-gate packages (74LS00: 0,1,2,3, 74LS04: 0..5). The 0 vs -1 choice on single parts is NOT explained: seed has A,B,C=0; U1,U2=-1; DSW1=0; U3=-1; U4=0; DSW2=0; DSW3=-1; U5=-1; U6=0, identical devices differ (6116 U3=-1, U6=0). Both values load fine in generated files as far as known; use 0 for parts and -1 for ref-less elements (always -1 there, 981/1045).

### PART fields
- words = `[1, sheetno, 0, interactive]`. `sheetno` = 0 on single sheet; = sheet number (1,2,..) in multi-sheet designs. `interactive` = 1 for parts with runtime state (LOGICSTATE `{PRIMITIVE=DIGITAL,RTDSTATE}`, buttons, LEDs RGB, motors, virtual terminals), else 0. Not purely device-determined (RES has 4 cases of 1) -> default rule in generator: 1 iff props contain RTDSTATE.
- `value`: the component value text; equals device name for most primitives (`AND`, `DIPSW_8`, `6116`), `10k` for resistors, `''` for LOGICSTATE.
- `package`: equals `{PACKAGE=..}` in props, else `''`.
- `props`: lines `{KEY=VALUE}\n` = device default properties merged with the instance's overrides (`{STATE=1}` for state C). Trailing extra `\n` blank lines occur (TC74: many); preserved.

### SHEETITEM (84-byte header, 21 x i32) - present only for multi-sheet designs (one per sheet)
`[sheetno, 2, 1, x1, y1, x2, y2 (nm, 1 mil = 25400), -1/1 flags x4, n_elements_on_sheet, n_parts_on_sheet, ?, ?, payload_len, FILEVER_at_save (805..828, or -1), 16, 0, -1, 0]`.
Verified: words 11/12 equal the number of ELEMENTs/PARTs of that sheet (Alarm Clock: 5/4 and 4/3). `payload_len` is non-zero for an embedded sub-circuit DSN snippet (Grove 4-digit display clock, 4087 bytes). Single-sheet files have 0 items.

## Meaning (from ISIS.DLL exported names, CDBCORE class)
`CDBCORE::{load,save,addrootsheet,addsheet,addmodule,bindelement,bindmodule,getsnippetelements,renamepart,findpart,getboard...}`
with types MODULE, SHEET, ELEMENT, PART, BOARD. Mapping: preamble = MODULE/SHEET tree, ELEMENT list = schematic instances (bound to the DSN objects by `bindelement(sheet, key)`), PART list = physical parts (ref/value/device/package, shared by gate sections, used by ARES/BOM), SHEETITEM = per-sheet extents. So the CDB is a cache/mirror of the design for netlisting, BOM, ARES; ROOT.DSN stays the source of truth (it carries refs and properties itself).

## Is the CDB needed?
- Empty 116-byte CDB (`build_cdb([])`, identical to lab4's) loads fine with generated DSNs (user-confirmed; 20+ generated files in sandbox/exchange use it: b0..d3,f1,p1..p3,r1..r4,v1..w4,k2).
- What Proteus does with it at load is NOT verified. Likely (guess from CDBCORE::killorphans/bindelement/beginsyncsheet) it re-syncs the CDB from the DSN, so a missing element/part is created on load. Not tested: ARES netlist transfer, BOM and PCB packaging with an empty CDB (parts may then have no PART record until the design is saved once in the GUI).
- Safe recommendation: generate the full CDB with `build_cdb` anyway (cheap, byte-exact for the seeds), fall back to the empty one if a part is not understood.

## PROJECT.XML
Zip member `PROJECT.XML`, UTF-8, `<?xml version='1.0' encoding='UTF-8' standalone='yes'?>`, LF, 1-space indent, final LF, no BOM.
```
<PROJECT [TITLE=".." AUTHOR=".." DOCNO=".." REV=".."]>          attribute order varies per file (kept by parse)
 <TIMESTAMP RELEASE="811" FILEVER="828" MODIFIED="<epoch>" CUSTID="175675344" CREATED="<epoch>"/>
 <VARIANTS/>                      or <VARIANTS><NAMES><STRING>..</STRING></NAMES><ACTIVE>A</ACTIVE></VARIANTS>
 [<DESCRIPTION><TEXT>..</TEXT>|<TEXT/>|<TEXT></TEXT> <KEYWORDS><STRING>..</STRING></KEYWORDS>|<KEYWORDS/></DESCRIPTION>]
 <SCRIPTS><STRING>PWRRAILS.DAT</STRING>[<STRING>REQUIRES.DAT</STRING>]</SCRIPTS>      or <SCRIPTS/>
</PROJECT>
```
- RELEASE = Proteus release (811 = 8.11); FILEVER = file format revision (828 for 8.11 SP, saw 800..828; 827 and 828 both appear under release 811). MODIFIED/CREATED = unix seconds; CREATED stays when re-saving, MODIFIED = save time. CUSTID = licence/customer id of the machine that saved (175675344 here; older samples 1213xxxxxx; absent in 292/666 files). Attribute order: FILEVER 828 -> RELEASE FILEVER MODIFIED CUSTID CREATED (42/43 files); older: RELEASE MODIFIED FILEVER CREATED or RELEASE FILEVER MODIFIED CREATED.
- Child order: FILEVER 828 -> TIMESTAMP, VARIANTS, SCRIPTS (DESCRIPTION, when present, sits between VARIANTS and SCRIPTS); older files vary (DESCRIPTION before VARIANTS, SCRIPTS first, VARIANTS absent). `build_project_xml(child_order=...)` reproduces any.
- SCRIPTS lists files under `SCRIPTS/` in the zip: PWRRAILS.DAT (17 bytes `*RAILS\n*BINDINGS\n` in 362/450 files, else a `{*RAILS}{GND=0,POWER}...` table) and optionally REQUIRES.DAT (`SAMPLE=xxxxxxxx\nPRODUCT=80808080\nPRODUCT=C9378434\n`, written for projects with VSM/firmware). Other optional members: FIRMWARE.XML + FIRMWARE/..., ROOT.LYT (PCB layout, 14 files).
- Zip members in GUI saves: PROJECT.XML, ROOT.CDB, ROOT.DSN, SCRIPTS/..., deflate (flag bits 2), date_time = save time.
- 666/666 samples round-trip byte-exact through `parse_project_xml` + `build_project_xml`.

## Established vs guessed
Established (byte-exact tests): CDB v3-5 grammar above; part/element/sheet-item framing; Master-Sheet preamble constant; PROJECT.XML grammar and generator; seeds reproducible from a part list.
Guessed / open: exact meaning of `unit` 0 vs -1 on single parts, `group`, `interactive` rule, PART `idx` ordering rule, A/B/C and ids in non-Master preambles, module record fields in hierarchical designs, sheet-item words 13/14/17+, what Proteus does when the CDB is empty or inconsistent with the DSN (only "loads" is confirmed), v2 layout.
