"""Simulate a SPICE deck and measure it. Needs: proteus2py setup <wine prefix with Proteus 8>."""
from pathlib import Path

from proteus2py import sim, spice

here = Path(__file__).parent
spec = spice.parse((here / "rc_lowpass.cir").read_text())
r = sim.simulate(spec, "rc")
print("-3 dB at", sim.measure_run("rc", "bandwidth", "v(out)")["f_high_3db"], "Hz")
print(sim.derive("rc", "db(v(out))", 6)["rows"])

# parameter sweep and Monte Carlo on the same circuit
metric = {"what": "bandwidth", "probe": "v(out)", "field": "f_high_3db"}
print(sim.sweep(spec, "R1", ["500", "1k", "2k"], metric)["rows"])
print({k: v for k, v in sim.monte_carlo(spec, {"R1": "5%", "C1": "10%"}, metric, runs=10).items() if k != "runs"})
