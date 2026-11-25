#!/usr/bin/env python3
"""pdsfmt - parser/validator for the ISIS object stream of ROOT.DSN (Proteus 8.11, file version 0x32b).

Grammar derived from ISIS.DLL FUN_1002bc50 (object loop), FUN_1002b490 (COMPONENT read),
FUN_1005e330 (link slot read / pairing), FUN_1008e640 (wire body read).  See NOTES.md.
"""
import struct, sys, zipfile
T = 2540

class Err(Exception): pass

class R:
    def __init__(s, b, p=0): s.b = b; s.p = p
    def u8(s):  v = s.b[s.p]; s.p += 1; return v
    def u16(s): v = struct.unpack_from('<H', s.b, s.p)[0]; s.p += 2; return v
    def u32(s): v = struct.unpack_from('<I', s.b, s.p)[0]; s.p += 4; return v
    def i32(s): v = struct.unpack_from('<i', s.b, s.p)[0]; s.p += 4; return v
    def raw(s, n):
        if s.p + n > len(s.b): raise Err('EOF at %#x' % s.p)
        v = s.b[s.p:s.p+n]; s.p += n; return v
    def pstr(s): n = s.u8(); return s.raw(n).decode('latin1')

def dpkb(r):
    """DPKB(0x1000,1) packet: u32 total size (incl. these 4 bytes) + payload."""
    n = r.u32()
    if n < 4 or n > 0x10000: raise Err('bad DPKB size %d at %#x' % (n, r.p-4))
    return r.raw(n-4)

def cts(r):
    return dict(x=r.i32()//T, y=r.i32()//T, rot=r.u16(), flags=r.u16())

def label(r):
    p0 = r.p
    b = r.u8()
    if b != 0xff: raise Err('LABEL: expected 0xff at %#x got %#x (legacy label format unsupported)' % (p0, b))
    d = dict(text=r.pstr(), cts=cts(r), b44=r.u8(), b45=r.u8())
    st = dpkb(r)               # TSTYLE: [h u32][w u32][?u32][u16 flags?] font\0 stylename\0 ...
    d['tstyle'] = st
    d['_off'] = p0
    return d

def textblock(r, lim=0x32):
    d = label(r)
    n = r.u32()
    d['len'] = n
    if n > lim: d['text'] = r.raw(n).decode('latin1')
    return d

class Obj(dict): pass

def read_component(r, t):
    o = Obj(type=t, kind='COMPONENT', _off=r.p-1)
    o['id'] = label(r); o['value'] = label(r); o['subckt'] = label(r)
    o['props'] = textblock(r)
    o['npins'] = r.u16()
    o['device'] = r.pstr()
    o['cts'] = cts(r)
    o['_ser_off'] = r.p
    o['serial'] = r.u32(); o['aux'] = r.u32()
    o['flags'] = r.u8()
    o['extra'] = r.u32()            # [0xa6]; present because file version 0x33c > 0x32c
    o['_slots_off'] = r.p
    o['slots'] = [r.u32() for _ in range(o['npins'])]
    return o

def read_junction(r, t):
    o = Obj(type=t, kind='JUNCTION', _off=r.p-1)
    o['x'] = r.i32()//T; o['y'] = r.i32()//T
    o['_slots_off'] = r.p
    o['slots'] = [r.u32() for _ in range(4)]
    return o

def read_terminal(r, t):          # type 0x10  (FUN_1002ca30)
    o = Obj(type=t, kind='TERMINAL', _off=r.p-1)
    o['cts'] = cts(r); o['name'] = r.pstr(); o['ttype'] = r.u16(); o['aux'] = r.u32()
    o['label'] = label(r); o['_slots_off'] = r.p; o['slots'] = [r.u32()]
    return o

def read_wire_body(r):
    o = Obj(kind='WIRE', _off=r.p)
    o['bus'] = r.u8()
    o['gstyle'] = dpkb(r)
    nl = r.u16()
    o['labels'] = [label(r) for _ in range(nl)]
    n = r.u16()
    o['pts'] = [(r.i32()//T, r.i32()//T) for _ in range(n)]
    return o

READERS = {0: read_component, 8: read_component, 1: read_junction, 0x10: read_terminal}

def find_stream(b):
    i = b.find(b'OBJECT DATA\x00')
    if i < 0: raise Err('no OBJECT DATA')
    return i + 12

def parse_objects(b, start=None, strict=True):
    """Returns (objects, end_pos, problems).  Wires appear in the list as kind=WIRE in file order,
    with ends: wire['a'], wire['b'] = (owner_index, slot_index)."""
    r = R(b, find_stream(b) if start is None else start)
    objs = []; probs = []
    pending = {}            # key -> (obj_idx, slot_idx)
    queue = []              # completed pairs waiting for a wire body
    def reg(oi, si, key):
        if key == 0: return
        if key in pending:
            queue.append((pending.pop(key), (oi, si), key))
        else:
            pending[key] = (oi, si)
    while True:
        t = r.u8()
        if t == 0xff: break
        if t not in READERS:
            probs.append('bad object type %#x at %#x (=> "Bad object record - circuit data lost")' % (t, r.p-1))
            return objs, r.p-1, probs
        try:
            o = READERS[t](r, t)
        except (Err, struct.error, IndexError) as e:
            probs.append('desync while reading type %#x object at %#x: %s' % (t, r.p, e))
            return objs, r.p, probs
        o['_end'] = r.p
        objs.append(o); oi = len(objs)-1
        for si, k in enumerate(o['slots']): reg(oi, si, k)
        while queue:
            a, bb, key = queue.pop(0)
            try:
                w = read_wire_body(r)
            except (Err, struct.error, IndexError) as e:
                probs.append('desync reading wire body for key %#x (pair %s-%s) at %#x: %s' % (key, a, bb, r.p, e))
                return objs, r.p, probs
            w['a'] = a; w['b'] = bb; w['key'] = key; w['_end'] = r.p
            if key != w['_off'] - 0 and key != w['_off']:
                pass
            objs.append(w)
    for k, v in pending.items():
        probs.append('unpaired link key %#x on object %d slot %d (Proteus keeps it dangling / pairs later)' % (k, v[0], v[1]))
    return objs, r.p, probs

def dump(path, member='ROOT.DSN'):
    b = zipfile.ZipFile(path).read(member) if path.endswith(('.pdsprj', '.zip')) else open(path, 'rb').read()
    objs, end, probs = parse_objects(b)
    for i, o in enumerate(objs):
        if o['kind'] == 'WIRE':
            print('%3d WIRE @%#x key=%#x a=%s b=%s pts=%s' % (i, o['_off'], o['key'], o['a'], o['b'], o['pts']))
        elif o['kind'] == 'COMPONENT':
            print('%3d COMP t=%d @%#x %-14s id=%r at(%d,%d) serial=%d npins=%d slots=%s' % (i, o['type'], o['_off'], o['device'], o['id']['text'], o['cts']['x'], o['cts']['y'], o['serial'], o['npins'], ['%x' % s if s else 0 for s in o['slots']]))
        else:
            print('%3d %s %s' % (i, o['kind'], {k: v for k, v in o.items() if k in ('x', 'y', 'slots')}))
    print('end of object stream at %#x; problems: %s' % (end, probs))
    return objs, probs

if __name__ == '__main__':
    for p in sys.argv[1:]:
        print('=====', p); dump(p)


# ---------------------------------------------------------------- writer side
WIRE_GSTYLE = bytes.fromhex('1d000000' '00c09e000000400000' '01ffffff00ffffff00' '027f5749524500')
# NB: payload after the u32 size = 00 c0 9e 00 00 00 40 00 00 01 ff ff ff 00 ff ff ff 00 02 7f 'WIRE' 00 (25 bytes)

def make_junction(x, y):
    """JUNCTION object bytes (type 1) and offset of its 4 slots. x,y in thousandth-inch."""
    b = bytes([1]) + struct.pack('<ii', x*T, y*T) + b'\0'*16
    return dict(blob=bytearray(b), slots_off=9, nslots=4, kind='JUNCTION')

def make_wire_body(pts, bus=0, labels=b'', nlabels=0):
    assert len(pts) >= 2
    return (bytes([bus]) + WIRE_GSTYLE + struct.pack('<H', nlabels) + labels +
            struct.pack('<H', len(pts)) + b''.join(struct.pack('<ii', x*T, y*T) for x, y in pts))

def component_from_parsed(blob, o):
    """Make an item from raw bytes of a parsed COMPONENT (slots cleared)."""
    b = bytearray(blob)
    so = o['_slots_off'] - o['_off']
    for i in range(o['npins']): b[so+4*i:so+4*i+4] = b'\0\0\0\0'
    return dict(blob=b, slots_off=so, nslots=o['npins'], kind='COMPONENT')

def assemble(items, wires, base_offset):
    """items: ordered list of dict(blob=bytearray, slots_off, nslots) (slots must be zero on input).
    wires: list of dict(a=(item,slot), b=(item,slot), pts=[(x,y),...], bus=0)
           pts[0] must lie at the tip of whichever endpoint comes first in the stream (a or b, we sort it for you).
    base_offset: absolute file offset where the first object's type byte will be written.
    Returns (bytes, keys) where every wire's pairing key = absolute offset of its body (as Proteus writes).
    Wire bodies are placed right after the object that completes the pair (see NOTES.md)."""
    used = set()
    for w in wires:
        for e in (w['a'], w['b']):
            if e in used: raise Err('slot %r used twice (use a junction)' % (e,))
            used.add(e)
            if not 0 <= e[0] < len(items) or not 0 <= e[1] < items[e[0]]['nslots']: raise Err('bad endpoint %r' % (e,))
    # completion point of each wire: later endpoint in (item, slot) order
    after = {}   # item index -> list of (slot, wire)
    for w in wires:
        first, second = sorted([w['a'], w['b']])
        w['_first'], w['_second'] = first, second
        after.setdefault(second[0], []).append((second[1], w))
    pos = base_offset
    out = bytearray(); keys = {}
    # pass 1: layout
    layout = []
    for i, it in enumerate(items):
        layout.append(('obj', i, pos)); pos += len(it['blob'])
        for _, w in sorted(after.get(i, []), key=lambda t: t[0]):
            w['_body'] = None
            layout.append(('wire', w, pos)); w['_key'] = pos
            ptsl = w['pts']
            # orientation: pts[0] belongs to w['_first']; if caller gave pts for (a..b) and a is not first -> reverse
            if w['a'] != w['_first']: ptsl = list(reversed(ptsl))
            body = make_wire_body(ptsl, w.get('bus', 0)); w['_body'] = body; pos += len(body)
    # patch slots
    for w in wires:
        for e in (w['_first'], w['_second']):
            it = items[e[0]]; o = it['slots_off'] + 4*e[1]
            it['blob'][o:o+4] = struct.pack('<I', w['_key'])
    for ent in layout:
        if ent[0] == 'obj': out += items[ent[1]]['blob']
        else: out += ent[1]['_body']
    return bytes(out), {id(w): w['_key'] for w in wires}

def splice_stream(dsn, new_stream):
    """Replace the object stream of sheet 1 (type bytes, WITHOUT the 0xff terminator) of a ROOT.DSN.
    The file is a sequence of sections 'ISIS CIRCUIT FILE ... OBJECT DATA\\0 <objects> 0xff' (one per sheet) followed
    by a sheet table.  Absolute offsets that must be shifted by the size delta: the u32 right before the first marker
    (-> sheet table) and the u32 entries of the sheet table that point at later section markers.  Returns new bytes."""
    start = find_stream(dsn)
    objs, end, probs = parse_objects(dsn)
    if probs and not all(p.startswith('unpaired') for p in probs): raise Err('old stream does not parse: %s' % probs)
    old_end = end - 1                                  # position of the 0xff terminator
    delta = len(new_stream) - (old_end - start)
    marker = b'ISIS CIRCUIT FILE'
    first = dsn.find(marker)
    later = {m for m in (i for i in range(len(dsn)) if dsn.startswith(marker, i)) if m > old_end}
    out = bytearray(dsn[:start] + new_stream + dsn[old_end:])
    v = struct.unpack_from('<I', out, first-4)[0]; struct.pack_into('<I', out, first-4, v+delta)
    tail = old_end + delta
    # sheet table lives after the last section; patch u32 equal to a later marker offset
    for m in later:
        pat = struct.pack('<I', m); j = tail
        while True:
            j = bytes(out).find(pat, j)
            if j < 0: break
            # only inside the table (after the last section); heuristic: after last marker + 30 bytes
            if j > max(later)+delta+30: struct.pack_into('<I', out, j, m+delta)
            j += 4
    return bytes(out)
