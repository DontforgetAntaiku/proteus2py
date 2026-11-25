"""Design.build rewritten on top of re/pdsfmt.assemble (wires placed after completing object, junction objects)."""
import sys, struct
from . import pdsfmt
from . import dsn
from .dsn import Design, TPL, PINS, SEED, ms, T
from collections import defaultdict

def build(s):
    order={'LS':0,'BCD':1,'BLUE':1,'GRN':1,'DIPSW_8':2,'74HC374':3,'6116':4}
    s.parts.sort(key=lambda p:order[p['kind']])
    for i,p in enumerate(s.parts): p['serial']=7+i
    pre=SEED[:ms[0]['start']]
    items=[]
    for p in s.parts:
        t=TPL[p['kind']]; b=bytearray(p.get('blob') or t['blob'])
        xo=list(t['xo']); yo=list(t['yo']); ser=t['ser']
        if p['ref'] and t['ref']:
            o,old=t['ref']; txt=p['ref'].encode(); d=len(txt)-old
            b[o-1]=len(txt); b[o:o+old]=txt
            xo=[v+d if v>=o else v for v in xo]; yo=[v+d if v>=o else v for v in yo]
            if ser>=o: ser+=d
        dx=p['x']-t['x0']; dy=p['y']-t['y0']
        for o in xo: b[o:o+4]=struct.pack('<i',(struct.unpack('<i',b[o:o+4])[0]//T+dx)*T)
        for o in yo: b[o:o+4]=struct.pack('<i',(struct.unpack('<i',b[o:o+4])[0]//T+dy)*T)
        b[ser:ser+4]=struct.pack('<I',p['serial'])
        if p.get('flip'): struct.pack_into('<H',b,xo[-1]+10,p['flip'])
        n=t['n']; so=len(b)-4*n
        b[so:]=b'\0'*(4*n)
        p['_idx']=len(items)
        items.append(dict(blob=b,slots_off=so,nslots=n,kind='COMPONENT'))
    jitems=[]
    for j in getattr(s,'junctions',[]):
        J=pdsfmt.make_junction(j['x'],j['y']); j['_idx']=len(items); items.append(J)
    # collect endpoints per pin
    ends=defaultdict(list)   # (id(part),pin) -> list of (wire, 'a'|'b')
    for w in s.wires:
        for e in ('a','b'):
            if w[e] and w[e][0]!='JN': ends[(id(w[e][0]),w[e][1])].append((w,e))
            elif w[e]: w['_ep_'+e]=(w[e][1]['_idx'],w[e][2])
    final=[]    # wire dicts for assemble
    def slot(p,pin): return (p['_idx'],PINS[p['kind']][pin][0])
    byid={id(p):p for p in s.parts}
    for (pid,pin),lst in ends.items():
        p=byid[pid]; tip=s.tip(p,pin)
        if len(lst)==1:
            w,e=lst[0]; w['_ep_'+e]=slot(p,pin)
        else:
            rem=list(lst); prev=slot(p,pin)
            while True:
                J=pdsfmt.make_junction(*tip); ji=len(items); items.append(J)
                final.append(dict(a=prev,b=(ji,0),pts=[tip,tip]))
                if len(rem)<=3:
                    for k,(w,e) in enumerate(rem): w['_ep_'+e]=(ji,1+k)
                    break
                for k,(w,e) in enumerate(rem[:2]): w['_ep_'+e]=(ji,1+k)
                rem=rem[2:]; prev=(ji,3)
    for w in s.wires:
        a=w.get('_ep_a'); b=w.get('_ep_b')
        assert a and b,(w['net'],a,b)
        final.append(dict(a=a,b=b,pts=w['pts']))
    objs,keys=pdsfmt.assemble(items,final,len(pre))
    last=SEED.rfind(b'ISIS CIRCUIT FILE'); tail=SEED[last-1:]
    old_len=(last-1)-ms[0]['start']; delta=len(objs)-old_len
    new=bytearray(pre+objs+tail)
    first=SEED.find(b'ISIS CIRCUIT FILE')
    v=struct.unpack('<I',new[first-4:first])[0]; new[first-4:first]=struct.pack('<I',v+delta)
    newlast=len(pre)+len(objs)+1
    j=bytes(new).find(struct.pack('<I',last),newlast); assert j>0
    new[j:j+4]=struct.pack('<I',last+delta)
    return bytes(new)
Design.build=build

from . import cdbfmt
LSP=lambda st:['{PRIMITIVE=DIGITAL,RTDSTATE}','{STATE=%d}'%st]
DSW_PINS=[('COM%d'%i,str(i)) for i in range(1,9)]+[('NO%d'%i,str(17-i)) for i in range(1,9)]
R6116=[(n,str(x)) for n,x in zip(['A0','A1','A2','A3','A4','A5','A6','A7','A8','A9','A10','$CE$','$OE$','$WE$','D0','D1','D2','D3','D4','D5','D6','D7','VCC','GND'],[8,7,6,5,4,3,2,1,23,22,19,18,20,21,9,10,11,13,14,15,16,17,24,12])]
P6116=['{MODFILE=6116.MDF}','{ITFMOD=NMOS}','{TAA=100ns}','{TCE=100ns}','{TOE=50ns}','{TOD=35ns}','{TWP=60ns}','{PACKAGE=DIL24}']
H374=[(n,str(x)) for n,x in zip(['D0','Q0','D1','Q1','D2','Q2','D3','Q3','D4','Q4','D5','Q5','D6','Q6','D7','Q7','$OE$','CLK','VCC','GND'],[3,2,4,5,7,6,8,9,13,12,14,15,17,16,18,19,1,11,20,10])]
P374=['{MODFILE=74XX374.MDF}','{PACKAGE=DIL20}','{INIT=0}','{ITFMOD=TTLHC}']
SEG=[('D3',''),('D2',''),('D1',''),('D0','')]
def make_cdb(s, setpoint=0):
    import re as _re
    DSWP=['{MODFILE=DIPSW}','{ELEMENTS=8}','{SETPOINT=%d}'%setpoint,'{SWTIME=1m}','{RON=1}','{ROFF=100M}']
    parts=[]
    for p in s.parts:
        k=p['kind']
        if k=='LS':
            st=1 if b'{STATE=1}' in (p.get('blob') or b'') else 0
            parts.append((p['serial'],p['ref'] or '','LOGICSTATE',[('Q0','')],LSP(st),{'unit':0}))
        elif k=='6116': parts.append((p['serial'],p['ref'],'6116',R6116,P6116,{'unit':0}))
        elif k=='74HC374': parts.append((p['serial'],p['ref'],'74HC374',H374,P374,{'unit':0}))
        elif k=='DIPSW_8': parts.append((p['serial'],p['ref'],'DIPSW_8',DSW_PINS,DSWP,{'unit':0}))
        else:
            dev={'BCD':'7SEG-BCD','BLUE':'7SEG-BCD-BLUE','GRN':'7SEG-BCD-GRN'}[k]
            parts.append((p['serial'],'',dev,SEG,[],{}))
    return cdbfmt.build_cdb(parts)
Design.make_cdb=make_cdb
