"""Number parsing for circuit specs (ISIS notation: m = milli, M = mega)."""
from __future__ import annotations

import re

from .errors import SpecError

_MULT = {"": 1.0, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "meg": 1e6, "G": 1e9, "g": 1e9, "T": 1e12}


def to_float(x: float | int | str | None) -> float:
    """1e-3 | 5 | "4.7k" | "10m" -> float (ISIS suffixes: m milli, M or meg mega)."""
    if isinstance(x, (int, float)):
        return float(x)
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*", str(x))
    if not m or m.group(2) not in _MULT:
        raise SpecError(f"bad number {x!r}")
    return float(m.group(1)) * _MULT[m.group(2)]
