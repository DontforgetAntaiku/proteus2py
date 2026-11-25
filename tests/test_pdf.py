import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proteus2py import pdf  # noqa: E402

D = Path(__file__).parent / "data"


def test_decode_transient():
    r = pdf.load(D / "tran_741.PDF")
    assert [p.name for p in r.probes] == ["U1(POS IP)", "OUT", "IN"]
    assert r.x_min == 0.0 and abs(r.x_max - 1.0) < 1e-12
    out = r.probe("OUT")
    assert abs(out.at(0.0005) - 2.583758620264549) < 1e-12
    assert r.probe("IN").y[1] == 0.01


def test_resample_shape():
    r = pdf.load(D / "tran_741.PDF")
    d = r.resample(50)
    assert len(d["x"]) == 50 and len(d["OUT"]) == 50
