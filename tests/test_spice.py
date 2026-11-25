import pytest

from proteus2py import spice
from proteus2py.sdf import spice_number


def test_spice_numbers():
    assert spice_number("1k") == 1000
    assert spice_number("1MEG") == 1e6
    assert spice_number("68.6M") == pytest.approx(0.0686)       # SPICE: M is milli
    assert spice_number("222P") == pytest.approx(2.22e-10)
    assert spice_number("10uF") == pytest.approx(1e-5)
    assert spice_number("3E-3") == 0.003


def test_rc_deck():
    s = spice.parse("""RC lowpass
V1 in 0 DC 0 AC 1 SIN(0 1 1k)
R1 in out {rv}
C1 out 0 159.155n
.param rv=1k
.ac dec 10 10 1meg
.end""")
    assert s["title"] == "RC lowpass"
    assert [p["ref"] for p in s["parts"]] == ["V1", "R1", "C1"]
    assert s["parts"][0]["ac"] and s["parts"][0]["wave"]["freq"] == 1000
    assert s["parts"][1]["value"] == 1000
    assert s["analysis"] == {"type": "ac", "mode": "dec", "points": 10.0, "fstart": 10.0, "fstop": 1e6}
    assert s["probes"] == ["v(in)", "v(out)"]


def test_pulse_and_pwl_and_tran():
    s = spice.parse("""t
V1 a 0 PULSE(0 5 1m 1u 1u 2m 5m)
V2 b 0 PWL(0 0 1m 5 2m 0)
R1 a b 1k
.tran 10u 10m
""")
    w = s["parts"][0]["wave"]
    assert w["type"] == "pulse" and w["high"] == 5 and w["period"] == 5e-3 and w["delay"] == 1e-3
    assert s["parts"][1]["wave"]["points"] == [[0, 0], [1e-3, 5], [2e-3, 0]]
    assert s["analysis"]["tstop"] == pytest.approx(0.01) and s["analysis"]["tmax"] == pytest.approx(1e-5)


DECK_SUB = """t
V1 in 0 1
X1 in out DIV
R9 out 0 1k
.subckt DIV a b
R1 a b 1k
R2 b 0 1k
.ends
.op
"""


def test_subckt_flatten_fallback():
    s = spice.parse(DECK_SUB, native=False)
    refs = {p["ref"]: p for p in s["parts"]}
    assert refs["X1.R1"]["nodes"] == ["in", "out"] and refs["X1.R2"]["nodes"] == ["out", "0"]
    assert s["probes"] == ["v(in)", "v(out)"]


def test_subckt_native_keeps_text():
    s = spice.parse(DECK_SUB)
    x = next(p for p in s["parts"] if p["kind"] == "X")
    assert x["nodes"] == ["in", "out"] and x["subckt"] == "DIV"
    assert ".SUBCKT DIV a b" in s["subckts"]["DIV"] and "R2 b 0 1k" in s["subckts"]["DIV"]


def test_unsupported_elements_are_reported():
    with pytest.raises(spice.SpiceError, match="unknown element"):
        spice.parse("t\nT1 a 0 b 0 TL\n")
    with pytest.raises(spice.SpiceError, match="host files"):
        spice.parse("t\n.include foo.lib\n")


def test_expressions():
    assert spice.evaluate("{2*1k + x}", {"x": 5}) == 2005
    assert spice.evaluate("'sqrt(4)'", {}) == 2


def test_w_switch_and_b_current_are_translated():
    s = spice.parse("t\nV1 a 0 1\nVS s 0 0\nR1 a s 1k\nW1 a out VS SWI\nRL out 0 1k\n.model SWI CSW(it=0.5m ih=0.1m ron=1 roff=1e6)\n"
                    "B1 x 0 V=2000*i(VS)\nR2 x 0 1k\n.op\n")
    kinds = [(p["ref"], p["kind"]) for p in s["parts"]]
    assert any(k == "H" for _, k in kinds)
    assert len([1 for p in s["parts"] if p["kind"] == "X"]) == 2
    assert any(".SUBCKT CSW_W1" in t for t in s["subckts"].values()) and any("B1" in t for t in s["subckts"].values())
