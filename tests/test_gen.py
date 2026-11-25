"""The .pdsprj generator: byte-exact regression against the output of the original (pre-refactor) code, router and API checks."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from proteus2py import gen
from proteus2py._gen import cdbfmt, pdsfmt

D = Path(__file__).parent / "data"


def _hashes(path):
    z = zipfile.ZipFile(path)
    return {n: hashlib.sha256(z.read(n)).hexdigest()[:12] for n in z.namelist()}


def test_explicit_routes_are_byte_identical_to_the_original_generator(tmp_path):
    d = json.loads((D / "design_ref.json").read_text())
    out = tmp_path / "x.pdsprj"
    r = gen.build(out, d["parts"], d["wires"])
    assert r.parts == 8 and r.wires == 11
    assert _hashes(out)["ROOT.DSN"] == "4f759e93a997" and _hashes(out)["ROOT.CDB"] == "26f1ca400b79"
    objs, end, probs = pdsfmt.parse_objects(zipfile.ZipFile(out).read("ROOT.DSN"))
    assert not [p for p in probs if "bad object" in p or "desync" in p]


def test_auto_router_removes_clashes_between_fan_out_wires(tmp_path):
    d = json.loads((D / "design_ref.json").read_text())
    plain = [{k: v for k, v in w.items() if k != "via"} for w in d["wires"]]        # let the router choose the channels
    r = gen.build(tmp_path / "y.pdsprj", d["parts"], plain)
    assert r.conflicts == []


def test_unknown_kind_and_pin_are_reported(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        gen.build(tmp_path / "z.pdsprj", [{"id": "X", "kind": "NOPE", "x": 0, "y": 0}], [])
    parts = [{"id": "S", "kind": "LS", "x": 0, "y": 0}, {"id": "U", "kind": "6116", "x": 1000, "y": 0}]
    with pytest.raises(ValueError, match="no pin"):
        gen.build(tmp_path / "z.pdsprj", parts, [{"a": "S.Q", "b": "U.NOPE"}])


def test_inspect_roundtrip(tmp_path):
    parts = [{"id": "S1", "kind": "LS", "x": -5600, "y": 900, "state": 1}, {"id": "U1", "kind": "6116", "x": -3400, "y": 1000}]
    gen.build(tmp_path / "w.pdsprj", parts, [{"a": "S1.Q", "b": "U1.A0"}])
    info = gen.inspect(tmp_path / "w.pdsprj")
    devs = {p["ref"]: p for p in info["parts"]}
    assert devs["S1"]["device"] == "LOGICSTATE" and "{STATE=1}" in devs["S1"]["props"] and devs["U1"]["device"] == "6116"
    cdbfmt.parse_cdb(zipfile.ZipFile(tmp_path / "w.pdsprj").read("ROOT.CDB"))
