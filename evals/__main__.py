"""`python -m evals` — the billed half (RC1-258).

Layer 1 is `pytest` and needs no key. This is layer 2: it builds each fixture's
prompt from the committed builder and calls the model. Exit codes match the
other repos' harnesses — 0 all passed, 1 a case failed, 2 a case errored.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_evals.record import RunRecord, RunStore, new_run_id

from evals import fixtures, subject, workflow

RUNS_PATH = Path(os.environ.get("EVAL_RUNS_PATH", "./eval-runs/runs.jsonl"))


def _print(result) -> None:
    if result.error:
        print(f"  ERROR {result.case_id}: {result.error}")
        return
    status = "pass" if result.passed else "FAIL"
    print(f"  {status} {result.case_id}  ({result.usage.latency_ms / 1000:.0f}s)")
    for c in result.characteristics:
        if c.passed and not c.advisory:
            continue
        mark = "~" if c.advisory else "✗"
        tag = " [advisory]" if c.advisory else ""
        print(f"    {mark} {c.name}{tag}: {c.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals", description=__doc__)
    parser.add_argument("--case", help="run a single fixture by id")
    parser.add_argument(
        "--show-prompt", action="store_true", help="print the bound prompt and exit"
    )
    args = parser.parse_args(argv)

    if args.show_prompt:
        fixture = fixtures.BY_ID[args.case or "headliner"]
        print(
            workflow.build_empty_prompt(fixture.artist)
            if not fixture.shows
            else workflow.build_preview_prompt(
                fixture.artist, fixture.shows, headliner=fixture.headliner
            )
        )
        return 0

    key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        subject.preflight(key)
    except Exception as exc:
        print(f"cannot run: {exc}", file=sys.stderr)
        return 2

    import anthropic

    client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=3)
    cases = subject.CASES
    if args.case:
        cases = tuple(c for c in cases if c.id == args.case)
        if not cases:
            print(f"no fixture {args.case!r}", file=sys.stderr)
            return 2

    print(f"{len(cases)} case(s) against {workflow.model()} — this spends money.\n")
    started = datetime.now(UTC)
    results = [subject.run(c, client) for c in cases]
    for r in results:
        _print(r)

    record = RunRecord(
        run_id=new_run_id(subject.NAME),
        subject_version=subject.version(),
        started_at=started,
        finished_at=datetime.now(UTC),
        results=results,
    )
    RunStore(RUNS_PATH).append(record)
    print(f"\nrun {record.run_id} recorded")

    if any(r.error for r in results):
        return 2
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
