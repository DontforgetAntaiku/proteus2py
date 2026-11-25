import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proteus2py import pdf  # noqa: E402

D = Path(__file__).parent / "data"


def test_decode_transient():
    r = pdf.load(D / "tran_rc.PDF")
    assert [p.name for p in r.probes] == ["v(in)", "v(out)"]
    assert r.x_min == 0.0 and abs(r.x_max - 6e-3) < 1e-12
    out = r.probe("v(out)")
    assert out.at(2e-3) == pytest.approx(5 * (1 - math.exp(-1)), rel=2e-3)      # RC step, tau = 1 ms, edge at 1 ms
    assert r.probe("v(in)").y[-1] == 5.0


def test_resample_shape():
    r = pdf.load(D / "tran_rc.PDF")
    d = r.resample(50)
    assert len(d["x"]) == 50 and len(d["v(out)"]) == 50
