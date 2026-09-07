# CLAUDE.md — working notes for AI sessions and the review agent

## What this is

An n8n Cloud workflow that scores upcoming concerts against Spotify listening
history and emails a daily digest with Claude-written setlist previews, plus
Google Calendar events and a Notion tracker. The deployable artifact is
`workflows/concert-intelligence-agent.json`; nothing here is a Python package.
Design notes are in `README.md`; the eval design is `docs/rc1-258-evals.md`.

## Layout

- `workflows/concert-intelligence-agent.json` — the workflow. Logic lives in
  JavaScript Code nodes (`Build Artist Profile`, `Match Concerts to Artists`,
  `Score Concerts`, `Build Prompt`, `Attach Previews`, `Build LLM Spans`, …) and
  HTTP Request nodes. Edited in the n8n editor and exported; a re-import
  overwrites what is in n8n.
- `evals/` — `workflow.py` reads the prompt and the consumer node out of the
  workflow JSON; `fixtures.py`; `subject.py` is the billed layer-2 subject on
  the shared `agent-evals` harness (pinned by tag in `requirements.txt`).
- `tests/` — layer 1, free: `test_contract.py` freezes the prompt contract and
  the undeclared assumptions below; `test_scoring.py` checks the layer-2 scorers
  in both directions.
- `scripts/*.js` — Ticketmaster matching QA outside n8n; `qa/` holds its inputs
  (the real ones are gitignored).
- `LOGGING-SETUP.md` — the `run_log` Data Table columns, created by hand in n8n.

## Conventions (hold changes to these)

- **No secrets in the JSON.** API keys are n8n Variables (`$vars.TICKETMASTER_API_KEY`,
  `$vars.SETLIST_FM_API_KEY`, `$vars.ANTHROPIC_API_KEY`, `$vars.DD_API_KEY`);
  OAuth is an n8n credential referenced by id. A literal key or token in a node
  is a blocker.
- **Node names are an interface.** `evals/workflow.py` and the tests look nodes
  up by name (`Build Prompt`, `Claude Request`, `Attach Previews`,
  `Build LLM Spans`, `Report LLM Spans`). Renaming one fails the tests on
  purpose: update the eval, do not delete the assertion.
- **Two assumptions in `Attach Previews` are frozen, not fixed.** Previews are
  matched to artists by position (prompt *i* ↔ response *i*), and the
  `'No preview available.'` fallback reads like a legitimate result. A change to
  either must show up in `tests/test_contract.py`.
- **Side branches never block the digest.** Anything hung off `Claude Request`
  other than `Attach Previews` (the LLM Observability span nodes) dead-ends and
  sets `onError: continueRegularOutput`. `Attach Previews` stays the request
  node's first output; an HTTP node between them would replace every item's
  JSON with the response and silently misattribute every preview.
- **Ticketmaster is paged per month and serialized.** `Loop Over Ticketmaster
  Windows` + a 2 s wait, pages 0–4 per window (deeper paging is rejected past
  1,000 results), `ignoreResponseCode` on the request with error pages tagged
  `reason: 'ticketmaster_error'` and counted rather than read as empty windows.
  Do not collapse the loop or page deeper.
- **Bounded prompts.** `Build Prompt` sends at most five setlists
  (`.slice(0, 5)`); `max_tokens` on `Claude Request` stays ≥ 150.
- **Duplicated JavaScript is checked, not trusted.** Where Python mirrors a
  node (`format_setlists`), `test_contract.py` asserts the shipped source still
  matches. New mirrors need the same assertion.
- **Python (evals and tests):** 3.12, `from __future__ import annotations`,
  ruff with line-length 100 and rules E, F, I, UP, B, SIM. Tests run offline
  with no credentials; the eval path reads no `.env` — `ANTHROPIC_API_KEY`
  comes from the process environment.

## Testing

```bash
pytest                       # layer 1 — free, CI on every push
ruff check .                 # lint
python -m evals              # layer 2 — BILLED, by hand, needs ANTHROPIC_API_KEY
python -m evals --show-prompt  # the built prompt, free
```

## Workflow

One branch per ticket, `rc1-NNN-short-slug`; commit subjects lead with the Jira
key, `RC1-NNN: what changed`. Workflow edits are made in n8n and exported into
`workflows/`, so a PR that changes a node should say which nodes and why.
