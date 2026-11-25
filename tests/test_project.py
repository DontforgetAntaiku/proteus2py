"""No-GUI project -> netlist -> simulation. Sample-based checks need the sandbox (Proteus sample projects) and are skipped without it."""
import pytest

from proteus2py import gen, project, workspace

SAMPLES = workspace.proteus_dir() / "DATA" / "SAMPLES" / "Graph Based Simulation"
needs_samples = pytest.mark.skipif(not SAMPLES.is_dir(), reason="Proteus sample projects not available")


def test_generated_project_is_translated(tmp_path):
    parts = [{"id": "S1", "kind": "LS", "x": -5600, "y": 900}, {"id": "U1", "kind": "6116", "x": -3400, "y": 1000}]
    gen.build(tmp_path / "p.pdsprj", parts, [{"a": "S1.Q", "b": "U1.A0"}])
    ps = project.project_to_spec(tmp_path / "p.pdsprj")
    assert ps.spec["analysis"] == {"type": "op"}
    by = {p["ref"]: p for p in ps.spec["parts"]}
    assert by["S1"]["kind"] == "DGEN"                            # LOGICSTATE -> one-bit digital generator
    # with a Proteus install the 6116 RAM becomes a DSIM memory primitive; without its libraries it is reported as unsupported
    assert any(r.startswith("U1") and p["kind"] == "DIGITAL" for r, p in by.items()) or "U1" in ps.unsupported


@needs_samples
def test_sample_netlist_structure():
    ps = project.project_to_spec(SAMPLES / "Rescap.pdsprj", {"type": "ac", "fstart": 10, "fstop": 1e5, "points": 10})
    kinds = sorted(p["kind"] for p in ps.spec["parts"])
    assert kinds == ["C", "R", "V", "V"]                      # R, C, the SINE generator and the current probe as a 0 V source
    assert not ps.unsupported


@pytest.mark.wine
@needs_samples
def test_rescap_bandwidth_without_gui():
    from proteus2py import sim
    r, ps = project.simulate_project_file(SAMPLES / "Rescap.pdsprj", {"type": "ac", "fstart": 10, "fstop": 1e5, "points": 20}, "it_pj")
    node = next(n for n in r.probes if n.startswith("v("))
    bw = max(sim.measure_run("it_pj", "bandwidth", p)["f_high_3db"] or 0 for p in r.probes if p.startswith("v("))
    assert bw == pytest.approx(1000, rel=5e-3)                # R = 159.134 ohm, C = 1 uF
    assert node
