"""dsn_nets: nets of generated designs match the wires specified; opt-in comparison with ISIS's own SDF netlists on the Labcenter samples."""
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

from proteus2py import dsn_nets, gen
from proteus2py.dsn import parse_dsn, read_pdsprj

D = Path(__file__).parent / "data"


def _nets(path):
    objs = read_pdsprj(path).objects
    n = dsn_nets.build_nets(objs)
    pins = dsn_nets.named_pins(n, objs, zipfile.ZipFile(path).read("ROOT.CDB"))
    groups = {}
    for ref, pin, net in pins:
        groups.setdefault(net, set()).add((ref, pin))
    return n, {frozenset(g) for g in groups.values()}


def test_generated_design_nets_match_the_specified_wires(tmp_path):
    d = json.loads((D / "design_ref.json").read_text())
    gen.build(tmp_path / "a.pdsprj", d["parts"], d["wires"])
    _, got = _nets(tmp_path / "a.pdsprj")
    want_a0 = frozenset({("S1", "Q0"), ("U1", "A0"), ("U1", "A3"), ("U1", "A4"), ("U1", "A5"), ("U1", "A6")})
    assert want_a0 in got                                                  # fan-out through junction objects
    assert frozenset({("S2", "Q0"), ("U1", "A1")}) in got and frozenset({("S3", "Q0"), ("U1", "A2")}) in got
    assert frozenset({("U1", "D0"), ("D1", "NO1"), ("U2", "D0")}) in got   # a net spanning two wires through one pin tip


def test_two_wires_one_source_make_one_net_and_separate_sources_stay_apart(tmp_path):
    parts = [{"id": "S1", "kind": "LS", "x": -5600, "y": 900}, {"id": "S2", "kind": "LS", "x": -5600, "y": 300},
             {"id": "U1", "kind": "6116", "x": -3400, "y": 1000}, {"id": "U2", "kind": "6116", "x": -3400, "y": -500}]
    wires = [{"a": "S1.Q", "b": "U1.A0"}, {"a": "S1.Q", "b": "U2.A0"}, {"a": "S2.Q", "b": "U1.A1"}]
    gen.build(tmp_path / "b.pdsprj", parts, wires)
    n, got = _nets(tmp_path / "b.pdsprj")
    assert got == {frozenset({("S1", "Q0"), ("U1", "A0"), ("U2", "A0")}), frozenset({("S2", "Q0"), ("U1", "A1")})}
    assert len(n.nets) == 2 and all(isinstance(p, tuple) for pins in n.nets.values() for p in pins)


def _synthetic():
    comp = lambda i, key: {"kind": "COMPONENT", "serial": i, "id": {"text": f"R{i}"}, "slots": [key, key + 1]}
    return [
        comp(1, 10), comp(2, 20), comp(3, 30),
        {"kind": "TERMINAL", "name": "$TERGROUND", "ttype": 13, "label": {"text": ""}, "slots": [1]},
        {"kind": "TERMINAL", "name": "$TERGROUND", "ttype": 13, "label": {"text": ""}, "slots": [2]},
        {"kind": "TERMINAL", "name": "$TERPOWER", "ttype": 13, "label": {"text": ""}, "slots": [3]},
        {"kind": "TERMINAL", "name": "$TERPOWER", "ttype": 13, "label": {"text": "VCC"}, "slots": [4]},
        {"kind": "JUNCTION", "slots": [1, 2, 3, 0]},
    ]


def test_ground_power_labels_and_junctions_with_synthetic_objects():
    o = _synthetic()
    w = lambda a, b, *labels: {"kind": "WIRE", "bus": 0, "a": a, "b": b, "labels": [{"text": t} for t in labels]}
    o += [w((0, 0), (3, 0)), w((1, 0), (4, 0)),                       # R1.1 and R2.1 on two separate ground symbols -> one GND net
          w((2, 0), (5, 0)), w((0, 1), (6, 0)),                       # R3.1 on an unnamed power terminal, R1.2 on VCC -> one net
          w((1, 1), (7, 0)), w((2, 1), (7, 1), "SIG"),                # R2.2 - junction - R3.2, labelled
          w((0, 1), (7, 2))]                                          # a last wire ties R1.2 (VCC) to the junction
    n = dsn_nets.build_nets(o)
    assert n.net_of((0, 0)) == n.net_of((1, 0)) and n.name(n.net_of((0, 0))) == "GND"
    assert n.net_of((2, 0)) == n.net_of((0, 1)) and "VCC" in n.names[n.net_of((2, 0))]
    assert n.net_of((1, 1)) == n.net_of((2, 1)) == n.net_of((0, 1))     # the last wire merged the junction net with VCC
    assert n.net_of((1, 1)) != n.net_of((0, 0))


def test_same_named_wire_labels_merge_and_bus_wires_are_ignored():
    comp = lambda i: {"kind": "COMPONENT", "serial": i, "id": {"text": f"U{i}"}, "slots": [i * 10, i * 10 + 1]}
    o = [comp(1), comp(2), comp(3),
         {"kind": "WIRE", "bus": 0, "a": (0, 0), "b": (0, 1), "labels": [{"text": "D0"}]},
         {"kind": "WIRE", "bus": 0, "a": (1, 0), "b": (1, 1), "labels": [{"text": "D0"}]},
         {"kind": "WIRE", "bus": 1, "a": (2, 0), "b": (0, 0), "labels": []}]
    n = dsn_nets.build_nets(o)
    assert n.net_of((0, 0)) == n.net_of((1, 1)) and n.names[n.net_of((0, 0))] == ["D0"]
    assert n.net_of((2, 0)) is None


def test_probe_slots_join_except_current_probes():
    comp = lambda i: {"kind": "COMPONENT", "serial": i, "id": {"text": f"R{i}"}, "slots": [i * 10, i * 10 + 1]}
    for dev, joined in (("$VPROBE", True), ("$IPROBE", False)):
        o = [comp(1), comp(2), {"kind": "PROBE", "device": dev, "slots": [5, 6]},
             {"kind": "WIRE", "bus": 0, "a": (0, 0), "b": (2, 0), "labels": []}, {"kind": "WIRE", "bus": 0, "a": (1, 0), "b": (2, 1), "labels": []}]
        n = dsn_nets.build_nets(o)
        assert (n.net_of((0, 0)) == n.net_of((1, 0))) is joined


_LAB = "drive_c/Program Files (x86)/Labcenter Electronics/Proteus 8 Professional/DATA/SAMPLES/Graph Based Simulation"
SAMPLES = Path(os.environ.get("PROTEUS2PY_HOME", Path(__file__).resolve().parents[1])) / "sandbox/prefix-pristine" / _LAB
CAPS = Path(__file__).resolve().parents[1] / "re/captures"
# projects whose ISIS netlist equals ours on the top-level parts (hierarchical Lpf, Mutual1, Plltype2, Zin, 741noise are not compared)
SAMPLE_NAMES = ["Rescap", "Ff", "Diode", "Transfer", "Sweep", "Pots", "UJTcurve", "Rsff", "Switches", "Triangle", "Poweramp", "Resistor"]


def _sdf_partition(path):
    text = path.read_text(errors="replace").split("*NETLIST")[1]
    out = []
    for blk in re.split(r"\n\s*\n", text.split("\n", 1)[1]):
        ls = [x.strip() for x in blk.strip().splitlines()]
        pins = {(p[0], p[2]) for p in (x.split(",", 2) for x in ls[1:]) if len(p) == 3 and p[1] in ("PS", "IP", "OP", "MOD", "BI") and p[0].isascii() and not p[0].startswith(("P2C", "G2C"))}
        if pins:
            out.append(pins)
    return out


@pytest.mark.skipif(not SAMPLES.is_dir() or not CAPS.is_dir(), reason="needs the Labcenter samples in the sandbox and re/captures")
@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_sample_nets_equal_isis_netlist(name):
    caps = sorted((CAPS / f"Graph_Based_Simulation_{name}").glob("LISA*.SDF"))
    if not caps:
        pytest.skip("no SDF capture")
    z = zipfile.ZipFile(SAMPLES / f"{name}.pdsprj")
    objs = parse_dsn(z.read("ROOT.DSN")).objects
    n = dsn_nets.build_nets(objs)
    info = dsn_nets.read_cdb_pins(z.read("ROOT.CDB"))
    comps = {dsn_nets.pins_of(o, info)[0]: dsn_nets.pins_of(o, info)[1] for o in objs if o["kind"] == "COMPONENT"}

    def canon(ref, tok):                                  # pin token of the SDF (name or number) -> slot index
        pl = comps[ref]
        for i, (a, b) in enumerate(pl):
            if tok in (a, b):
                return ref, i
        return ref, int(tok) - 1 if tok.isdigit() else tok

    sdf = _sdf_partition(caps[0])
    refs = {r for g in sdf for r, _ in g if r in comps}
    want = {frozenset(canon(r, t) for r, t in g if r in refs) for g in sdf}
    got = {frozenset((dsn_nets.pins_of(objs[oi], info)[0], si) for oi, si in pins if dsn_nets.pins_of(objs[oi], info)[0] in refs) for pins in n.nets.values()}
    assert {g for g in want if len(g) > 1} == {g for g in got if len(g) > 1}
