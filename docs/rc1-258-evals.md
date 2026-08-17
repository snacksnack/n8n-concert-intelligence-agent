# Evaluating the concert-preview prompt (RC1-258)

The workflow runs in n8n Cloud. One node calls a model — `Claude Request`,
between `Build Prompt` and `Attach Previews` — and that is what these tests
cover. Two layers, split by what they cost.

## Layer 1 — the prompt contract (free, gates)

`pytest`. No credentials, no tokens, runs on every push. Everything is read out
of `workflows/concert-intelligence-agent.json` at test time, including the
prompt templates themselves, so the tests are about the shipped workflow or
about nothing.

`Build Prompt` has two branches and both are frozen:

* **no setlists** — asks for one exact sentence, quoted inside the instruction.
  The rare golden where the correct output is known literally.
* **preview** — 2-3 sentences, "Output ONLY the preview itself with no
  preamble", plus a conditional opener note when the artist is supporting.

## The two assumptions worth knowing about

Neither is a bug this ticket fixes. Both are now asserted so a change is visible.

**Previews are matched to artists by position.** `Attach Previews` walks
`Build Prompt`'s items by index and reads the Claude response at the same index:

```js
for (let i = 0; i < prompts.length; i++) {
  const aid = prompts[i].json.artist_id;
  const text = claudeOut[i]?.json?.content?.[0]?.text || 'No preview available.';
}
```

If the request node ever reorders, drops or retries an item, a preview lands on
the wrong artist — and it reads perfectly, because a plausible concert preview
under the wrong band name looks like content rather than a bug.

**The failure fallback is indistinguishable from a real result.** That same line
substitutes `'No preview available.'` when there is no text — an API failure, a
truncation, a misaligned index. The no-setlist branch legitimately produces
"No recent setlist data available for X." To a reader of the email, "we could
not reach the model" and "this artist has no recent setlists" look the same.

## Layer 2 — prompt goldens (billed, by hand)

```bash
export ANTHROPIC_API_KEY=...
python -m evals                    # all three fixtures
python -m evals --case opener      # one
python -m evals --show-prompt      # the built prompt, free
```

Nothing parses this output — `Attach Previews` writes it straight into the email
and the Notion page — so every flaw ships. That decides what is scored: no
preamble, no invented song titles, 2-3 sentences, and the opening-set note when
the artist is supporting.

The run/record/exit plumbing is the shared
[`agent-evals`](https://github.com/snacksnack/agent-evals) harness (RC1-262);
what lives here is only this repo's subject and fixtures. `ANTHROPIC_API_KEY`
is read from the process environment — this repo's eval path reads no `.env`.
Records land in the shared store when `EVAL_DATABASE_URL` is set (else a local
gitignored `eval-runs/runs.jsonl`), and render to the
[quality trend page](https://snacksnack.github.io/agent-evals/) — see the
library's
[runbook](https://github.com/snacksnack/agent-evals/blob/main/docs/measuring.md).

Two false positives shaped the song check, both found by running it:

| Flagged | Why it was wrong |
| --- | --- |
| `'PPP,'` `'Levitation,'` | American-style punctuation inside the quotes |
| `'t duck out before the encore —'` | Captured between the apostrophes in "don't" |

## What is deliberately not tested

End-to-end execution through the schedule trigger. It would need a test variant
of the workflow with fixture-injecting nodes so it does not hit live Spotify,
Ticketmaster, Setlist.fm, Gmail, Notion and Google Calendar, plus a hosted n8n
instance in CI. Revisit if a bug appears that layers 1 and 2 could not have
caught — that is the evidence that would justify the cost.
