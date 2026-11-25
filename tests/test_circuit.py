import pytest

from proteus2py import circuit
from proteus2py.sdf import Sdf


def test_sdf_text_roundtrip_sections():
    s = Sdf(partition="ABCD0001")
    s.add_part("R1", "RES", "1k", ["PRIMITIVE=PASSIVE"], [("a", "PS", "1"), ("0", "PS", "2")])
    t = s.text()
    assert "*PARTLIST,1" in t and "R1,RES,1k,PRIMITIVE=PASSIVE" in t and "GND,GT" in t and "PARTITION=ABCD0001" in t


def test_build_divider_and_probes():
    b = circuit.build({"parts": [{"ref": "V1", "kind": "V", "nodes": ["in", "0"], "value": 10},
                                 {"ref": "R1", "kind": "R", "nodes": ["in", "out"], "value": "1k"},
                                 {"ref": "R2", "kind": "R", "nodes": ["out", "0"], "value": "2k"}],
                       "probes": ["v(out)", "i(R1)"]})
    t = b.sdf.text()
    assert "SIMTYPE=TRANSIENT" in t and "APROBE,v(out)" in t and "IPROBE,i(R1)" in t
    assert b.analysis == "op"


def test_current_source_pins_follow_spice_direction():
    b = circuit.build({"parts": [{"ref": "I1", "kind": "I", "nodes": ["a", "b"], "value": "1m"},
                                 {"ref": "R1", "kind": "R", "nodes": ["b", "0"], "value": 1000},
                                 {"ref": "R2", "kind": "R", "nodes": ["a", "0"], "value": 1000}]})
    pins = {(x[0], x[2]): net for net, lst in b.sdf.nets.items() for x in lst}
    assert pins[("I1", "+")] == "b" and pins[("I1", "-")] == "a"     # ISIS pushes current into '+', SPICE into n-


@pytest.mark.parametrize("bad,msg", [
    ({"parts": []}, "no parts"),
    ({"parts": [{"ref": "X", "kind": "Z", "nodes": []}]}, "unknown kind"),
    ({"parts": [{"ref": "D1", "kind": "D", "nodes": ["a", "0"], "model": "NOPE_NOT_A_MODEL"}]}, "not found"),
    ({"parts": [{"ref": "R1", "kind": "R", "nodes": ["a"], "value": 1}]}, "2 nodes"),
    ({"parts": [{"ref": "V1", "kind": "V", "nodes": ["a", "0"], "wave": {"type": "sine", "ampl": 1}}]}, "unknown keys"),
])
def test_spec_errors(bad, msg):
    with pytest.raises(circuit.SpecError, match=msg):
        circuit.build(bad)


def test_wave_names_map_to_isis_properties():
    _, props = circuit._wave_props({"type": "pulse", "low": 0, "high": 5, "delay": "1m", "rise": "1u", "width": "2m", "freq": 100})
    assert props == ["LOW=0", "HIGH=5", "TD=1m", "TR=1u", "PW=2m", "FREQ=100"]


def test_model_params_are_converted_from_spice_notation():
    mt, mp = circuit._resolve_model({"models": {"D": {"type": "D", "params": {"RS": "68.6M", "IS": "222P"}}}}, "D")
    assert mp["RS"] == pytest.approx(0.0686) and mp["IS"] == pytest.approx(2.22e-10)


def test_k_groups_become_one_subcircuit():
    spec = circuit._couple({"parts": [{"ref": "L1", "kind": "L", "nodes": ["a", "0"], "value": "10m"},
                                      {"ref": "L2", "kind": "L", "nodes": ["b", "0"], "value": "40m"},
                                      {"ref": "K1", "kind": "K", "inductors": ["L1", "L2"], "value": 0.99},
                                      {"ref": "R1", "kind": "R", "nodes": ["a", "b"], "value": 1}]})
    assert [p["ref"] for p in spec["parts"]] == ["R1", "XKGRP1"]
    t = spec["subckts"]["KGRP1"]
    assert ".SUBCKT KGRP1 1 2 3 4" in t and "LL1 1 2" in t and "KK1 LL1 LL2 0.99" in t


def test_library_devices_resolve_to_generic_models():
    pytest.importorskip("PIL")
    from proteus2py import library
    if not library.models_dir().is_dir():
        pytest.skip("no Proteus prefix in this environment")
    typ, params = library.resolve("BC547")
    assert typ == "NPN" and "BETAF" in params
