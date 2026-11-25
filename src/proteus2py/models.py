"""Typed schemas: circuit specification (input, strictly validated) and simulation results (output, documented to MCP clients)."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Number = float | int | str            # 1e-3, 5, "4.7k" (ISIS notation: m = milli, M = mega)
KINDS = ("R", "C", "L", "V", "I", "D", "Q", "QP", "J", "JP", "M", "MP", "E", "G", "F", "H", "K", "X", "OPAMP")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Wave(_Strict):
    """Time-dependent source. sine: amp offset freq delay phase damping | pulse: low high delay rise fall width freq|period |
    exp: low high td1 tau1 td2 tau2 | sffm: amp offset carrier index signal | pwl: points [[t, v], ...]."""
    model_config = ConfigDict(extra="allow")
    type: Literal["sine", "pulse", "exp", "sffm", "pwl"]
    points: list[list[Number]] | None = None


class Part(_Strict):
    ref: str = Field(min_length=1)
    kind: str
    nodes: list[str] = Field(default_factory=list)
    value: Number | None = None
    model: str | None = None
    wave: Wave | None = None
    ac: bool = False
    acmag: Number = 1
    ic: Number | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    control: str | None = None              # F/H: ref of the V source whose current controls the part
    inductors: list[str] | None = None      # K: the two coupled inductors
    subckt: str | None = None               # X: subcircuit name
    gain: Number | None = None              # OPAMP
    rout: Number | None = None              # OPAMP

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        v = v.upper()
        if v not in KINDS:
            raise ValueError(f"unknown kind {v!r}; one of {', '.join(KINDS)}")
        return v

    @field_validator("nodes", mode="before")
    @classmethod
    def _nodes(cls, v: Any) -> Any:
        return [str(x) for x in v] if isinstance(v, (list, tuple)) else v


class OpAnalysis(_Strict):
    type: Literal["op"] = "op"


class TransientAnalysis(_Strict):
    type: Literal["transient"] = "transient"
    tstop: Number
    tstart: Number = 0
    tmax: Number | None = None


class AcAnalysis(_Strict):
    type: Literal["ac"] = "ac"
    fstart: Number
    fstop: Number
    points: int | float = 10
    mode: Literal["dec", "oct", "lin"] = "dec"


class DcAnalysis(_Strict):
    type: Literal["dc"] = "dc"
    source: str
    start: Number
    stop: Number
    step: Number
    source2: str | None = None
    start2: Number | None = None
    stop2: Number | None = None
    step2: Number | None = None


class NoiseAnalysis(_Strict):
    type: Literal["noise"] = "noise"
    output: str
    input: str | None = None
    fstart: Number
    fstop: Number
    points: int | float = 10
    mode: Literal["dec", "oct", "lin"] = "dec"


Analysis = Annotated[OpAnalysis | TransientAnalysis | AcAnalysis | DcAnalysis | NoiseAnalysis, Field(discriminator="type")]


class ModelDef(_Strict):
    type: str
    params: dict[str, Any] | str = Field(default_factory=dict)


class CircuitSpec(_Strict):
    title: str = "proteus2py"
    parts: list[Part]
    models: dict[str, ModelDef] = Field(default_factory=dict)
    analysis: Analysis = Field(default_factory=OpAnalysis)
    analyses: list[dict[str, Any]] = Field(default_factory=list)     # extra analysis cards from a SPICE deck (informational)
    probes: list[str] | None = None
    temp: Number = 27
    options: dict[str, Any] = Field(default_factory=dict)
    options_raw: dict[str, Any] = Field(default_factory=dict)
    subckts: dict[str, str] = Field(default_factory=dict)
    spice_text: str = ""

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, d: Any) -> Any:
        if isinstance(d, dict) and isinstance(d.get("analysis"), dict) and d["analysis"].get("type") == "tran":
            d = {**d, "analysis": {**d["analysis"], "type": "transient"}}
        return d


# ---- results ------------------------------------------------------------------------------------------------------
class Table(BaseModel):
    columns: list[str]
    rows: list[list[float]]


class Stats(BaseModel):
    min: float
    max: float
    pkpk: float
    first: float
    last: float
    mean: float | None = None
    rms: float | None = None


class GainSummary(BaseModel):
    db_at_start: float
    peak_db: float | None = None
    peak_f: float | None = None
    f_high_3db: float | None = None


class NoiseSummary(BaseModel):
    output_V_per_rtHz: dict[str, float]
    total_output_rms_V: float
    input_referred_V_per_rtHz: dict[str, float]
    total_input_rms_V: float


class Curve(BaseModel):
    outer: float
    table: Table


class SimResult(BaseModel):
    """Answer of simulate / simulate_spice / run_sdf. Exactly one of op / stats / gain / noise is filled, depending on the analysis."""
    name: str
    analysis: Literal["op", "transient", "ac", "dc", "noise"]
    probes: list[str]
    x_range: tuple[float, float]
    warnings: list[str] = Field(default_factory=list)
    solver_options: dict[str, Any] = Field(default_factory=dict)
    csv: str
    csv_rows: int = 0
    op: dict[str, float] | None = None
    stats: dict[str, Stats] | None = None
    gain: dict[str, GainSummary] | None = None
    noise: NoiseSummary | None = None
    table: Table | None = None
    family: list[Curve] | None = None
    partition: str | None = None


class SweepRow(BaseModel):
    value: Any
    result: float | None = None
    error: str | None = None


class SweepResult(BaseModel):
    target: str
    metric: dict[str, Any]
    rows: list[SweepRow]


class MonteCarloRun(BaseModel):
    values: dict[str, float]
    metric: float | None = None
    error: str | None = None


class MonteCarloResult(BaseModel):
    n: int = 0
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    runs: list[MonteCarloRun]
    error: str | None = None


class ProjectRunResult(BaseModel):
    captured: list[str]
    runs: list[SimResult | dict[str, Any]]
    screenshot: str
