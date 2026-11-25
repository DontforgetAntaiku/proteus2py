"""Decoder for PROSPICE "LISA PARTITION DATA FILE" (.PDF) analogue results.

Layout (reverse-engineered from graph-simulation runs, 2026-11):
  0x00 "LISA PARTITION DATA FILE"; u32@0x28 = offset of the probe table; u16@0x2c = probe count; u32@0x30 = data start.
  data: records `FF FF <x: f64>` then 0..n changes `<probe idx: u16><value: f64>` (a probe is only listed when its value changed);
        x is time (transient), frequency (AC) or the swept source value (DC sweep). `FE FF` ends the data.
  probe table: per probe `pstr id`, `pstr name`, u16 1, u16 type (8 = analogue).
Between a probe's changes the value is interpolated linearly (that is how Proteus draws analogue traces).
"""
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, field, replace
from pathlib import Path

MAGIC = b"LISA PARTITION DATA FILE"
KIND_REAL, KIND_COMPLEX = 8, 16        # probe table type codes seen so far (digital kinds not decoded)


@dataclass
class Probe:
    index: int
    id: str
    name: str
    kind: int
    x: list[float] = field(default_factory=list)
    y: list = field(default_factory=list)          # float, or complex for AC probes
    interp: str = "linear"                         # "linear" (Proteus draws analogue traces this way) | "hold" (step: value of the last sample)

    def with_interp(self, mode: str) -> Probe:
        """Copy that evaluates with `mode` ("linear" | "hold")."""
        if mode not in ("linear", "hold"):
            raise ValueError("interp: linear | hold")
        return replace(self, interp=mode)

    def at(self, x: float) -> float | complex:
        """Value at x: linear interpolation (default) or zero-order hold; clamped at the ends."""
        if not self.x:
            return float("nan")
        if self.interp == "hold":
            j = bisect.bisect_right(self.x, x) - 1
            return self.y[max(j, 0)]
        i = bisect.bisect_left(self.x, x)
        if i == 0:
            return self.y[0]
        if i >= len(self.x):
            return self.y[-1]
        x0, x1 = self.x[i - 1], self.x[i]
        if x1 == x0:
            return self.y[i]
        return self.y[i - 1] + (self.y[i] - self.y[i - 1]) * (x - x0) / (x1 - x0)

    @property
    def complex(self) -> bool:
        return self.kind == KIND_COMPLEX


@dataclass
class Result:
    probes: list[Probe]
    x_min: float
    x_max: float
    records: int
    family: list = field(default_factory=list)       # all curves of a nested sweep (see parse)

    def probe(self, name: str) -> Probe:
        for p in self.probes:
            if p.name == name or p.id == name:
                return p
        raise KeyError(f"no probe {name!r}; have {[p.name for p in self.probes]}")

    def resample(self, n: int = 200, log: bool = False, x_min: float | None = None, x_max: float | None = None) -> dict:
        lo = self.x_min if x_min is None else x_min
        hi = self.x_max if x_max is None else x_max
        if log and lo > 0:
            r = (hi / lo) ** (1 / max(n - 1, 1))
            xs = [lo * r ** i for i in range(n)]
        else:
            xs = [lo + (hi - lo) * i / max(n - 1, 1) for i in range(n)]
        return {"x": xs, **{p.name: [p.at(x) for x in xs] for p in self.probes}}


def _pstr(b: bytes, p: int) -> tuple[str, int]:
    n = b[p]
    return b[p + 1:p + 1 + n].decode("latin-1"), p + 1 + n


def _curve(data: bytes, start: int, end: int, template: list[Probe]) -> Result:
    probes = [Probe(p.index, p.id, p.name, p.kind) for p in template]
    pos, recs, x = start, 0, 0.0
    while pos + 10 <= end:
        if data[pos:pos + 2] != b"\xff\xff":
            raise ValueError(f"bad record marker at {pos:#x}")
        x = struct.unpack_from("<d", data, pos + 2)[0]
        pos += 10
        recs += 1
        while pos + 10 <= end and data[pos:pos + 2] != b"\xff\xff":
            idx = struct.unpack_from("<H", data, pos)[0]
            kind = probes[idx].kind if idx < len(probes) else KIND_REAL
            if kind == KIND_COMPLEX:
                v = complex(*struct.unpack_from("<dd", data, pos + 2))
                pos += 18
            else:
                v = struct.unpack_from("<d", data, pos + 2)[0]
                pos += 10
            if idx < len(probes):
                pr = probes[idx]
                if pr.x and pr.x[-1] == x:
                    pr.y[-1] = v                      # same x twice: keep the last value
                else:
                    pr.x.append(x)
                    pr.y.append(v)
    xs = [q.x[0] for q in probes if q.x]
    xe = [q.x[-1] for q in probes if q.x]
    return Result(probes, min(xs) if xs else 0.0, max(xe) if xe else x, recs)


def parse(data: bytes) -> Result:
    """Decode a result file. Nested DC sweeps hold one data block per outer step: `Result.family` lists them (family[0] is the result itself)."""
    if not data.startswith(MAGIC):
        raise ValueError("not a LISA partition data file")
    table = struct.unpack_from("<I", data, 0x28)[0]
    nprobe, ncurves = struct.unpack_from("<HH", data, 0x2C)
    ncurves = max(ncurves, 1)
    starts = list(struct.unpack_from(f"<{ncurves}I", data, 0x30))
    end = table - 2 if data[table - 2:table] == b"\xfe\xff" else table
    template: list[Probe] = []
    p = table
    for i in range(nprobe):
        pid, p = _pstr(data, p)
        name, p = _pstr(data, p)
        _, kind = struct.unpack_from("<HH", data, p)
        p += 4
        template.append(Probe(i, pid, name, kind))
    curves = [_curve(data, st, (starts[k + 1] if k + 1 < len(starts) else end), template) for k, st in enumerate(starts)]
    first = curves[0]
    first.family = curves
    return first


def load(path: str | Path) -> Result:
    return parse(Path(path).read_bytes())
