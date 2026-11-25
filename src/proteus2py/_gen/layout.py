"""Geometric conflict check for generated wires (different nets that touch/overlap merge in Proteus)."""


def check(d):
    # geometric conflicts between wires of different nets
    segs=[]
    for w in d.wires:
        for p,q in zip(w['pts'],w['pts'][1:]): segs.append((w['net'],p,q))
    bad=[]
    def on(p,s,t):
        (x,y),(a,b),(c,e)=p,s,t
        return min(a,c)<=x<=max(a,c) and min(b,e)<=y<=max(b,e) and (a==c==x or b==e==y)
    for i in range(len(segs)):
        for j in range(i+1,len(segs)):
            n1,p1,q1=segs[i]; n2,p2,q2=segs[j]
            if n1==n2: continue
            # collinear overlap or endpoint-touch
            for P in (p1,q1):
                if on(P,p2,q2): bad.append(('touch',n1,n2,P))
            for P in (p2,q2):
                if on(P,p1,q1): bad.append(('touch',n2,n1,P))
            if p1[0]==q1[0]==p2[0]==q2[0]:
                lo=max(min(p1[1],q1[1]),min(p2[1],q2[1])); hi=min(max(p1[1],q1[1]),max(p2[1],q2[1]))
                if lo<hi: bad.append(('overlapV',n1,n2,p1[0],lo,hi))
            if p1[1]==q1[1]==p2[1]==q2[1]:
                lo=max(min(p1[0],q1[0]),min(p2[0],q2[0])); hi=min(max(p1[0],q1[0]),max(p2[0],q2[0]))
                if lo<hi: bad.append(('overlapH',n1,n2,p1[1],lo,hi))
    return bad
