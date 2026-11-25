"""Schematic component -> circuit-spec part mapping (offline: hand-built inputs; the sample-driven checks need the Proteus install and skip otherwise)."""
import struct
import zipfile

import pytest

from proteus2py import dsn_devices as dd
from proteus2py import workspace as ws
from proteus2py._gen import cdbfmt
from proteus2py.models import Part


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No device libraries / model descriptions unless a test provides them."""
    monkeypatch.setattr(dd, "lib_component", lambda device: {})
    monkeypatch.setattr(dd, "_resolves", lambda name: False)
    monkeypatch.setattr(dd, "mdf_text", lambda name: None)


def one(ref, device, value, props, pins, **kw):
    out = dd.to_spec_parts(ref, device, value, props, pins, **kw)
    for p in out:
        Part.model_validate(p)                        # every result is a valid circuit-spec part
    return out


# ---------------------------------------------------------------------------------------------------------------- numbers / properties

@pytest.mark.parametrize("text,want", [
    ("1k", 1000), ("4k7", 4700), ("1m", 1e-3), ("1M", 1e6), ("1MEG", 1e6), ("1meg", 1e6), ("100u", 1e-4), ("10pF", 1e-11), ("3.3uH", 3.3e-6),
    ("1MHz", 1e6), ("5V", 5), ("24Ohms", 24), ("2R2", 2.2), ("1e-3", 1e-3), ("-5", -5), ("220*1.41", 310.2), ("0.5*1k", 500), ("(1-0.849)*1k", 151),
    ("EVAL(500/3.142)", 500 / 3.142), ("E12(1/(2*PI*33k*10k))", 470e-12), ("SQRT(4)*2", 4)])
def test_isis_number(text, want):
    assert dd.isis_number(text) == pytest.approx(want, rel=1e-9)


@pytest.mark.parametrize("text", ["", "abc", "10*X", "__import__('os')", "9**9**9", "1k+"])
def test_isis_number_rejects(text):
    assert dd.isis_number(text) is None


def test_parse_props_braced_and_bare():
    p = dd.parse_props("{PRIMITIVE=ANALOGUE,NPN}\n{MODEL=LX_NPN_SSLF,BIPOLAR}\n{PACKAGE=TO92}\nVON=5\nVOFF=4\n{R(0)=100M}\n{package = X}\n")
    assert p["PRIMITIVE"] == "ANALOGUE,NPN" and p["MODEL"] == "LX_NPN_SSLF,BIPOLAR" and p["VON"] == "5" and p["R(0)"] == "100M" and p["PACKAGE"] == "TO92"
    assert dd.parse_props({"a": 1}) == {"A": "1"} and dd.parse_props(None) == {}


# ---------------------------------------------------------------------------------------------------------------- passives and sources

def test_resistor_by_primitive_primtype_and_name():
    pins = {"1": "a", "2": "b"}
    for dev, props in (("RES", "{PRIMITIVE=ANALOG}{PRIMTYPE=RESISTOR}{PACKAGE=RES40}"), ("RES", "{PRIMITIVE=PASSIVE,RESISTOR}"), ("RESISTOR", "{PRIMITIVE=ANALOGUE}"),
                       ("RES", "{PRIMITIVE=DIGITAL}{PRIMTYPE=RESISTOR}"), ("PULLUP", "{PRIMITIVE=DIGITAL,RESISTOR}"), ("RES", "")):
        assert one("R1", dev, "4k7", props, pins) == [{"ref": "R1", "kind": "R", "nodes": ["a", "b"], "value": 4700}]


def test_capacitor_inductor_and_polarised():
    assert one("C1", "CAP", "10p", "{PRIMITIVE=ANALOGUE,CAPACITOR}", {"1": "a", "2": "b"})[0] == {"ref": "C1", "kind": "C", "nodes": ["a", "b"], "value": 1e-11}
    assert one("C2", "CAP-ELEC", "1000uF", "{PRIMITIVE=ANALOGUE,CAPACITOR}", {"+": "a", "-": "b"})[0]["value"] == 1e-3
    assert one("C3", "CAP ELEC", "1u", "", {"+": "a", "-": "b"})[0]["kind"] == "C"
    assert one("L1", "INDUCTOR", "5mH", "{PRIMITIVE=ANALOGUE}", {"1": "a", "2": "b"})[0] == {"ref": "L1", "kind": "L", "nodes": ["a", "b"], "value": 5e-3}


def test_value_expressions_and_variables():
    assert one("R1", "RES", "EVAL(500/3.142)", "", {"1": "a", "2": "b"})[0]["value"] == pytest.approx(159.134, abs=1e-3)
    assert one("R1", "RESISTOR", "<LOAD>", "", {"1": "a", "2": "b"}, variables={"load": "100"})[0]["value"] == 100
    with pytest.raises(dd.UnsupportedDevice, match="unresolved <LOAD>"):
        one("R1", "RESISTOR", "<LOAD>", "", {"1": "a", "2": "b"})
    with pytest.raises(dd.UnsupportedDevice, match="not a number"):
        one("R1", "RESISTOR", "10*X", "", {"1": "a", "2": "b"})


def test_unconnected_pin_gets_private_net():
    r = one("R9", "RES", "1k", "", {"1": "a"})[0]
    assert r["nodes"][0] == "a" and r["nodes"][1] == "NC_R9_2" and r["nodes"][1] != "NC_R8_2"


def test_battery_and_dc_sources():
    assert one("B1", "BATTERY", "12V", "{PRIMITIVE=ANALOGUE}{RINT=0.1}", {"+": "vcc", "-": "0"}) == [{"ref": "B1", "kind": "V", "nodes": ["vcc", "0"], "value": 12}]
    assert one("B2", "CELL", "3V", "{PRIMITIVE=ANALOGUE,BATTERY}", {"+": "a", "-": "b"})[0]["value"] == 3
    assert one("V1", "VSOURCE", "5", "", {"+": "a", "-": "b"})[0] == {"ref": "V1", "kind": "V", "nodes": ["a", "b"], "value": 5}


def test_current_source_orientation_matches_elements_translation():
    dc = one("I1", "ISOURCE", "1m", "", {"+": "p", "-": "m"})[0]
    assert dc["kind"] == "I" and dc["nodes"] == ["m", "p"] and dc["value"] == 1e-3               # DC ISOURCE: ISIS pushes into '+', spec nodes are SPICE order
    ac = one("I2", "ISINE", "", "{VO=0}{VA=1m}{FREQ=1k}", {"+": "p", "-": "m"})[0]
    assert ac["nodes"] == ["p", "m"] and ac["wave"]["type"] == "sine"                               # waveform sources: ISIS '+' = n+


def test_waveform_sources():
    s = one("MAINS", "VSINE", "VSINE", "{VO=0}{VA=220*1.41}{FREQ=50}{PRIMITIVE=ANALOGUE}", {"+": "a", "-": "0"})[0]
    assert s["wave"] == {"type": "sine", "offset": 0, "amp": 310.2, "freq": 50} and "value" not in s
    p = one("V2", "VPULSE", "", "{V1=0}{V2=5}{TD=1m}{TR=1u}{TF=1u}{PW=0.5m}{PER=1m}", {"+": "a", "-": "0"})[0]
    assert p["wave"] == {"type": "pulse", "low": 0, "high": 5, "delay": 1e-3, "rise": 1e-6, "fall": 1e-6, "width": 5e-4, "period": 1e-3}
    e = one("V3", "VEXP", "", "{V1=0}{V2=1}{TD1=0}{TAU1=1m}{TD2=5m}{TAU2=2m}", {"+": "a", "-": "0"})[0]
    assert e["wave"]["type"] == "exp" and e["wave"]["tau2"] == 2e-3
    f = one("V4", "VSFFM", "", "{VO=0}{VA=1}{FC=10k}{FS=1k}{MDI=5}", {"+": "a", "-": "0"})[0]
    assert f["wave"] == {"type": "sffm", "offset": 0, "amp": 1, "carrier": 1e4, "signal": 1e3, "index": 5}
    w = one("V5", "VPWLIN", "", "{V(0)=0}{V(1m)=5}{V(2m)=0}", {"+": "a", "-": "0"})[0]
    assert w["wave"] == {"type": "pwl", "points": [[0, 0], [1e-3, 5], [2e-3, 0]]}


# ---------------------------------------------------------------------------------------------------------------- pots and switches

def test_pot_split_by_wiper_matches_isis_pots_sample():
    # Pots.pdsprj: RV1 at 50 % -> 0.5*1k / 0.5*1k; RV2 at 84.9 % -> (1-0.849)*1k between pin 1 and the wiper, 0.849*1k between wiper and pin 2
    props = "{PRIMITIVE=ANALOG,POT}{MODDLL=HGPOT.DLL}{LAW=0}{RMIN=0.1}{POS=84.9}{STATE=5}{DCPATH=*}"
    parts = one("RV2", "POT-HG", "1k", props, {"1": "gnd", "2": "in", "3": "w"})
    assert parts == [{"ref": "RV2_R1", "kind": "R", "nodes": ["gnd", "w"], "value": 151}, {"ref": "RV2_R2", "kind": "R", "nodes": ["w", "in"], "value": 849}]
    mid = one("RV1", "POT-HG", "1k", "{PRIMITIVE=ANALOG,POT}{STATE=5}", {"1": "a", "2": "b", "3": "w"})
    assert [p["value"] for p in mid] == [500, 500]
    end = one("RV3", "POT-HG", "1k", "{PRIMITIVE=ANALOG,POT}{POS=0}{RMIN=0.1}", {"1": "a", "2": "b", "3": "w"})
    assert [p["value"] for p in end] == [1000, 0.1]                                                  # RMIN keeps a section from being 0 ohm
    with pytest.raises(dd.DeviceError, match="expands to 2"):
        dd.to_spec_part("RV3", "POT-HG", "1k", "{PRIMITIVE=ANALOG,POT}", {"1": "a", "2": "b", "3": "w"})
    with pytest.raises(dd.UnsupportedDevice, match="law"):
        one("RV4", "POT-HG", "1k", "{PRIMITIVE=ANALOG,POT}{LAW=1}", {"1": "a", "2": "b", "3": "w"})


def test_runtime_switch_is_a_resistor_by_state():
    props = "{STATE=1}{PRIMITIVE=PASSIVE,RTSWITCH}{R(0)=100M}{R(1)=100m}{TSWITCH=1m}"
    assert one("BTN", "BUTTON", "", props, {"1": "a", "2": "b"}) == [{"ref": "BTN", "kind": "R", "nodes": ["a", "b"], "value": 0.1}]
    assert one("BTN", "BUTTON", "", props.replace("STATE=1", "STATE=0"), {"1": "a", "2": "b"})[0]["value"] == 1e8
    sw = one("SW1", "SW-SPST", "SW-SPST", "{STATE=0}{R(0)=100M}{R(1)=0.01}{PRIMITIVE=PASSIVE,RTSWITCH}", {"COM": "c", "NO": "n"})[0]
    assert sw["nodes"] == ["c", "n"]
    with pytest.raises(dd.DeviceError, match=r"R\(3\)"):
        one("SW2", "SW-SPST", "", "{STATE=3}{PRIMITIVE=PASSIVE,RTSWITCH}{R(0)=1}", {"COM": "c", "NO": "n"})


# ---------------------------------------------------------------------------------------------------------------- semiconductors / subcircuits

def test_diode_models(monkeypatch):
    d = one("D1", "1N4148", "1N4148", "{PRIMITIVE=ANALOG,DIODE}{SPICEMODEL=1N4148}{SPICELIB=DIODESINC}", {"K": "k", "A": "a"})[0]
    assert d == {"ref": "D1", "kind": "D", "nodes": ["a", "k"], "model": "1N4148"}                  # pins by NAME: CDB order K, A does not matter
    assert one("D2", "DIODE", "DIODE", "{PRIMITIVE=ANALOGUE}", {"A": "a", "K": "k"})[0]["model"] == "DIODE"
    monkeypatch.setattr(dd, "_resolves", lambda name: name == "1N4007")
    assert one("D3", "1N4007", "1N4007", "{PRIMITIVE=ANALOGUE,DIODE}{MODEL=LX_D_POW,DIODE}", {"A": "a", "K": "k"})[0]["model"] == "1N4007"


def test_bjt_pin_order_and_models():
    q = one("Q1", "BC547", "BC547", "{MODEL=LX_NPN_SSLF,BIPOLAR}{PACKAGE=TO92}{PRIMITIVE=ANALOGUE,NPN}", {"B": "b", "C": "c", "E": "e"})[0]
    assert q["kind"] == "Q" and q["nodes"] == ["c", "b", "e"]                                       # spec order: C B E, whatever the CDB pin order
    p = one("Q2", "2N4403", "2N4403", "{SPICELIB=FAIRCHLD}{PRIMITIVE=ANALOGUE,PNP}{SPICEMODEL=2N4403}", {"B": "b", "C": "c", "E": "e"})[0]
    assert p["kind"] == "QP" and p["model"] == "2N4403"
    assert one("Q3", "NPN", "TIP31", "{PRIMITIVE=PASSIVE}{PACKAGE=TO220}", {"B": "b", "C": "c", "E": "e"})[0]["model"] == "TIP31"      # value names the model
    assert one("Q4", "NPN", "NPN", "{PRIMITIVE=ANALOGUE}", {"B": "b", "C": "c", "E": "e"})[0]["model"] == "NPN"                           # generic part -> builtin model


def test_fets():
    n = one("M1", "2N7002", "2N7002", "{PRIMITIVE=ANALOG,NMOSFET}{SPICEMODEL=2N7002}{L=2.5E-6}{W=0.5E-2}", {"D": "d", "G": "g", "S": "s"})[0]
    assert n == {"ref": "M1", "kind": "M", "nodes": ["d", "g", "s", "s"], "model": "2N7002", "params": {"l": 2.5e-6, "w": 5e-3}}      # bulk tied to the source
    p = one("M2", "PMOSFET", "PMOSFET", "{PRIMITIVE=ANALOGUE}", {"B": "b", "D": "d", "G": "g", "S": "s"})[0]
    assert p["kind"] == "MP" and p["nodes"] == ["d", "g", "s", "b"]
    j = one("J1", "2N3819", "2N3819", "{PRIMITIVE=ANALOG,NJFET}{SPICEMODEL=2N3819}", {"D": "d", "G": "g", "S": "s"})[0]
    assert j["kind"] == "J" and j["nodes"] == ["d", "g", "s"]
    assert one("J2", "2N5460", "2N5460", "{PRIMITIVE=ANALOG,PJFET}{SPICEMODEL=2N5460}", {"D": "d", "G": "g", "S": "s"})[0]["kind"] == "JP"


def test_opamp_subcircuit_uses_spicepins_order():
    props = "{SPICEMODEL=LM358/NS}{SPICELIB=NATOA}{SPICEPINS=+IP,-IP,V+,V-,OP}{PACKAGE=SO8}{PRIMITIVE=ANALOG,SUBCKT}"
    u = one("U1", "LM358", "LM358", props, {"+IP": "p", "-IP": "n", "OP": "o", "V+": "vp", "V-": "vn"})[0]       # CDB pin order is +IP -IP OP V+ V-
    assert u == {"ref": "U1", "kind": "X", "nodes": ["p", "n", "vp", "vn", "o"], "subckt": "LM358/NS"}
    # the spaces and case of the ISIS pin names do not matter, an unconnected supply pin becomes a private net
    u = one("U2", "LMC660", "LMC660", '{SPICEMODEL="LMC660,SPICEMOD.LIB"}{SPICEPINS="POS IP,NEG IP,V+,V-,OP"}', {"posip": "p", "NEG IP": "n", "OP": "o"})[0]
    assert u["subckt"] == "LMC660" and u["nodes"][:2] == ["p", "n"] and u["nodes"][4] == "o" and u["nodes"][2].startswith("NC_U2_")


# ---------------------------------------------------------------------------------------------------------------- digital

def test_gate_primitives():
    g = one("U5", "NOR_2", "NOR_2", "{PRIMITIVE=DIGITAL}", {"D0": "a", "Q": "q", "D1": "b"})[0]
    assert g == {"ref": "U5", "kind": "GATE", "nodes": ["a", "b", "q"], "gate": "NOR"}
    g = one("U19", "NAND_3", "NAND_3", "{PRIMITIVE=DIGITAL,NAND_3}{TDHLDQ=10n}", {"D0": "a", "D1": "b", "D2": "c", "Q": "q"})[0]
    assert g["nodes"] == ["a", "b", "c", "q"] and g["delays"] == {"TDHLDQ": 1e-8}
    n = one("U6", "NOT", "NOT", "{PRIMITIVE=DIGITAL,INVERTER}", {"Q": "y", "D": "a"})[0]
    assert n == {"ref": "U6", "kind": "GATE", "nodes": ["a", "y"], "gate": "NOT"}
    assert one("U7", "BUFFER", "BUFFER", "{PRIMITIVE=DIGITAL}", {"D": "a", "Q": "y"})[0]["gate"] == "BUF"


def test_digital_with_inverted_input_or_other_primitive_is_generic():
    d = one("U2", "AND_3", "AND_3", "{PRIMITIVE=DIGITAL} INVERT=D0", {"D0": "a", "D1": "b", "Q": "q", "D2": "c"})[0]
    assert d["kind"] == "DIGITAL" and d["primitive"] == "AND_3" and d["params"] == {"INVERT": "D0"}
    assert d["pins"] == ["D0:IP", "D1:IP", "Q:OP", "D2:IP"] and d["nodes"] == ["a", "b", "q", "c"]
    l4 = one("U4", "LATCH_4", "LATCH_4", "{PRIMITIVE=DIGITAL}", {"D0": "a", "Q0": "q"})[0]
    assert l4["kind"] == "DIGITAL" and l4["pins"] == ["D0:IP", "Q0:OP"]


def test_logicstate_is_a_constant_generator():
    assert one("S1", "LOGICSTATE", "", "{PRIMITIVE=DIGITAL,RTDSTATE}{STATE=1}", {"Q0": "n"}) == [
        {"ref": "S1", "kind": "DGEN", "nodes": ["n"], "pattern": "1", "init": "HIGH", "width": 1}]
    assert one("S2", "LOGICSTATE", "", "{PRIMITIVE=DIGITAL,RTDSTATE}{STATE=0}", {"Q0": "n"})[0]["init"] == "LOW"


# a tiny invented model description in the LISA MDF layout (what ISIS flattens into U1:A_U3 style SDF parts)
GATE_MDF = """LISA MODEL DESCRIPTION FORMAT 6.1
*PROPERTIES,1
TGQ=?

*MAPPINGS,2,VALUE
TG00 : TDLHDQ=9n, TDHLDQ=10n, SCHMITT=[NULL]
TG13 : TDLHDQ=15n, TDHLDQ=15n, SCHMITT="D0,D1"

*MODELDEFS,0

*PARTLIST,1
U3,NAND_2,NAND_2,PRIMITIVE=DIGITAL,SCHMITT=<SCHMITT>,TDHLDQ=<TDHLDQ>,TDLHDQ=<TDLHDQ>,TGQ=<TGQ>

*NETLIST,3
A,2
A,IT
U3,IP,D0

B,2
B,IT
U3,IP,D1

Y,2
Y,OT
U3,OP,Q

*GATES,0
"""

RC_MDF = """LISA MODEL DESCRIPTION FORMAT 8.0
*PROPERTIES,1
CS=0.05p

*PARTLIST,3
CP,CAPACITOR,140*<CS>,PRIMITIVE=ANALOGUE
L,INDUCTOR,1/(39.48*<CS>*<FREQ>*<FREQ>),PRIMITIVE=ANALOGUE
R,RES,<RS>,PRIMITIVE=PASSIVE,PRIMTYPE=RESISTOR

*NETLIST,4
#00002,2
L,PS,2
R,PS,1

1,3
1,GT
L,PS,1
CP,PS,1

2,3
2,GT
R,PS,2
CP,PS,2

*V+,1
*V+,GT
"""

POT_MDF = """*PROPERTIES,0

*PARTLIST,2
R1,RTSWITCH,,PRIMITIVE=ANALOG,R(0)=1m*<VALUE>,R(1)=0.1*<VALUE>,R(2)=0.2*<VALUE>,STATE=<STATE>
R2,RTSWITCH,,PRIMITIVE=ANALOG,R(0)=0.999*<VALUE>,R(1)=0.9*<VALUE>,R(2)=0.8*<VALUE>,STATE=<STATE>

*NETLIST,3
1,2
1,GT
R1,PS,1

3,3
3,GT
R1,PS,2
R2,PS,1

2,2
2,GT
R2,PS,2
"""


def test_model_description_gate_is_flattened_like_isis(monkeypatch):
    monkeypatch.setattr(dd, "mdf_text", lambda name: GATE_MDF if name.upper().startswith("TGATE") else None)
    out = one("U1:A", "TG00", "TG00", "{MODFILE=TGATE.MDF}{PACKAGE=DIL14}", {"A": "x", "B": "y", "Y": "z"})
    assert out == [{"ref": "U1:A_U3", "kind": "GATE", "nodes": ["x", "y", "z"], "gate": "NAND", "delays": {"TDHLDQ": 1e-8, "TDLHDQ": 9e-9}}]      # [NULL] / ? dropped
    schmitt = one("U1:B", "TG13", "TG13", "{MODFILE=TGATE}", {"A": "x", "B": "y", "Y": "z"})[0]
    assert schmitt["kind"] == "DIGITAL" and schmitt["params"] == {"SCHMITT": "D0,D1"} and schmitt["pins"] == ["D0:IP", "D1:IP", "Q:OP"]
    # value not in the mappings: the delay properties simply stay unset
    plain = one("U1:C", "TGXX", "TGXX", "{MODFILE=TGATE}", {"A": "x", "B": "y", "Y": "z"})[0]
    assert plain["kind"] == "GATE" and "delays" not in plain


def test_model_description_analogue_macro_with_internal_nets(monkeypatch):
    monkeypatch.setattr(dd, "mdf_text", lambda name: RC_MDF)
    out = one("X1", "CRYSTAL", "4MHz", "{MODFILE=CRYSTAL.MDF}{FREQ=1MHz}{RS=2}", {"1": "a", "2": "b"})
    by = {p["ref"]: p for p in out}
    assert set(by) == {"X1_CP", "X1_L", "X1_R"}
    assert by["X1_CP"]["value"] == pytest.approx(7e-12) and by["X1_L"]["value"] == pytest.approx(1 / (39.48 * 0.05e-12 * 1e12), rel=1e-6) and by["X1_R"]["value"] == 2
    assert by["X1_L"]["nodes"] == ["a", "X1.#00002"] and by["X1_R"]["nodes"] == ["X1.#00002", "b"]
    assert by["X1_CP"]["nodes"] == ["a", "b"]
    with pytest.raises(dd.UnsupportedDevice, match="needs a value"):
        one("X2", "CRYSTAL", "4MHz", "{MODFILE=CRYSTAL.MDF}{FREQ=1MHz}", {"1": "a", "2": "b"})      # RS is neither a property nor a default


def test_model_description_pot_by_state(monkeypatch):
    monkeypatch.setattr(dd, "mdf_text", lambda name: POT_MDF)
    out = one("RV1", "POT-LIN", "10k", "{MODFILE=POT_LIN}{STATE=2}", {"1": "a", "2": "b", "3": "w"})
    assert [(p["ref"], p["nodes"], p["value"]) for p in out] == [("RV1_R1", ["a", "w"], 2000), ("RV1_R2", ["w", "b"], 8000)]


def test_unknown_model_file_and_dll_models_are_unsupported():
    with pytest.raises(dd.UnsupportedDevice, match="not found"):
        one("U1", "LPC2138", "LPC2138", "{MODFILE=LPC213X}", {})
    with pytest.raises(dd.UnsupportedDevice, match="VSM model"):
        one("U2", "ATMEGA328P", "ATMEGA328P", "{PRIMITIVE=DIGITAL,ATMEGA328P}{MODDLL=AVR2.DLL}", {})
    assert dd.to_spec_parts("U2", "ATMEGA328P", "", "{PRIMITIVE=DIGITAL,ATMEGA328P}{MODDLL=AVR2.DLL}", {}, strict=False) == []
    with pytest.raises(dd.UnsupportedDevice, match="no mapping"):
        one("X9", "WHATEVER", "", "", {})


def test_controlled_sources_use_spice_orientation():
    g = one("G1", "VCCS", "0.5", "{PRIMITIVE=ANALOGUE}", {"+": "p", "-": "m", "P": "cp", "N": "cn"})[0]
    assert g == {"ref": "G1", "kind": "G", "nodes": ["m", "p", "cp", "cn"], "value": 0.5}
    e = one("E1", "VCVS", "0.005", "{PRIMITIVE=ANALOGUE}", {"+": "p", "-": "m", "P": "cp", "N": "cn"})[0]
    assert e["kind"] == "E" and e["nodes"] == ["p", "m", "cp", "cn"]


# ---------------------------------------------------------------------------------------------------------------- non-simulated things

@pytest.mark.parametrize("device,props", [
    ("CRYSTAL", "{PACKAGE=XTAL18}{PRIMITIVE=NULL}"), ("CONN-SIL6", "{PRIMITIVE=NULL}{PACKAGE=CONN-SIL6}"), ("SPEAKER", "{PRIMITIVE=ANALOG,RTVPROBE}{LOAD=8}"),
    ("SOUNDER", "{PRIMITIVE=DIGITAL,RTDPROBE}"), ("VUMETER", "{PRIMITIVE=ANALOG,VUMETER}"), ("VPROBE", ""), ("RES", "{PRIMITIVE=NULL}{CONNECT=1,2}")])
def test_non_simulated_parts_give_none(device, props):
    assert dd.to_spec_parts("X1", device, "", props, {"1": "a", "2": "b"}) == []
    assert dd.to_spec_part("X1", device, "", props, {"1": "a", "2": "b"}) is None


def test_connect_groups_and_jumpers_merge_nets():
    assert dd.connect_groups("{PRIMITIVE=NULL}{CONNECT=1A,1B;2A,2B}") == [["1A", "1B"], ["2A", "2B"]]
    parts = [("R1", "RES", "1k", ""), ("J1", "RES", "", "{PRIMITIVE=NULL}{CONNECT=1,2}"), ("R2", "RES", "2k", "")]
    nets = [("R1", "1", "in"), ("R1", "2", "mid"), ("J1", "1", "mid"), ("J1", "2", "0"), ("R2", "1", "mid"), ("R2", "2", "out")]
    res = dd.spec_from_parts(parts, nets)
    assert res.ignored == ["J1"] and not res.unsupported
    assert [p["nodes"] for p in res.parts] == [["in", "0"], ["0", "out"]]                         # 'mid' is tied to ground through the jumper


def test_spec_from_parts_collects_unsupported():
    res = dd.spec_from_parts([("R1", "RES", "1k", ""), ("U1", "ATMEGA16", "", "{MODDLL=AVR2.DLL}{PRIMITIVE=DIGITAL,ATMEGA16}"), ("X1", "CRYSTAL", "", "{PRIMITIVE=NULL}")],
                             {"R1": {"1": "a", "2": "b"}})
    assert [p["ref"] for p in res.parts] == ["R1"] and "U1" in res.unsupported and res.ignored == ["X1"]
    assert res.as_spec(probes=["v(a)"]) == {"parts": res.parts, "probes": ["v(a)"]}


# ---------------------------------------------------------------------------------------------------------------- CDB input

def test_spec_from_cdb_v5_roundtrip_with_multi_unit_refs():
    cdb = cdbfmt.build_cdb([
        (1, "R1", "RES", [("1", "1"), ("2", "2")], ["{PRIMITIVE=ANALOG}", "{PRIMTYPE=RESISTOR}", "{PACKAGE=RES40}"], {"value": "10k"}),
        (2, "Q1", "BC547", [("B", "2"), ("C", "1"), ("E", "3")], ["{MODEL=LX_NPN_SSLF,BIPOLAR}", "{PRIMITIVE=ANALOGUE,NPN}"], {"value": "BC547"}),
        (3, "U6:A", "74LS04", [("A", "1"), ("Y", "2")], ["{MODFILE=74INV.MDF}"], {"part_ref": "U6", "idx": 3, "value": "74LS04"}),
        (4, "", "", [("1", "")], [], {"part": False}),                                           # element without a part (a terminal)
    ])
    comps = dd.read_components(cdb)
    assert [c[0] for c in comps] == ["R1", "Q1", "U6:A"]
    res = dd.spec_from_cdb(cdb, [("R1", "1", "a"), ("R1", "2", "b"), ("Q1", "B", "b"), ("Q1", "C", "c"), ("Q1", "E", "0"), ("U6:A", "A", "a"), ("U6:A", "Y", "y")])
    kinds = {p["ref"]: p for p in res.parts}
    assert kinds["R1"]["value"] == 10000 and kinds["Q1"]["nodes"] == ["c", "b", "0"]
    assert "U6:A" in res.unsupported and "74INV" in res.unsupported["U6:A"]                      # no model library offline


def _v2_cdb() -> bytes:
    """Minimal version-2 ROOT.CDB: two elements (R1, a probe-like element without part) and the part table with R1 only (words = 2 x u32)."""
    def s8(s: str) -> bytes:
        return bytes([len(s)]) + s.encode("latin1")

    def element(serial: int, ref: str, pins: list[tuple[str, str]], part: int) -> bytes:
        return (struct.pack("<3I", serial, 1, 0) + s8(ref) + struct.pack("<I", len(pins)) + b"".join(s8(a) + s8(b) for a, b in pins)
                + struct.pack("<I", 0) + struct.pack("<Ii", part, -1))

    body = struct.pack("<I", 2) + element(1, "R1", [("1", "1"), ("2", "2")], 1) + element(2, "", [("1", "")], 0)
    props = b"{PRIMITIVE=PASSIVE}\n"
    part = struct.pack("<3I", 1, 1, 0) + s8("R1") + s8("4k7") + s8("RESISTOR") + s8("") + struct.pack("<I", len(props) + 5) + props + b"\0"
    return struct.pack("<II", 2, 1) + body + struct.pack("<4I", 1, 1, 1, 1) + part


def test_spec_from_cdb_v2_old_samples():
    res = dd.spec_from_cdb(_v2_cdb(), {"R1": {"1": "a", "2": "0"}})
    assert res.parts == [{"ref": "R1", "kind": "R", "nodes": ["a", "0"], "value": 4700}] and not res.unsupported


# ---------------------------------------------------------------------------------------------------------------- opt-in: the Proteus install

SAMPLES = ws.proteus_dir() / "DATA" / "SAMPLES" / "Graph Based Simulation"
needs_samples = pytest.mark.skipif(not (SAMPLES / "Pots.pdsprj").exists(), reason="Proteus sample projects not installed")


def _sample(name: str) -> bytes:
    return zipfile.ZipFile(SAMPLES / f"{name}.pdsprj").read("ROOT.CDB")


@needs_samples
def test_sample_pots_matches_isis_sdf(monkeypatch):
    monkeypatch.undo()                                                                                # real libraries for this test
    res = dd.spec_from_cdb(_sample("Pots"), {"RV1": {"1": "g", "2": "i", "3": "w1"}, "RV2": {"1": "g", "2": "i", "3": "w2"}})
    vals = {p["ref"]: p["value"] for p in res.parts}
    assert vals == {"RV1_R1": 500, "RV1_R2": 500, "RV2_R1": 151, "RV2_R2": 849}                       # LISA4111.SDF: 0.5*1k, 0.5*1k, (1-0.849)*1k, 0.849*1k


@needs_samples
def test_sample_rescap_and_lpf_values(monkeypatch):
    monkeypatch.undo()
    res = dd.spec_from_cdb(_sample("Rescap"), {"R1": {"1": "a", "2": "b"}, "C1": {"1": "b", "2": "0"}})
    assert {p["ref"]: p["value"] for p in res.parts} == {"R1": pytest.approx(159.134, abs=1e-3), "C1": 1e-6}                  # LISA3342.SDF: 159.134 and 1u
    lpf = dd.spec_from_cdb(_sample("Lpf"), {"R1": {"1": "a", "2": "b"}, "C1": {"1": "b", "2": "0"}}, variables={"IMP": "33k", "FREQ": "10k"})
    assert {p["ref"]: p["value"] for p in lpf.parts} == {"R1": 33000, "C1": pytest.approx(470e-12)}                       # LISA2475.SDF: 33k and 470p


@needs_samples
def test_sample_triangle_inverter_is_flattened_from_the_model_library(monkeypatch):
    monkeypatch.undo()
    if dd.mdf_text("74INV") is None:
        pytest.skip("DATA/MODELS/*.LML not readable")
    res = dd.spec_from_cdb(_sample("Triangle"), {"U1:A": {"A": "in", "Y": "out"}})
    inv = next(p for p in res.parts if p["ref"].startswith("U1:A"))
    assert inv["kind"] == "GATE" and inv["gate"] == "NOT" and inv["nodes"] == ["in", "out"] and inv["delays"] == {"TDHLDQ": 8e-9, "TDLHDQ": 12e-9}   # SDF: TDHLDQ=8n TDLHDQ=12n
