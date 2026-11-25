"""The generic DSN reader: synthetic projects (always) and the whole Proteus sample corpus (opt-in, needs the sandbox)."""
import glob
import zipfile

import pytest

from proteus2py import gen, workspace
from proteus2py.dsn import parse_dsn, read_pdsprj


def test_reads_generated_project(tmp_path):
    parts = [{"id": "S1", "kind": "LS", "x": -5600, "y": 900, "state": 1}, {"id": "U1", "kind": "6116", "x": -3400, "y": 1000},
             {"id": "S2", "kind": "LS", "x": -5600, "y": 600}]
    wires = [{"a": "S1.Q", "b": "U1.A0"}, {"a": "S2.Q", "b": "U1.A1"}, {"a": "S1.Q", "b": "U1.A2"}]
    gen.build(tmp_path / "p.pdsprj", parts, wires)
    r = read_pdsprj(tmp_path / "p.pdsprj")
    assert r.ok and r.version >= 800
    comps = [o for o in r.objects if o["kind"] == "COMPONENT"]
    assert sorted(c["device"] for c in comps) == ["6116", "LOGICSTATE", "LOGICSTATE"]
    assert len([o for o in r.objects if o["kind"] == "WIRE"]) >= 3
    assert any(o["kind"] == "JUNCTION" for o in r.objects)            # S1.Q has two wires


def test_truncated_stream_reports_an_error(tmp_path):
    gen.build(tmp_path / "p.pdsprj", [{"id": "S1", "kind": "LS", "x": 0, "y": 0}], [])
    data = zipfile.ZipFile(tmp_path / "p.pdsprj").read("ROOT.DSN")
    cut = data.find(b"OBJECT DATA") + 40
    assert not parse_dsn(data[:cut]).ok


def _samples():
    base = workspace.proteus_dir() / "DATA" / "SAMPLES"
    return sorted(glob.glob(str(base / "**" / "*.pdsprj"), recursive=True)) if base.is_dir() else []


@pytest.mark.skipif(not _samples(), reason="Proteus sample projects not available (needs the sandbox)")
def test_every_bundled_sample_parses_to_the_end():
    bad = [(f, r.error) for f in _samples() if not (r := parse_dsn(zipfile.ZipFile(f).read("ROOT.DSN"))).ok]
    assert not bad, bad[:3]
