"""Read the provider-matrix run store and report by experiment + cohort (RC1-505).

`python -m evals.matrix` records one run per arm under subject `concert-matrix`.
This reads the newest run for each arm and separates the things the raw pass
rate conflates:

* **groundedness** (`names-only-real-songs`) vs. **format** (`is-two-to-three-
  sentences`, preamble) — a model can be perfectly grounded and still fail the
  length rule, and lumping them hides which arm is actually trustworthy.
* **cohort** (active / tribute / stale) — grading a grounded-web arm against
  setlist.fm is only clean for currently-touring acts; the tribute/defunct
  entries are the trusted-source failure-mode exhibit, not a fair quality score.
* **sources retrieved** — a grounded-web "miss" against the oracle that still
  pulled real sources is coverage, not hallucination, so the source count is
  reported beside groundedness rather than folded into it.

Reads only the local store; no keys, no spend.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean

from agent_evals.record import RunRecord, RunStore

SUBJECT = "concert-matrix"
DEFAULT_STORE = Path("eval-runs/matrix.jsonl")

GROUND = "names-only-real-songs"
SENTENCES = "is-two-to-three-sentences"
PREAMBLE = "starts-with-the-preview-itself"

GEN_ARMS = ("claude-gen", "cohere-gen", "sonar-gen")
WEB_ARMS = ("claude-web", "sonar-web")


def latest_per_arm(records: list[RunRecord]) -> dict[str, RunRecord]:
    """Newest run keyed by arm name (store is oldest-first, so last write wins)."""
    out: dict[str, RunRecord] = {}
    for record in records:
        if record.subject_version.subject != SUBJECT:
            continue
        arm = record.subject_version.code_version.split("@", 1)[0]
        out[arm] = record
    return out


def _rate(results: list, name: str) -> tuple[float | None, int]:
    flags = [
        c.passed
        for r in results
        if r.error is None
        for c in r.characteristics
        if c.name == name
    ]
    return (sum(flags) / len(flags), len(flags)) if flags else (None, 0)


def _pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value:>4.0%}"


def _arm_row(arm: str, record: RunRecord) -> str:
    results = record.results
    scored = [r for r in results if r.error is None]
    errored = sum(1 for r in results if r.error)
    ground, _ = _rate(results, GROUND)
    sentences, _ = _rate(results, SENTENCES)
    cost = sum((r.usage.cost_usd for r in scored), Decimal("0"))
    per_call = (cost / len(scored)) if scored else Decimal("0")
    latency = mean([r.usage.latency_ms for r in scored]) / 1000 if scored else 0.0
    sources = mean([r.observations.get("sources", 0) for r in scored]) if scored else 0.0
    err = f" ({errored} err)" if errored else ""
    return (
        f"{arm:<12}{record.subject_version.model:<22} {_pct(ground)}  {_pct(sentences)}  "
        f"{latency:>6.1f}  {float(per_call):>8.4f}  {sources:>5.1f}{err}"
    )


def _cohort_ground(record: RunRecord) -> dict[str, tuple[float | None, int]]:
    buckets: dict[str, list] = defaultdict(list)
    for r in record.results:
        buckets[r.observations.get("cohort", "active")].append(r)
    return {cohort: _rate(rs, GROUND) for cohort, rs in buckets.items()}


def _table(title: str, arms: tuple[str, ...], latest: dict[str, RunRecord]) -> None:
    print(f"\n{title}")
    header = (
        f"{'arm':<12}{'model':<22} {'grnd':>5}  {'2-3s':>5}  "
        f"{'lat s':>6}  {'$/call':>8}  {'srcs':>5}"
    )
    print(header)
    print("-" * len(header))
    for arm in arms:
        if arm in latest:
            print(_arm_row(arm, latest[arm]))
        else:
            print(f"{arm:<12}(no run recorded)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.report", description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    args = parser.parse_args(argv)

    records = RunStore(args.store).all()
    latest = latest_per_arm(records)
    if not latest:
        print(f"no {SUBJECT} runs in {args.store}")
        return 1

    print(f"provider matrix — newest run per arm in {args.store}")
    print("grnd = named only real (in-oracle) songs | 2-3s = sentence-count rule")
    _table("Experiment 1 — generation (same setlist.fm data, vary the model)", GEN_ARMS, latest)
    _table("Experiment 2 — grounded web (retrieve + write; vs claude-gen baseline)",
           ("claude-gen", *WEB_ARMS), latest)

    print("\nGroundedness by cohort (names-only-real-songs pass rate vs the setlist.fm oracle)")
    cohorts = ("active", "tribute", "stale")
    header = f"{'arm':<12}" + "".join(f"{c:>10}" for c in cohorts)
    print(header)
    print("-" * len(header))
    for arm in (*GEN_ARMS, *WEB_ARMS):
        if arm not in latest:
            continue
        by = _cohort_ground(latest[arm])
        cells = "".join(
            f"{(f'{by[c][0]:.0%}' if c in by and by[c][0] is not None else '-'):>10}"
            for c in cohorts
        )
        print(f"{arm:<12}{cells}")
    print(
        "\nNote: for the web arms a low 'active' groundedness is largely oracle coverage "
        "(real recent songs absent from the 3 frozen setlists), not hallucination — see "
        "the per-call source counts above."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
