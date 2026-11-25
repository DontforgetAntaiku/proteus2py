"""Golden SDF netlists: the generated ProSPICE input for a set of circuits must not change unintentionally.
These tests need no Proteus (they run in CI); regenerate after an intended change with UPDATE_GOLDEN=1."""
import os
from pathlib import Path

import pytest

from proteus2py import circuit, spice

G = Path(__file__).parent / "golden"

DECKS = {
    "rc_ac": "rc\nV1 in 0 AC 1\nR1 in out 1k\nC1 out 0 159.155n\n.ac dec 10 10 1meg\n",
    "rc_step": "rc\nV1 in 0 PULSE(0 5 1m 1u 1u 100m 1)\nR1 in out 1k\nC1 out 0 1u\n.tran 5u 6m\n",
    "ce_amp": "ce\nVCC vcc 0 12\nVIN in 0 SIN(0 10m 1k)\nCIN in b 10u\nR1 vcc b 47k\nR2 b 0 10k\nRC vcc c 2.2k\nRE e 0 470\nQ1 c b e QN\n.model QN NPN(IS=5e-15 BF=250 BR=3 RC=30 RB=80 CJC=5p CJE=10p VAF=50)\n.op\n",
    "diode_sweep": "d\nV1 a 0 0\nR1 a k 100\nD1 k 0 DM\n.model DM D(IS=222p RS=68.6m N=1.7)\n.dc V1 0 5 0.5\n",
    "transformer": "t\nV1 s 0 SIN(0 1 1k)\nRS s in 1\nL1 in 0 10m\nL2 out 0 40m\nK1 L1 L2 0.999\nRL out 0 100k\n.tran 5u 5m\n",
    "sources": "s\nV1 a 0 1\nVS s 0 0\nR1 a s 1k\nF1 f 0 VS 2\nRF f 0 1k\nH1 h 0 VS 1000\nRH h 0 1k\nE1 e 0 a 0 5\nRE e 0 1k\nG1 g 0 a 0 1m\nRG g 0 1k\n.op\n",
}


@pytest.mark.parametrize("name", sorted(DECKS))
def test_generated_sdf_matches_golden(name, tmp_path, monkeypatch):
    monkeypatch.setenv("PROTEUS2PY_HOME", str(tmp_path))
    text = circuit.build(spice.parse(DECKS[name]), partition="GOLD0001").sdf.text()
    f = G / f"{name}.SDF"
    if os.environ.get("UPDATE_GOLDEN") == "1" or not f.exists():
        f.write_text(text)
    assert text == f.read_text()
