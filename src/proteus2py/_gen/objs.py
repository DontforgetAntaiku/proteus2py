import struct,re
T=2540
NAMES=[b'LOGICSTATE',b'LOGICPROBE (BIG)',b'AND',b'OR',b'6116',b'74HC374',b'DIPSW_8',b'7SEG-BCD-BLUE',b'7SEG-BCD-GRN',b'7SEG-BCD']
def parse(b):
    start=b.find(b'OBJECT DATA')
    mains=[]
    for nm in NAMES:
        pat=bytes([len(nm)])+nm
        for m in re.finditer(re.escape(pat),b[start:]):
            o=start+m.start()
            if o==0 or b[o-1]==0xff: continue
            x,y=struct.unpack('<ii',b[o+1+len(nm):o+9+len(nm)])
            if x%T or y%T: continue
            mains.append(dict(off=o,name=nm.decode(),x=x//T,y=y//T))
    mains.sort(key=lambda d:d['off'])
    # object start: look back for 'COMPONENT ID\0'
    for d in mains:
        p=b.rfind(b'COMPONENT ID\x00',0,d['off'])
        for ln in range(0,10):
            q=p-49-ln
            if b[q]==0xff and b[q+1]==ln: d['start']=q-1; break
        else: raise Exception('no start '+hex(d['off']))
    return mains
def objects(b):
    ms=parse(b)
    for i,d in enumerate(ms):
        d['end']=ms[i+1]['start'] if i+1<len(ms) else None
    return ms
if __name__=='__main__':
    b=open('/tmp/d3/seed3.DSN','rb').read()
    for d in objects(b): print(d['name'],hex(d['start']),hex(d['off']),d['x'],d['y'],hex(d['end']) if d['end'] else None)
