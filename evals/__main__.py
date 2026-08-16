"""`python -m evals` — the billed half (RC1-258).

Layer 1 is `pytest` and needs no key. This is layer 2: it builds each fixture's
prompt from the committed builder and calls the model. The run/record/exit
plumbing is `agent_evals.runner` (RC1-262); what lives here is this repo's
subject, its fixtures, and its key.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from agent_evals.runner import UnknownCase, exit_code, print_result, record_run, select_cases

from evals import fixtures, subject, workflow


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
    try:
        cases = select_cases(subject.CASES, args.case)
    except UnknownCase as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"{len(cases)} case(s) against {workflow.model()} — this spends money.\n")
    started = datetime.now(UTC)
    results = [subject.run(c, client) for c in cases]
    for r in results:
        print_result(r)

    record_run(subject.version(), started, results)
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
