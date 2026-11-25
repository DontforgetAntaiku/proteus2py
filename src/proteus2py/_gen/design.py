import zipfile, struct, sys
from . import dsn
from .dsn import Design, TPL, T, PINS, SEED, ms

def tpl_edit(kind, old, new):
    """replace text inside a template's PROPERTIES block, fixing length field and later offsets"""
    t=TPL[kind]; b=bytearray(t['blob']); i=bytes(b).find(old); assert i>=0,(kind,old)
    d=len(new)-len(old)
    if d:
        pi=bytes(b).find(b'PROPERTIES\x00'); ls=pi+len(b'PROPERTIES\x00')+4
        ln=struct.unpack('<I',b[ls:ls+4])[0]; assert ls+4<=i<ls+4+ln,(ls,ln,i)
        b[ls:ls+4]=struct.pack('<I',ln+d)
    b[i:i+len(old)]=new
    if d:
        for key in ('xo','yo'): t[key]=[o+d if o>i else o for o in t[key]]
        for key in ('ser','L','main'):
            if key in t and t[key]>i: t[key]+=d
        if t.get('ref') and t['ref'][0]>i: pass
    t['blob']=bytes(b)
def ls_blob(state):
    b=bytearray(TPL['LS']['blob']); j=bytes(b).find(b'{STATE=0}'); b[j:j+9]=b'{STATE=%d}'%state; return bytes(b)

class D2(Design):
    def addls(self,x,y,state=0):
        p=self.add('LS',x,y); p['state']=state; return p
    def build(self):
        orig=TPL['LS']['blob']
        # patch per part state by temporarily swapping template blob
        # (emit parts one by one is inside Design.build; so wrap)
        return Design.build(self)
