#!/usr/bin/env python3
"""cdbfmt.py - parser / writer for Proteus 8.x ROOT.CDB (CDBCORE database dump).

stdlib only.  See NOTES.md for the grammar and the evidence behind it.

Layout (little endian; S8 = u8 length + bytes, no terminator):

  u32 version (2..5; 5 = Proteus 8.0x+, FILEVER>=813)   [v2 = legacy, not parsed]
  u32 nmodules
  ...preamble (module tree + sheet records, kept as opaque bytes)...
  u32 0x0a, u32 0                                         <- "marker"
  u32 n_elements
  n_elements x ELEMENT:
      u32 serial, u32 sheet_id, u32 group, u32 key,
      S8 ref, u32 npins, npins x (S8 name, S8 number),
      u32 bloblen, blob[bloblen],
      u32 part_idx (0 = no part record), i32 unit
  u32 1, u32 1, u32 1, u32 n_parts
  n_parts x PART:
      u32 idx, (v5: 4 | v3,v4: 3) x u32 words  [1, sheetno, 0, (interactive)],
      S8 ref, S8 value, S8 device, S8 package,
      u32 L, text[L-5] ("{K=V}\\n" lines), u8 0          <- L = len(text)+5
  u32 n_sheetitems
  n_sheetitems x (84-byte header = 21 x i32, then payload[hdr[15]])

The last 4 bytes of the last PART record's "L" (+5) are in fact the next record's
idx (or the u32 n_sheetitems after the last one); we model this as separate
fields, which gives identical bytes.

Public API
  parse_cdb(bytes)  -> dict      (structure; see parse_cdb docstring)
  write_cdb(dict)   -> bytes     (exact inverse of parse_cdb for v3..v5)
  build_cdb(parts)  -> bytes     (generator for a v5 'Master Sheet' design)
  dump(bytes)       -> str       (human readable)
"""
import re
import struct
import sys
import zipfile

MARKER = struct.pack('<II', 0x0A, 0)
_MARK_RE = re.compile(b'(?=' + re.escape(MARKER) + b')')

# preamble of every single-sheet design saved by Proteus 8.x with the default
# sheet name (identical in 258/258 samples):  bytes [8 .. marker end) of the file
_PRE_HEAD = bytes.fromhex(
    '01000000 00000000 04 524f4f54 00 00000000 01000000 01000000 02000000'
    '01000000 02000000 00000000'.replace(' ', ''))
_PRE_TAIL = struct.pack('<II', 0x0A, 0) + b'' \
    + struct.pack('<III', 2, 3, 1) + b'\x00'          # sheet record (2,3,1,"")
_PRE_END = MARKER                                      # marker before n_elements


def _preamble(title='Master Sheet'):
    t = title.encode('cp1252')
    return (_PRE_HEAD + bytes([len(t)]) + t + _PRE_TAIL + _PRE_END)


class _R:
    def __init__(s, d, p=0):
        s.d, s.p = d, p

    def u32(s):
        v = struct.unpack_from('<I', s.d, s.p)[0]
        s.p += 4
        return v

    def i32(s):
        v = struct.unpack_from('<i', s.d, s.p)[0]
        s.p += 4
        return v

    def u8(s):
        v = s.d[s.p]
        s.p += 1
        return v

    def s8(s):
        n = s.u8()
        if s.p + n > len(s.d):
            raise ValueError('string overrun')
        v = s.d[s.p:s.p + n]
        s.p += n
        return v.decode('latin1')

    def raw(s, n):
        if n < 0 or s.p + n > len(s.d):
            raise ValueError('overrun')
        v = s.d[s.p:s.p + n]
        s.p += n
        return v


def _s8(x):
    b = x.encode('latin1') if isinstance(x, str) else bytes(x)
    if len(b) > 255:
        raise ValueError('S8 string too long: %r' % x[:20])
    return bytes([len(b)]) + b


def _parse_from(d, mpos, ver):
    nw = 4 if ver >= 5 else 3
    r = _R(d, mpos + 8)
    n1 = r.u32()
    if n1 > len(d):
        raise ValueError('bad count')
    els = []
    for _ in range(n1):
        e = {'serial': r.u32(), 'sheet': r.u32(), 'group': r.u32(), 'key': r.u32(),
             'ref': r.s8()}
        npin = r.u32()
        if npin > len(d):
            raise ValueError('bad npins')
        e['pins'] = [(r.s8(), r.s8()) for _ in range(npin)]
        e['blob'] = r.raw(r.u32())
        e['part'] = r.u32()
        e['unit'] = r.i32()
        els.append(e)
    head = [r.u32() for _ in range(3)]
    if head != [1, 1, 1]:
        raise ValueError('bad sec2 head')
    n2 = r.u32()
    if n2 > len(d):
        raise ValueError('bad n2')
    parts = []
    for _ in range(n2):
        p = {'idx': r.u32(), 'words': [r.u32() for _ in range(nw)]}
        p['ref'], p['value'], p['device'], p['package'] = r.s8(), r.s8(), r.s8(), r.s8()
        L = r.u32()
        if L < 5:
            raise ValueError('bad L')
        p['props'] = r.raw(L - 5).decode('latin1')
        if r.u8() != 0:
            raise ValueError('bad part terminator')
        parts.append(p)
    n3 = r.u32()
    sheets = []
    for _ in range(n3):
        hdr = struct.unpack('<21i', r.raw(84))
        sheets.append({'hdr': list(hdr), 'payload': r.raw(hdr[15])})
    if r.p != len(d):
        raise ValueError('trailing bytes')
    return els, parts, sheets


def parse_cdb(d):
    """Return dict(version, nmodules, preamble, elements, parts, sheets).

    elements: dict(serial, sheet, group, key, ref, pins[(name,number)], blob, part, unit)
    parts:    dict(idx, words, ref, value, device, package, props)
    sheets:   dict(hdr[21 ints], payload)
    preamble: opaque bytes between the 8-byte file head and n_elements (incl. marker)
    Raises ValueError if no consistent parse exists (version 2 files).
    """
    ver, nmod = struct.unpack_from('<II', d)
    if ver < 3 or ver > 5:
        raise ValueError('unsupported CDB version %d' % ver)
    last = None
    for m in _MARK_RE.finditer(d, 8):
        try:
            els, parts, sheets = _parse_from(d, m.start(), ver)
        except (ValueError, IndexError, struct.error) as ex:
            last = ex
            continue
        return {'version': ver, 'nmodules': nmod, 'preamble': d[8:m.start() + 8],
                'elements': els, 'parts': parts, 'sheets': sheets}
    raise ValueError('no consistent parse (%s)' % last)


def write_cdb(c):
    out = [struct.pack('<II', c['version'], c['nmodules']), c['preamble']]
    out.append(struct.pack('<I', len(c['elements'])))
    for e in c['elements']:
        out.append(struct.pack('<IIII', e['serial'], e['sheet'], e['group'], e['key']))
        out.append(_s8(e['ref']))
        out.append(struct.pack('<I', len(e['pins'])))
        for n, num in e['pins']:
            out.append(_s8(n) + _s8(num))
        out.append(struct.pack('<I', len(e['blob'])) + e['blob'])
        out.append(struct.pack('<Ii', e['part'], e['unit']))
    out.append(struct.pack('<IIII', 1, 1, 1, len(c['parts'])))
    for p in c['parts']:
        out.append(struct.pack('<I', p['idx']))
        out.append(struct.pack('<%dI' % len(p['words']), *p['words']))
        out.append(_s8(p['ref']) + _s8(p['value']) + _s8(p['device']) + _s8(p['package']))
        t = p['props'].encode('latin1')
        out.append(struct.pack('<I', len(t) + 5) + t + b'\x00')
    out.append(struct.pack('<I', len(c['sheets'])))
    for s in c['sheets']:
        out.append(struct.pack('<21i', *s['hdr']) + s['payload'])
    return b''.join(out)


# --------------------------------------------------------------------------
# generator
# --------------------------------------------------------------------------
def _norm(p):
    """tuple/dict -> dict with defaults.  Tuple = (serial, ref, device, pins, props[, extras])."""
    if isinstance(p, dict):
        x = dict(p)
    else:
        x = dict(zip(('serial', 'ref', 'device', 'pins', 'props', 'extra'), p))
        x.update(x.pop('extra', None) or {})
    x.setdefault('pins', [])
    x.setdefault('props', [])
    if isinstance(x['props'], str):
        x['props'] = [x['props']]
    x['pins'] = [(q, '') if isinstance(q, str) else tuple(q) for q in x['pins']]
    return x


def _prop(props, name):
    for s in props:
        m = re.match(r'\{%s=(.*)\}$' % re.escape(name), s.strip())
        if m:
            return m.group(1)
    return None


def build_cdb(parts, *, sheet_title='Master Sheet', sheet_id=2, version=5):
    """Generate a v5 ROOT.CDB for ONE sheet ("Master Sheet" family preamble).

    parts: iterable, each either a dict or a tuple
        (serial, ref, device, pins, props[, extras_dict])
      serial  u32 element id (also used as `key` unless extras['key'])
      ref     reference ("U1"); '' -> element only, NO part record (probes,
              7-seg displays without PREFIX, graphics ...)
      device  device name, e.g. "AND", "LOGICSTATE"
      pins    [(name, number)]  (number '' for primitives without numbers)
      props   ["{PRIMITIVE=DIGITAL,AND_2}", "{STATE=0}", ...]  (instance props as stored)
    extras (all optional): value, package, interactive(0/1), unit(-1/0/n),
      idx (explicit sec-2 index), key, group, sheet, blob(bytes), part(bool)
    Order: ELEMENTs are written in the order given; PART records are sorted by
    idx (default idx = running 1..n over parts in the given order).
    Defaults reproducing the Proteus GUI output:
      value   = device, but '' when props contain RTDSTATE (LOGICSTATE)
      package = value of {PACKAGE=..} in props else ''
      interactive = 1 iff props contain RTDSTATE
      unit    = 0 for parts, -1 for element-only entries
    """
    if version != 5:
        raise NotImplementedError('only v5 is generated')
    els, prts = [], []
    nxt = 1
    seen = set()
    for raw in parts:
        x = _norm(raw)
        props = x['props']
        has_part = x.get('part', x['ref'] != '')
        rtd = any('RTDSTATE' in s for s in props)
        idx = 0
        if has_part:
            idx = x.get('idx', nxt)
            shared = idx in seen
            seen.add(idx)
            nxt = max(nxt, idx) + 1
            if not shared:
              prts.append({
                'idx': idx,
                'words': [1, x.get('words_sheet', 0), 0, x.get('interactive', 1 if rtd else 0)],
                'ref': x.get('part_ref', x['ref']),
                'value': x.get('value', '' if rtd else x['device']),
                'device': x['device'],
                'package': x.get('package', _prop(props, 'PACKAGE') or ''),
                'props': ''.join(s + '\n' for s in props),
              })
        els.append({'serial': x['serial'], 'sheet': x.get('sheet', sheet_id),
                    'group': x.get('group', 0), 'key': x.get('key', x['serial']),
                    'ref': x['ref'], 'pins': x['pins'], 'blob': x.get('blob', b''),
                    'part': idx, 'unit': x.get('unit', 0 if has_part else -1)})
    prts.sort(key=lambda p: p['idx'])
    return write_cdb({'version': 5, 'nmodules': 1, 'preamble': _preamble(sheet_title),
                      'elements': els, 'parts': prts, 'sheets': []})


def parts_from_cdb(c):
    """Inverse of build_cdb for a parsed CDB: list of (serial, ref, device, pins, props, extras)
    such that build_cdb(parts_from_cdb(c)) == original bytes (single-sheet Master family)."""
    byidx = {p['idx']: p for p in c['parts']}
    out = []
    seen = set()
    for e in c['elements']:
        p = byidx.get(e['part'])
        extra = {'key': e['key'], 'group': e['group'], 'sheet': e['sheet'],
                 'unit': e['unit'], 'blob': e['blob']}
        if p:
            props = p['props'].split('\n')[:-1] if p['props'] else []
            extra.update(idx=p['idx'], part_ref=p['ref'], value=p['value'], package=p['package'],
                         interactive=p['words'][3] if len(p['words']) > 3 else 0,
                         words_sheet=p['words'][1])
            out.append((e['serial'], e['ref'], p['device'], e['pins'], props, extra))
        else:
            extra['part'] = False
            out.append((e['serial'], e['ref'], '', e['pins'], [], extra))
    return out


# --------------------------------------------------------------------------
# PROJECT.XML
# --------------------------------------------------------------------------
def _esc(s, attr=False):
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return s.replace('"', '&quot;') if attr else s


TS_ORDER_NEW = ('RELEASE', 'FILEVER', 'MODIFIED', 'CUSTID', 'CREATED')   # FILEVER 828 (8.11 GUI)
TS_ORDER_OLD = ('RELEASE', 'MODIFIED', 'FILEVER', 'CREATED')             # FILEVER <= 827 samples


def build_project_xml(release=811, filever=828, modified=None, created=None, custid=175675344,
                      title=None, author=None, docno=None, rev=None,
                      description=None, keywords=None, variants=None, active=None,
                      scripts=('PWRRAILS.DAT',), ts_order=None, desc_first=False, emit_variants=True, child_order=None, text_pair=False,
                      root_order=('TITLE', 'AUTHOR', 'DOCNO', 'REV')):
    """PROJECT.XML exactly as Proteus 8.11 writes it (UTF-8, ' ' indent, LF, final LF).

    modified/created: unix epoch seconds (default: now).  custid=None omits CUSTID.
    description: text (None omits the whole DESCRIPTION element; '' -> <TEXT/>)
    keywords: list of str; variants: list of variant names (None -> <VARIANTS/>);
    active: active variant letter ("A"), only written when variants given.
    """
    import time
    now = int(time.time())
    modified = now if modified is None else modified
    created = modified if created is None else created
    ts = {'RELEASE': release, 'FILEVER': filever, 'MODIFIED': modified, 'CREATED': created}
    if custid is not None:
        ts['CUSTID'] = custid
    order = ts_order or (TS_ORDER_NEW if filever >= 828 else TS_ORDER_OLD)
    rv = {'TITLE': title, 'AUTHOR': author, 'DOCNO': docno, 'REV': rev}
    root = ''.join(' %s="%s"' % (k, _esc(str(rv[k]), True)) for k in root_order
                   if rv.get(k) is not None)
    L = ["<?xml version='1.0' encoding='UTF-8' standalone='yes'?>", '<PROJECT%s>' % root,
         ' <TIMESTAMP %s/>' % ' '.join('%s="%s"' % (k, ts[k]) for k in order if k in ts)]
    desc = []
    if description is not None or keywords is not None:
        desc.append(' <DESCRIPTION>')
        desc.append('  <TEXT>%s</TEXT>' % _esc(description) if (description or text_pair) else '  <TEXT/>')
        if keywords is not None and not keywords:
            desc.append('  <KEYWORDS/>')
        elif keywords:
            desc.append('  <KEYWORDS>')
            desc += ['   <STRING>%s</STRING>' % _esc(k) for k in keywords]
            desc.append('  </KEYWORDS>')
        desc.append(' </DESCRIPTION>')
    var = [' <VARIANTS/>'] if emit_variants else []
    if variants:
        var = [' <VARIANTS>', '  <NAMES>'] + ['   <STRING>%s</STRING>' % _esc(v) for v in variants] \
              + ['  </NAMES>', '  <ACTIVE>%s</ACTIVE>' % _esc(active or 'A'), ' </VARIANTS>']
    if scripts:
        scr = [' <SCRIPTS>'] + ['  <STRING>%s</STRING>' % _esc(s) for s in scripts] + [' </SCRIPTS>']
    else:
        scr = [' <SCRIPTS/>']
    sect = {'DESCRIPTION': desc, 'VARIANTS': var, 'SCRIPTS': scr}
    co = child_order or (('DESCRIPTION', 'VARIANTS', 'SCRIPTS') if desc_first
                         else ('VARIANTS', 'DESCRIPTION', 'SCRIPTS'))
    for k in co:
        L += sect[k]
    L += ['</PROJECT>', '']
    return '\n'.join(L).encode('utf-8')


def parse_project_xml(b):
    """-> kwargs for build_project_xml (round-trip helper)."""
    import xml.etree.ElementTree as ET
    t = ET.fromstring(b)
    ts = t.find('TIMESTAMP')
    kw = dict(release=int(ts.get('RELEASE')), filever=int(ts.get('FILEVER')),
              modified=int(ts.get('MODIFIED')), created=int(ts.get('CREATED')),
              custid=int(ts.get('CUSTID')) if ts.get('CUSTID') else None,
              title=t.get('TITLE'), author=t.get('AUTHOR'), docno=t.get('DOCNO'), rev=t.get('REV'),
              ts_order=tuple(ts.attrib), root_order=tuple(t.attrib), scripts=[s.text for s in t.find('SCRIPTS')])
    kids = [c.tag for c in t]
    kw['emit_variants'] = 'VARIANTS' in kids
    kw['text_pair'] = b'<TEXT></TEXT>' in b
    kw['child_order'] = tuple(k for k in kids if k != 'TIMESTAMP')
    kw['desc_first'] = ('DESCRIPTION' in kids and 'VARIANTS' in kids
                        and kids.index('DESCRIPTION') < kids.index('VARIANTS'))
    d = t.find('DESCRIPTION')
    if d is not None:
        kw['description'] = d.find('TEXT').text or ''
        k = d.find('KEYWORDS')
        kw['keywords'] = [s.text for s in k] if k is not None else None
    v = t.find('VARIANTS')
    if v is not None and len(v):
        kw['variants'] = [s.text for s in v.find('NAMES')]
        kw['active'] = v.find('ACTIVE').text
    return kw


def selftest_xml(paths):
    ok = bad = 0
    for p in paths:
        b = zipfile.ZipFile(p).read('PROJECT.XML')
        try:
            r = build_project_xml(**parse_project_xml(b))
        except Exception as ex:
            r = None
        if r == b:
            ok += 1
        else:
            bad += 1
            if bad < 4:
                print('XML FAIL', p)
    print('PROJECT.XML round-trip ok=%d fail=%d' % (ok, bad))
    return bad == 0


# --------------------------------------------------------------------------
def dump(d):
    c = parse_cdb(d)
    L = ['version=%d nmodules=%d preamble=%d bytes' % (c['version'], c['nmodules'], len(c['preamble']))]
    pre = c['preamble']
    L.append('  preamble strings: %r' % re.findall(rb'[\x20-\x7e]{3,}', pre))
    L.append('elements (%d):' % len(c['elements']))
    for e in c['elements']:
        L.append('  ser=%d sheet=%d grp=%d key=%d ref=%r part=%d unit=%d blob=%d pins=%s' % (
            e['serial'], e['sheet'], e['group'], e['key'], e['ref'], e['part'], e['unit'],
            len(e['blob']), ','.join('%s/%s' % pn if pn[1] else pn[0] for pn in e['pins'])))
    L.append('parts (%d):' % len(c['parts']))
    for p in c['parts']:
        L.append('  idx=%d w=%s ref=%r value=%r device=%r pkg=%r props=%r' % (
            p['idx'], p['words'], p['ref'], p['value'], p['device'], p['package'],
            p['props'].replace('\n', ' ')[:100]))
    L.append('sheet items (%d):' % len(c['sheets']))
    for s in c['sheets']:
        L.append('  hdr=%s payload=%d' % (s['hdr'], len(s['payload'])))
    return '\n'.join(L)


def _read_cdb(path):
    if path.lower().endswith('.pdsprj'):
        return zipfile.ZipFile(path).read('ROOT.CDB')
    return open(path, 'rb').read()


def selftest(paths):
    ok = bad = skip = apiok = apitot = 0
    for p in paths:
        try:
            d = _read_cdb(p)
        except Exception:
            continue
        try:
            c = parse_cdb(d)
        except ValueError:
            skip += 1
            continue
        if write_cdb(c) == d:
            ok += 1
        else:
            bad += 1
            print('ROUNDTRIP FAIL', p)
        if (c['version'] == 5 and not c['sheets'] and c['nmodules'] == 1
                and c['preamble'] == _preamble('Master Sheet')):
            apitot += 1
            if build_cdb(parts_from_cdb(c)) == d:
                apiok += 1
            else:
                print('BUILD FAIL', p)
    print('structural round-trip ok=%d fail=%d unparsed(v2)=%d ; build_cdb round-trip %d/%d'
          % (ok, bad, skip, apiok, apitot))
    return bad == 0 and apiok == apitot


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'dump':
        print(dump(_read_cdb(sys.argv[2])))
    elif len(sys.argv) >= 3 and sys.argv[1] == 'selftest':
        ok = selftest(sys.argv[2:])
        ok = selftest_xml([p for p in sys.argv[2:] if p.endswith('.pdsprj')]) and ok
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
