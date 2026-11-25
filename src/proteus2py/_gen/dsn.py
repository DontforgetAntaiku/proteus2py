import os
import struct, zipfile, re
SEEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seeds')
from .objs import objects, T
SEED = open(SEEDS+'/seed4.DSN','rb').read()
ms = objects(SEED)
def pick(i, length):
    d = ms[i]; return SEED[d['start']:d['start']+length], d['x'], d['y'], d['off']-d['start']
# template defs: (index in seed, length, xoffs, yoffs, serial_off, ref_off, n_pins)
TPL = {
 '6116':   dict(i=18, L=0x201, xo=[5,75,148,213,392], yo=[9,79,152,217,396], ser=404, ref=(3,2), n=24),
 '74HC374':dict(i=14, L=462,   xo=[5,78,154,219,357], yo=[9,82,158,223,361], ser=369, ref=(3,2), n=20),
 'DIPSW_8':dict(i=12, L=460,   xo=[7,80,156,221,371], yo=[11,84,160,225,375], ser=383, ref=(3,4), n=16),
 'BCD':    dict(i=15, L=358,   xo=[3,69,146,240,317], yo=[7,73,150,244,321], ser=329, ref=(3,0), n=4),
 'BLUE':   dict(i=16, L=368,   xo=[3,69,151,245,327], yo=[7,73,155,249,331], ser=339, ref=(3,0), n=4),
 'GRN':    dict(i=17, L=366,   xo=[3,69,150,244,325], yo=[7,73,154,248,329], ser=337, ref=(3,0), n=4),
}
for k,t in TPL.items():
    t['blob'], t['x0'], t['y0'], t['main'] = pick(t['i'], t['L'])
# LOGICSTATE template (unnamed), original at (-1900,-1000)
LSB = open(SEEDS+'/inst_ls.bin','rb').read()
TPL['LS'] = dict(blob=LSB, L=len(LSB), x0=-1900, y0=-1000, xo=[3,69,148,252,331], yo=[7,73,152,256,335], ser=343, ref=(3,0), n=1)
WHDR = bytes.fromhex('001d00000000c09e0000004000000 1ffffff00ffffff00'.replace(' ',''))
def i32(v): return struct.pack('<i', v*T)
# pin tip geometry: name -> list of (dx,dy) relative to origin; slot index per pin
def pins_6116():
    P={}
    for k in range(11): P[f'A{k}']=(k,(-200,-100-100*k))
    for n,(idx,row) in {'CE':(11,12),'OE':(12,13),'WE':(13,14)}.items(): P[n]=(idx,(-200,-100-100*row))
    for k in range(8): P[f'D{k}']=(14+k,(1000,-100-100*k))
    return P
def pins_374():
    P={}
    for k in range(8):
        P[f'D{k}']=(2*k,(-200,-100-100*k)); P[f'Q{k}']=(2*k+1,(800,-100-100*k))
    P['OE']=(16,(-200,-100-100*9)); P['CLK']=(17,(-200,-100-100*10))
    return P
def pins_dsw():
    P={}
    for k in range(8):
        P[f'L{k}']=(8+k,(-100,-100-100*k))     # NO(16-k): left side, row k
        P[f'R{k}']=(k,(500,-100-100*k))        # COM(1+k): right side row k
    return P
def pins_bcd():
    return {f'P{k}':(k,(100+150*k,-1050)) for k in range(4)}
def pins_ls(): return {'Q':(0,(200,0))}
PINS={'6116':pins_6116(),'74HC374':pins_374(),'DIPSW_8':pins_dsw(),'BCD':pins_bcd(),'BLUE':pins_bcd(),'GRN':pins_bcd(),'LS':pins_ls()}
KEY={'6116':'6116','74HC374':'74HC374','DIPSW_8':'DIPSW_8','BCD':'BCD','BLUE':'BLUE','GRN':'GRN','LS':'LS'}
class Design:
    def __init__(s): s.parts=[]; s.wires=[]; s.serial=7
    def add(s, kind, x, y, ref=None, state=None, flip=0):
        p=dict(kind=kind,x=x,y=y,ref=ref,serial=s.serial,state=state,slots={},flip=flip); s.serial+=1; s.parts.append(p); return p
    def tip(s,p,pin):
        dx,dy=PINS[p['kind']][pin][1]
        if p.get('flip',0)&1: dx=-dx
        if p.get('flip',0)&2: dy=-dy
        return (p['x']+dx, p['y']+dy)
    def junction(s,x,y):
        j=dict(x=x,y=y); s.__dict__.setdefault('junctions',[]).append(j); return j
    def wire(s, pts, a=None, b=None):
        w=dict(pts=pts, a=a, b=b); s.wires.append(w); return w
    def build(s):
        # emit parts then wires; compute offsets
        out=bytearray(); offs=[]
        order={'LS':0,'BCD':1,'BLUE':1,'GRN':1,'DIPSW_8':2,'74HC374':3,'6116':4}
        s.parts.sort(key=lambda p:order[p['kind']])
        for i,p in enumerate(s.parts): p['serial']=7+i
        pre=SEED[:ms[0]['start']]
        bodies=[]
        pos=len(pre)
        for p in s.parts:
            t=TPL[p['kind']]; b=bytearray(p.get('blob') or t['blob'])
            xo=list(t['xo']); yo=list(t['yo']); ser=t['ser']; L=len(b)
            if p['ref'] and t['ref']:
                o,old=t['ref']; txt=p['ref'].encode(); d=len(txt)-old
                b[o-1]=len(txt); b[o:o+old]=txt
                xo=[v+d if v>=o else v for v in xo]; yo=[v+d if v>=o else v for v in yo]
                if ser>=o: ser+=d
                L=len(b)
            p['_L']=L
            dx=p['x']-t['x0']; dy=p['y']-t['y0']
            for o in xo: b[o:o+4]=struct.pack('<i',(struct.unpack('<i',b[o:o+4])[0]//T+dx)*T)
            for o in yo: b[o:o+4]=struct.pack('<i',(struct.unpack('<i',b[o:o+4])[0]//T+dy)*T)
            b[ser:ser+4]=struct.pack('<I',p['serial'])
            p['_b']=b; p['_off']=pos; pos+=len(b); bodies.append(b)
        # junction dots: pins with >=2 wire ends get a zero-length wire at the tip; pin slot -> dot
        from collections import Counter
        cnt=Counter()
        for w in s.wires:
            for end in ('a','b'):
                if w[end]: cnt[(id(w[end][0]),w[end][1])]+=1
        s.dots=[]
        for p in s.parts:
            for pin in PINS[p['kind']]:
                if cnt[(id(p),pin)]>=2:
                    tip=(p['x']+PINS[p['kind']][pin][1][0], p['y']+PINS[p['kind']][pin][1][1])
                    s.dots.append(dict(pts=[tip,tip],a=(p,pin),b=None,net='dot',dot=True))
        allw=s.wires+s.dots
        # wires
        wb=[]
        for w in allw:
            n=len(w['pts'])
            rec=WHDR+bytes.fromhex('027f5749524500')+struct.pack('<HH',0,n)+b''.join(i32(x)+i32(y) for x,y in w['pts'])
            w['_off']=pos; pos+=len(rec); wb.append(rec)
        # patch pin slots with first wire attached
        for w in s.dots:
            p,pin=w['a']; p['slots'][pin]=w['_off']
        for w in s.wires:
            for end in ('a','b'):
                if w[end]:
                    p,pin=w[end]
                    if pin not in p['slots']: p['slots'][pin]=w['_off']
        for p in s.parts:
            t=TPL[p['kind']]; b=p['_b']; base=len(b)-4*t['n']
            for pin,off in p['slots'].items():
                idx=PINS[p['kind']][pin][0]; b[base+4*idx:base+4*idx+4]=struct.pack('<I',off)
        objs=b''.join(bytes(p['_b']) for p in s.parts)+b''.join(wb)
        # tail from seed
        old_first=ms[0]['start']
        last=SEED.rfind(b'ISIS CIRCUIT FILE')
        tail=SEED[last-1:]
        # old objects region end = last-1
        old_len=(last-1)-old_first; delta=len(objs)-old_len
        new=bytearray(pre+objs+tail)
        # pointer patches: u32 before first marker, and u32 in tail equal to old last-marker offset
        first=SEED.find(b'ISIS CIRCUIT FILE')
        v=struct.unpack('<I',new[first-4:first])[0]; new[first-4:first]=struct.pack('<I',v+delta)
        newlast=len(pre)+len(objs)+1
        ptr_old=struct.pack('<I',last)
        j=bytes(new).find(ptr_old,newlast)
        assert j>0
        new[j:j+4]=struct.pack('<I',last+delta)
        return bytes(new)
    def save(s, path, template_zip=SEEDS+'/seed4.pdsprj'):
        dsn=s.build()
        src=zipfile.ZipFile(template_zip)
        z=zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED)
        for n in src.namelist(): z.writestr(n, dsn if n=='ROOT.DSN' else src.read(n))
        z.close()
