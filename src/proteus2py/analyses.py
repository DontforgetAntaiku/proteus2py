"""Analysis registry: each handler writes the `*PROPERTIES` entries for one analysis card and returns the result kind."""
from __future__ import annotations

from collections.abc import Callable

from .elements import Ctx
from .errors import SpecError
from .models import AcAnalysis, CircuitSpec, DcAnalysis, DistortionAnalysis, NoiseAnalysis, OpAnalysis, TransientAnalysis, Wave
from .sdf import num
from .units import to_float

Handler = Callable[..., str]
REGISTRY: dict[type, Handler] = {}
PREPARE: dict[type, Callable[[CircuitSpec], CircuitSpec]] = {}     # spec rewrites that must happen before elements are translated


def analysis(cls: type) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        REGISTRY[cls] = fn
        return fn
    return deco


def _first_v(ctx: Ctx) -> str | None:
    return next((p.ref for p in ctx.spec.parts if p.kind == "V"), None)


@analysis(OpAnalysis)
def _op(ctx: Ctx, a: OpAnalysis) -> str:
    ctx.sdf.props.update(SIMTYPE="TRANSIENT", TSTART="0", TSTOP="1e-9")           # the t = 0 point is the operating point
    return "op"


@analysis(TransientAnalysis)
def _tran(ctx: Ctx, a: TransientAnalysis) -> str:
    ctx.sdf.props.update(SIMTYPE="TRANSIENT", TSTART=num(a.tstart), TSTOP=num(a.tstop), TMAX=num(a.tmax if a.tmax is not None else to_float(a.tstop) / 1000))
    return "transient"


@analysis(AcAnalysis)
def _ac(ctx: Ctx, a: AcAnalysis) -> str:
    ref = ctx.ac_ref or _first_v(ctx)
    if ref is None:
        raise SpecError('ac analysis needs a voltage source (mark one with "ac": true)')
    ctx.sdf.props.update(SIMTYPE="FREQUENCY", FSTART=num(a.fstart), FSTOP=num(a.fstop), NUMSTEPS=num(a.points), FREQREF=ref)
    ctx.sdf.props[a.mode.upper()] = "TRUE"
    return "ac"


@analysis(NoiseAnalysis)
def _noise(ctx: Ctx, a: NoiseAnalysis) -> str:
    src = a.input or ctx.ac_ref or _first_v(ctx)
    if src is None:
        raise SpecError('noise analysis needs a voltage source "input" (ref)')
    ctx.sdf.props.update(SIMTYPE="NOISE", FSTART=num(a.fstart), FSTOP=num(a.fstop), NUMSTEPS=num(a.points), INPUT=src,
                         LOGSPECTRAL="FALSE", LOGTOTAL="TRUE", FREQREF=src)
    ctx.sdf.props[a.mode.upper()] = "TRUE"
    return "noise"


def _prepare_distortion(spec: CircuitSpec) -> CircuitSpec:
    a = spec.analysis
    assert isinstance(a, DistortionAnalysis)
    src = next((p for p in spec.parts if p.ref == a.source), None)
    if src is None or src.kind != "V":
        raise SpecError(f"distortion: source {a.source!r} must be a V part")
    wave = src.wave.model_dump(exclude_none=True) if src.wave else {"type": "sine"}
    if wave["type"] != "sine":
        raise SpecError("distortion: the source must be a sine wave")
    wave["freq"] = a.f0
    if a.amplitude is not None:
        wave["amp"] = a.amplitude
    parts = [p.model_copy(update={"wave": Wave(**wave)}) if p.ref == a.source else p for p in spec.parts]
    return spec.model_copy(update={"parts": parts, "options": {"RELTOL": "1e-5", **spec.options}, "probes": [f"v({a.output})"]})


PREPARE[DistortionAnalysis] = _prepare_distortion


@analysis(DistortionAnalysis)
def _distortion(ctx: Ctx, a: DistortionAnalysis) -> str:
    f0 = to_float(a.f0)
    ctx.sdf.props.update(SIMTYPE="TRANSIENT", TSTART="0", TSTOP=num((a.settle + a.periods) / f0), TMAX=num(1 / (f0 * 256)))
    return "distortion"


@analysis(DcAnalysis)
def _dc(ctx: Ctx, a: DcAnalysis) -> str:
    ctx.sdf.props.update(ANALYSIS="DC", NAME1=a.source, START1=num(a.start), STOP1=num(a.stop), STEP1=num(a.step))
    if a.source2:
        if None in (a.start2, a.stop2, a.step2):
            raise SpecError("nested DC sweep needs start2, stop2 and step2")
        ctx.sdf.props.update(NAME2=a.source2, START2=num(a.start2), STOP2=num(a.stop2), STEP2=num(a.step2))
    return "dc"
