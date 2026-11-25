"""Geometric conflict check for generated wires: wires of different nets that touch or overlap merge into one net in Proteus."""
from __future__ import annotations

from .builder import Design

Point = tuple[int, int]


def _on(p: Point, s: Point, t: Point) -> bool:
    (x, y), (a, b), (c, e) = p, s, t
    return min(a, c) <= x <= max(a, c) and min(b, e) <= y <= max(b, e) and (a == c == x or b == e == y)


def conflicts(d: Design) -> list[tuple[object, ...]]:
    segs = [(w.net, p, q) for w in d.wires for p, q in zip(w.pts, w.pts[1:])]
    bad: list[tuple[object, ...]] = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            n1, p1, q1 = segs[i]
            n2, p2, q2 = segs[j]
            if n1 == n2:
                continue
            bad += [("touch", n1, n2, pt) for pt in (p1, q1) if _on(pt, p2, q2)]
            bad += [("touch", n2, n1, pt) for pt in (p2, q2) if _on(pt, p1, q1)]
            if p1[0] == q1[0] == p2[0] == q2[0]:
                lo, hi = max(min(p1[1], q1[1]), min(p2[1], q2[1])), min(max(p1[1], q1[1]), max(p2[1], q2[1]))
                if lo < hi:
                    bad.append(("overlapV", n1, n2, p1[0], lo, hi))
            if p1[1] == q1[1] == p2[1] == q2[1]:
                lo, hi = max(min(p1[0], q1[0]), min(p2[0], q2[0])), min(max(p1[0], q1[0]), max(p2[0], q2[0]))
                if lo < hi:
                    bad.append(("overlapH", n1, n2, p1[1], lo, hi))
    return bad
