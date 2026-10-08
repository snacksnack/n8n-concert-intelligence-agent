"""Optional Datadog LLM Observability emission for the provider matrix (RC1-505).

Off unless asked for (`--datadog`) *and* `DD_API_KEY` is set; otherwise every
function here is a no-op, so a run without Datadog is unaffected. When on, each
arm call becomes an `llm` span under one ml_app (`concert-matrix`), tagged with
arm / seat / experiment / provider / cohort / artist and carrying token metrics
plus the cost this harness computed — so all five arms line up in the LLM Obs UI
beside the estate's production model callers, and cost/latency are comparable
across providers.

Agentless, so no Datadog Agent is needed to run it locally. `ddtrace` is only
imported inside `tracing()` — it is a CI/optional dependency that is not always
installed for a plain eval run, and the no-Datadog path must not require it.
The span carries a `version` tag (RC1-441: the intake drops a root span's tags
without one).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

ML_APP = "concert-matrix"


@contextlib.contextmanager
def tracing(enabled: bool, *, version: str) -> Iterator[Any]:
    """Enable LLM Obs for the duration, or yield None when off/unconfigured."""
    if not (enabled and os.environ.get("DD_API_KEY")):
        yield None
        return
    from ddtrace.llmobs import LLMObs  # optional dep; see module docstring

    os.environ.setdefault("DD_VERSION", version)
    LLMObs.enable(
        ml_app=ML_APP,
        agentless_enabled=True,
        api_key=os.environ["DD_API_KEY"],
        site=os.environ.get("DD_SITE", "datadoghq.com"),
    )
    try:
        yield LLMObs
    finally:
        LLMObs.flush()
        LLMObs.disable()


@contextlib.contextmanager
def llm_span(llmobs: Any, arm: Any) -> Iterator[Any]:
    """An `llm` span around one arm call (duration = the generate wall time)."""
    if llmobs is None:
        yield None
        return
    with llmobs.llm(model_name=arm.model, model_provider=arm.provider, name=arm.name) as span:
        yield span


def annotate(
    llmobs: Any,
    span: Any,
    *,
    arm: Any,
    artist: str,
    cohort: str,
    prompt: str,
    text: str,
    input_tokens: int,
    output_tokens: int,
    searches: int,
    cost: Decimal,
    version: str,
) -> None:
    """Attach I/O, token metrics, and comparison tags to one arm's span."""
    if llmobs is None or span is None:
        return
    llmobs.annotate(
        span,
        input_data=[{"role": "user", "content": prompt}],
        output_data=[{"role": "assistant", "content": text}],
        metrics={
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(input_tokens + output_tokens),
        },
        tags={
            "arm": arm.name,
            "seat": arm.seat,
            "experiment": "+".join(str(e) for e in arm.experiments),
            "provider": arm.provider,
            "cohort": cohort,
            "artist": artist,
            "searches": str(searches),
            "cost_usd": str(cost),
            "version": version,
        },
    )
