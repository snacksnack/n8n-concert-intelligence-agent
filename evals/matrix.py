"""`python -m evals.matrix` — the RC1-505 provider bake-off (two experiments).

This is a *sibling* of `python -m evals`, not a replacement. The incumbent
subject (`evals/subject.py`) still runs on its own, records under subject
`concert-preview`, and owns the shared trend. This module reuses that subject's
scorers but records the experimental arms under a separate subject
(`concert-matrix`) in a separate local store by default, so a five-provider
sweep never muddies the incumbent's history.

Experiment 1 (generation, retrieval held constant): `claude-gen`, `cohere-gen`,
`sonar-gen` all get the *same* setlist.fm data; only the model varies.
Experiment 2 (grounded-web, task held constant): `claude-gen` (two-stage
baseline) vs `claude-web` and `sonar-web`, which retrieve from the open web
themselves. Everything is graded against the same setlist.fm oracle
(`fixture.songs`).

Nothing here spends money under `--dry-run`: it prints the arm/case plan and a
sample prompt per seat and exits.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import anthropic
from agent_evals.record import (
    CaseResult,
    CharacteristicResult,
    RunStore,
    SubjectVersion,
    Usage,
)
from agent_evals.runner import exit_code, print_result, record_run

from evals import arms, eval_set, fixtures, subject, workflow

SUBJECT = "concert-matrix"
DEFAULT_STORE = Path("eval-runs/matrix.jsonl")


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _prompt_for(arm: arms.Arm, fixture: fixtures.Fixture) -> str:
    if arm.seat == arms.SEAT_GENERATION:
        if not fixture.shows:
            return workflow.build_empty_prompt(fixture.artist)
        return workflow.build_preview_prompt(
            fixture.artist, fixture.shows, headliner=fixture.headliner
        )
    return arms.grounded_task_prompt(fixture.artist, headliner=fixture.headliner)


def _score(
    fixture: fixtures.Fixture, seat: str, response: arms.ArmResponse
) -> tuple[list[CharacteristicResult], dict]:
    """Reuse the incumbent subject's scorers; add a citation signal for the web seat."""
    text = response.text
    observations = {
        "artist": fixture.artist,
        "seat": seat,
        "searches": response.searches,
        "citations": len(response.citations),
        "output": text,
    }
    if not fixture.shows:
        return [subject._exact_sentence(text, fixture)], observations
    characteristics = [
        subject._no_preamble(text),
        subject._real_songs(text, fixture),
        subject._sentence_count(text),
    ]
    if fixture.headliner:
        characteristics.append(subject._opener_note(text))
    if seat == arms.SEAT_GROUNDED:
        characteristics.append(
            CharacteristicResult(
                name="cites-sources",
                passed=bool(response.citations),
                detail=(
                    f"{len(response.citations)} citation(s) over {response.searches} search(es)"
                    if response.citations
                    else "no citations returned — groundedness cannot be traced to a source"
                ),
                advisory=True,
            )
        )
    return characteristics, observations


def _run_one(arm: arms.Arm, fixture: fixtures.Fixture) -> CaseResult:
    prompt = _prompt_for(arm, fixture)
    started = time.perf_counter()
    try:
        response = arm.generate(prompt)
    except Exception as exc:  # noqa: BLE001 — an arm failure is a recorded error, not a crash
        return CaseResult(
            case_id=fixture.id,
            usage=Usage(latency_ms=(time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )
    latency_ms = (time.perf_counter() - started) * 1000
    characteristics, observations = _score(fixture, arm.seat, response)
    return CaseResult(
        case_id=fixture.id,
        characteristics=characteristics,
        usage=Usage(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=arm.cost(response),
            latency_ms=latency_ms,
        ),
        observations=observations,
    )


def _applicable(arm: arms.Arm, fixture: fixtures.Fixture) -> bool:
    """A grounded-web arm needs an oracle to be graded against, so it skips the
    no-setlist fixture (there is nothing to retrieve toward and nothing to check)."""
    return not (arm.seat == arms.SEAT_GROUNDED and not fixture.shows)


def _version(arm: arms.Arm, sha: str) -> SubjectVersion:
    return SubjectVersion(
        subject=SUBJECT,
        code_version=f"{arm.name}@{sha}",
        model=arm.model,
        prompt_version=f"matrix-{arm.seat}",
    )


def _groundedness(results: list[CaseResult]) -> float | None:
    checked = [
        c
        for r in results
        if r.error is None
        for c in r.characteristics
        if c.name == "names-only-real-songs"
    ]
    if not checked:
        return None
    return sum(1 for c in checked if c.passed) / len(checked)


def _summary(per_arm: dict[str, list[CaseResult]], by_name: dict[str, arms.Arm]) -> None:
    print("\n=== comparison ===")
    header = (
        f"{'arm':<12} {'seat':<12} {'pass':>7} {'ground':>7} "
        f"{'lat(s)':>7} {'$/call':>9} {'srch':>5}"
    )
    print(header)
    print("-" * len(header))
    for name, results in per_arm.items():
        scored = [r for r in results if r.error is None]
        passed = sum(1 for r in scored if r.passed)
        pass_rate = f"{passed}/{len(results)}"
        ground = _groundedness(results)
        ground_s = "-" if ground is None else f"{ground:.0%}"
        lat = (
            sum(r.usage.latency_ms for r in scored) / len(scored) / 1000 if scored else 0.0
        )
        cost = sum((r.usage.cost_usd for r in scored), Decimal("0"))
        per_call = cost / len(scored) if scored else Decimal("0")
        searches = sum(r.observations.get("searches", 0) for r in results)
        print(
            f"{name:<12} {by_name[name].seat:<12} {pass_rate:>7} {ground_s:>7} "
            f"{lat:>7.1f} {float(per_call):>9.4f} {searches:>5}"
        )


def _build_arms(max_searches: int) -> dict[str, arms.Arm]:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    client = (
        anthropic.Anthropic(api_key=anthropic_key, timeout=90.0, max_retries=3)
        if anthropic_key
        else None
    )
    return arms.build_arms(
        anthropic_client=client,
        cohere_key=os.environ.get("COHERE_API_KEY"),
        perplexity_key=os.environ.get("PERPLEXITY_API_KEY"),
        model=workflow.model() or "claude-sonnet-4-6",
        max_tokens=max(workflow.max_tokens(), 150),
        max_searches=max_searches,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.matrix", description=__doc__)
    parser.add_argument("--experiment", type=int, choices=(1, 2), help="run one experiment's arms")
    parser.add_argument("--arm", action="append", help="run only this arm (repeatable)")
    parser.add_argument("--case", help="run a single fixture by id")
    parser.add_argument("--limit", type=int, help="cap the number of fixtures")
    parser.add_argument("--max-searches", type=int, default=3, help="web_search cap per call")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE, help="local JSONL run store")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit, no spend")
    parser.add_argument(
        "--goldens", action="store_true", help="use the 3 built-in fixtures, not the eval set"
    )
    args = parser.parse_args(argv)

    available = _build_arms(args.max_searches)
    selected = {
        name: arm
        for name, arm in available.items()
        if (args.experiment is None or args.experiment in arm.experiments)
        and (args.arm is None or name in args.arm)
    }

    frozen = () if args.goldens else eval_set.load()
    cases = list(frozen) if frozen else list(fixtures.FIXTURES)
    source = "eval-set" if frozen else "goldens"
    if args.case:
        cases = [f for f in cases if f.id == args.case]
        if not cases:
            print(f"no fixture {args.case!r}", file=sys.stderr)
            return 2
    if args.limit:
        cases = cases[: args.limit]

    wanted = ("claude-gen", "cohere-gen", "sonar-gen", "claude-web", "sonar-web")
    missing = [
        n
        for n in wanted
        if n not in available and (args.arm is None or n in args.arm)
    ]

    if args.dry_run:
        print(f"arms available: {', '.join(available) or '(none — no keys set)'}")
        if missing:
            print(f"arms skipped (no key): {', '.join(missing)}")
        print(f"arms selected: {', '.join(selected) or '(none)'}")
        print(f"fixtures ({source}): {', '.join(f.id for f in cases)}\n")
        sample = cases[0] if cases else fixtures.FIXTURES[0]
        print("--- sample generation prompt ---")
        print(workflow.build_preview_prompt(sample.artist, sample.shows, headliner=sample.headliner)
              if sample.shows else workflow.build_empty_prompt(sample.artist))
        print("\n--- sample grounded-web prompt ---")
        print(arms.grounded_task_prompt(sample.artist, headliner=sample.headliner))
        return 0

    if not selected:
        print("no arms selected (check keys and filters)", file=sys.stderr)
        return 2

    sha = _git_sha()
    per_arm: dict[str, list[CaseResult]] = {}
    overall: list[CaseResult] = []
    for name, arm in selected.items():
        arm_cases = [f for f in cases if _applicable(arm, f)]
        print(
            f"\n## {name} ({arm.seat}, {arm.model}) — "
            f"{len(arm_cases)} case(s), this spends money"
        )
        started = datetime.now(UTC)
        results = [_run_one(arm, f) for f in arm_cases]
        for r in results:
            print_result(r)
        record_run(_version(arm, sha), started, results, store=RunStore(args.store))
        per_arm[name] = results
        overall.extend(results)

    _summary(per_arm, selected)
    if missing:
        print(f"\nnot run (no key): {', '.join(missing)}")
    return exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
