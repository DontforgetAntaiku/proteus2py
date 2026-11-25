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
    r = pdf.load(D / "tran_rc.PDF")
    st = measure.stats(r.probe("v(out)"))
    assert st["min"] < st["mean"] < st["max"]
    t = measure.table(r, ["v(out)"], 5)
    assert t["columns"] == ["x", "v(out)"] and len(t["rows"]) == 5


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
    assert len(r.family) == 5                        # IB = 20 .. 100 uA in 20 uA steps
    ic = [c.probe("i(VCE)") for c in r.family]
    assert ic[0].x[0] == 0.0 and ic[0].x[-1] == pytest.approx(10.0)
    assert abs(ic[-1].y[-1]) > abs(ic[0].y[-1]) * 3     # collector current grows with base current


def test_hold_interpolation_keeps_steps():
    p = pdf.Probe(0, "s", "s", 8)
    p.x, p.y = [0.0, 1.0, 1.0001, 2.0], [0.0, 0.0, 5.0, 5.0]       # sparse samples around an edge
    assert p.at(0.5) == 0.0
    h = p.with_interp("hold")
    assert h.at(1.00005) == 0.0 and h.at(1.5) == 5.0 and h.at(-1) == 0.0
    assert p.at(1.00005) == pytest.approx(2.5, rel=1e-3)             # linear interpolation draws the edge as a ramp
    with pytest.raises(ValueError):
        p.with_interp("cubic")


def test_numpy_and_python_harmonics_agree():
    np = pytest.importorskip("numpy")
    p = pdf.Probe(0, "s", "s", 8)
    f, n = 1000.0, 5000
    p.x = [i * 6e-3 / n for i in range(n + 1)]
    p.y = [math.sin(2 * math.pi * f * t) + 0.02 * math.sin(2 * math.pi * 3 * f * t) + 0.3 for t in p.x]
    fast = measure.harmonics(p, f, periods=4)
    slow = measure.harmonics(p.with_interp("hold"), f, periods=4)             # hold forces the pure-Python path
    assert np.isclose(fast["thd_pct"], 2.0, rtol=0.02) and abs(fast["thd_pct"] - slow["thd_pct"]) < 0.05
    assert fast["dc"] == pytest.approx(0.3, abs=2e-3)
