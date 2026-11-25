import math
from pathlib import Path

import pytest

from proteus2py import measure, pdf

D = Path(__file__).parent / "data"


def test_ac_decode_and_bandwidth():
    r = pdf.load(D / "ac_rc.PDF")
    p = r.probe("v(out)")
    assert p.complex
    assert measure.bandwidth(p)["f_high_3db"] == pytest.approx(1000, rel=1e-3)
    row = min(measure.ac_table(p), key=lambda x: abs(x["f"] - 1000))
    assert row["db"] == pytest.approx(-3.0, abs=0.1) and row["phase_deg"] == pytest.approx(-45, abs=3)


def test_stats_and_table():
    r = pdf.load(D / "tran_741.PDF")
    st = measure.stats(r.probe("OUT"))
    assert st["min"] < st["mean"] < st["max"]
    t = measure.table(r, ["OUT"], 5)
    assert t["columns"] == ["x", "OUT"] and len(t["rows"]) == 5


def test_harmonics_of_synthetic_sine():
    p = pdf.Probe(0, "s", "s", 8)
    f = 1000.0
    n = 4000
    p.x = [i * 5e-3 / n for i in range(n + 1)]
    p.y = [math.sin(2 * math.pi * f * t) + 0.01 * math.sin(2 * math.pi * 3 * f * t) for t in p.x]
    h = measure.harmonics(p, f, periods=3)
    assert h["fund"] == pytest.approx(1.0, rel=1e-3)
    assert h["thd_pct"] == pytest.approx(1.0, rel=2e-2)


def test_nested_dc_sweep_family():
    r = pdf.load(D / "dc_family.PDF")
    assert len(r.family) == 11                       # IB = 100 uA ... 1 mA in 90 uA steps
    ic = [c.probe("IC") for c in r.family]
    assert ic[0].x[0] == 0.0 and ic[0].x[-1] == pytest.approx(10.0)
    assert ic[-1].y[-1] > ic[0].y[-1] * 4            # collector current grows with base current
