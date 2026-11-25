"""Integration tests against real PROSPICE in the Wine sandbox: PROTEUS_INTEGRATION=1 PROTEUS2PY_HOME=<sandbox> pytest."""
import math

import pytest

pytestmark = pytest.mark.wine


def test_divider_op():
    from proteus2py import sim
    r = sim.simulate({"parts": [{"ref": "V1", "kind": "V", "nodes": ["in", "0"], "value": 10},
                                {"ref": "R1", "kind": "R", "nodes": ["in", "out"], "value": "1k"},
                                {"ref": "R2", "kind": "R", "nodes": ["out", "0"], "value": "2k"}],
                      "analysis": {"type": "op"}}, "it_div")
    assert r.op["v(out)"] == pytest.approx(20 / 3, rel=1e-6)


def test_rc_bandwidth():
    from proteus2py import sim, spice
    r = sim.simulate(spice.parse("rc\nV1 in 0 AC 1\nR1 in out 1k\nC1 out 0 159.155n\n.ac dec 20 10 1meg\n"), "it_rc")
    assert r.gain["v(out)"].f_high_3db == pytest.approx(1000, rel=2e-3)


def test_rc_step_response():
    from proteus2py import sim, spice
    sim.simulate(spice.parse("rc\nV1 in 0 PULSE(0 5 1m 1u 1u 100m 1)\nR1 in out 1k\nC1 out 0 1u\n.tran 5u 6m\n"), "it_step")
    v = sim.get("it_step")["result"].probe("v(out)").at(2e-3)
    assert v == pytest.approx(5 * (1 - math.exp(-1)), rel=2e-3)


def test_opamp_library_subckt_converges():
    from proteus2py import sim, spice
    r = sim.simulate(spice.parse("t\nVCC vcc 0 15\nVEE vee 0 -15\nVIN in 0 0.5\nR1 n 0 1k\nR2 n out 10k\nX1 in n vcc vee out OP07\n.op\n"), "it_op07")
    assert r.op["v(out)"] == pytest.approx(5.5, rel=1e-3)


def test_resistor_thermal_noise():
    from proteus2py import sim
    r = sim.simulate({"parts": [{"ref": "V1", "kind": "V", "nodes": ["in", "0"], "value": 0, "ac": True},
                                {"ref": "R1", "kind": "R", "nodes": ["in", "out"], "value": "10k"},
                                {"ref": "R2", "kind": "R", "nodes": ["out", "0"], "value": "10k"}],
                      "analysis": {"type": "noise", "output": "out", "input": "V1", "fstart": 100, "fstop": "100k", "points": 5}}, "it_noise")
    expect = math.sqrt(4 * 1.380649e-23 * 300.15 * 5000)                     # R1 || R2 at 27 C
    assert r.noise.output_V_per_rtHz["at_start"] == pytest.approx(expect, rel=1e-3)
    assert r.noise.input_referred_V_per_rtHz["at_start"] == pytest.approx(2 * expect, rel=1e-3)


def test_current_controlled_sources():
    from proteus2py import sim, spice
    r = sim.simulate(spice.parse("t\nV1 in 0 1\nR1 in s 1k\nVS s 0 0\nF1 out 0 VS 2\nR2 out 0 1k\nH1 o2 0 VS 1000\nR3 o2 0 1k\n.op\n"), "it_fh")
    assert r.op["v(out)"] == pytest.approx(-2.0, rel=1e-4)      # SPICE: F pulls current out of n+
    assert r.op["v(o2)"] == pytest.approx(1.0, rel=1e-4)


def test_transformer_coupling():
    from proteus2py import sim, spice
    r = sim.simulate(spice.parse("t\nV1 s 0 SIN(0 1 1k)\nRS s in 1\nL1 in 0 10m\nL2 out 0 40m\nK1 L1 L2 0.999\nRL out 0 100k\n.tran 5u 5m\n"), "it_xfmr")
    assert r.stats["v(out)"].max == pytest.approx(2.0, rel=0.05)         # turns ratio sqrt(40m/10m) = 2


def test_switch_and_behavioural_source():
    from proteus2py import sim, spice
    r = sim.simulate(spice.parse("sw\nV1 in 0 5\nVC c 0 PULSE(0 5 1m 1u 1u 2m 5m)\nS1 in out c 0 SW1\nRL out 0 1k\n"
                                 ".model SW1 SW(vt=2.5 vh=0.1 ron=1 roff=1meg)\n.tran 10u 10m\n.probe v(out)\n"), "it_sw")
    assert r.stats["v(out)"].max == pytest.approx(5.0, abs=0.02) and r.stats["v(out)"].min < 0.01
    r = sim.simulate(spice.parse("b\nV1 in 0 SIN(0 1 1k)\nB1 out 0 V=3*v(in)+0.5\nR1 out 0 1k\n.tran 10u 3m\n.probe v(out)\n"), "it_b")
    assert r.stats["v(out)"].max == pytest.approx(3.5, abs=0.01)


def test_current_source_polarity_dc_and_wave():
    from proteus2py import sim, spice
    for deck, v in (("t\nI1 0 n 1m\nR1 n 0 1k\n.op\n", 1.0), ("t\nI1 n 0 1m\nR1 n 0 1k\n.op\n", -1.0)):
        assert sim.simulate(spice.parse(deck), "it_i").op["v(n)"] == pytest.approx(v, rel=1e-6)
    r = sim.simulate(spice.parse("t\nI1 0 n SIN(0.5m 1m 1k)\nR1 n 0 1k\n.tran 10u 2m\n"), "it_isine")
    assert r.stats["v(n)"].mean == pytest.approx(0.5, abs=0.01)


def test_vccs_and_library_generic_transistor():
    from proteus2py import sim, spice
    r = sim.simulate(spice.parse("t\nV1 in 0 1m\nG1 out 0 in 0 2m\nR1 out 0 1k\n.op\n"), "it_g")
    assert r.op["v(out)"] == pytest.approx(-2e-3, rel=1e-4)                  # SPICE: G pulls current out of n+
    r = sim.simulate(spice.parse("t\nVCC vcc 0 12\nR1 vcc b 47k\nR2 b 0 10k\nRC vcc c 2.2k\nRE e 0 470\nQ1 c b e BC547\n.op\n"), "it_bc547")
    assert 5.0 < r.op["v(c)"] < 7.5 and 1.0 < r.op["v(e)"] < 1.6          # biased active region with the generic LX_NPN_SSLF model


def test_current_switch_and_b_source_with_branch_current():
    from proteus2py import sim, spice
    deck = "t\nV1 a 0 1\nVS s 0 0\nR1 a s 1k\nW1 a out VS SWI\nRL out 0 1k\n.model SWI CSW(it=0.5m ih=0.1m ron=1 roff=1e6)\n.op\n"
    r = sim.simulate(spice.parse(deck), "it_w")                       # 1 mA through VS > 0.5 mA threshold: switch closed
    assert r.op["v(out)"] == pytest.approx(1.0, abs=0.01)
    r = sim.simulate(spice.parse("t\nV1 a 0 2\nVS s 0 0\nR1 a s 1k\nB1 x 0 V=3000*i(VS)\nR2 x 0 1k\n.op\n"), "it_bi")
    assert r.op["v(x)"] == pytest.approx(6.0, rel=1e-3)               # i(VS) = 2 mA


def test_distortion_analysis_of_a_clipping_stage():
    from proteus2py import sim
    spec = {"parts": [{"ref": "V1", "kind": "V", "nodes": ["in", "0"], "wave": {"type": "sine", "amp": 0.1, "freq": 1000}},
                      {"ref": "R1", "kind": "R", "nodes": ["in", "out"], "value": "1k"},
                      {"ref": "D1", "kind": "D", "nodes": ["out", "0"], "model": "1N4148"}],
            "analysis": {"type": "distortion", "output": "out", "source": "V1", "f0": 1000, "periods": 8, "settle": 2}}
    r = sim.simulate(spec, "it_dist")
    d = r.distortion
    assert d is not None and d.fundamental == pytest.approx(0.1, rel=0.02)     # the diode barely conducts at 0.1 V: nearly linear
    clipped = sim.simulate({**spec, "parts": [{**spec["parts"][0], "wave": {"type": "sine", "amp": 3, "freq": 1000}}, *spec["parts"][1:]]}, "it_dist2")
    assert clipped.distortion.thd_pct > 5 * d.thd_pct and clipped.distortion.harmonics_rel_pct[0] > 1   # 2nd harmonic from half-wave clipping
