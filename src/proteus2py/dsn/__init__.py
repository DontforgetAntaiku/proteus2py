"""Generic reader for the ISIS object stream (ROOT.DSN) of Proteus 8 projects: components, wires, junctions, terminals, probes/generators,
graphs. Decoded from ISIS.DLL (Ghidra) and validated on the whole bundled sample corpus."""
from .reader import DsnResult, parse_dsn, read_pdsprj

__all__ = ["DsnResult", "parse_dsn", "read_pdsprj"]
