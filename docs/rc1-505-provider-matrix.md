# Which model should write a grounded concert preview?

A five-arm, two-experiment bake-off across Anthropic, Cohere, and Perplexity,
run on real traffic from my concert-intelligence agent (RC1-505).

## The seat under test

The agent emails a daily concert digest. One step writes a two to three sentence
"preview" of what an artist is playing live. Today that step is two stages:

1. **Retrieval**: a setlist.fm API call pulls the artist's recent setlists
   (authoritative, structured, not an LLM).
2. **Generation**: Claude writes the preview from that setlist JSON, with no
   tools and no web access.

Perplexity's Sonar is built for a different shape: retrieve from the open web
*and* synthesize, in one call. So the real question is not "swap one model for
another," it is "should the model do its own retrieval from the open web, or
should we keep handing it a trusted source?" That is a build-vs-buy,
first-party-vs-third-party decision, which is what this evaluation is really about.

## Method: two controlled experiments, not one leaderboard

A single ranking would conflate two independent variables: the generation
*model* and the retrieval *architecture*. Splitting them keeps the comparison
honest (apples to apples where it should be):

- **Experiment 1 (generation).** Hand every model the identical setlist.fm data;
  only the model varies. Arms: `claude-gen` (Claude Sonnet 4.6), `cohere-gen`
  (Command A), `sonar-gen` (Perplexity Sonar, search off).
- **Experiment 2 (grounded web).** Same task, but the model retrieves from the
  open web itself. Arms: `claude-web` (Claude + the Messages `web_search` tool)
  and `sonar-web` (Sonar + `web_search`), against the two-stage `claude-gen`
  baseline.

Claude and Sonar appear in both experiments with search off and on, which
isolates what open-web search adds and costs for the same model.

Every arm is graded against one oracle: 25 real artists whose recent setlists
were frozen from setlist.fm. Scoring reuses the agent's own eval harness:
groundedness (does the preview name only songs the artist actually played,
measured by quoted titles present in the oracle), the format rules, latency,
and cost per call. Each provider is called through its official SDK, and every
call is also emitted to Datadog LLM Observability (one ml_app, tagged by
arm / provider / cohort) so the five arms are comparable beside the estate's
production model callers.

## Results

**Experiment 1 (generation, same data, vary the model):**

| arm | model | groundedness | format (2-3 sent.) | latency | cost/call |
|---|---|---|---|---|---|
| cohere-gen | Command A | **92%** | 80% | 3.3s | $0.0020 |
| claude-gen | Claude Sonnet 4.6 | 68% | 84% | 4.7s | $0.0038 |
| sonar-gen | Perplexity Sonar | 36% | 60% | 3.0s | **$0.0004** |

**Experiment 2 (grounded web, same task, vary retrieval):**

| arm | model | groundedness | latency | cost/call | sources/call |
|---|---|---|---|---|---|
| claude-gen (baseline) | Claude Sonnet 4.6 | 68% | 4.7s | $0.0038 | n/a |
| sonar-web | Perplexity Sonar | 28% | **6.1s** | **$0.0043** | 15 |
| claude-web | Claude Sonnet 4.6 | 8% | 35.2s | $0.1864 | 18 |

## Four findings

**1. For generation from trusted data, Cohere Command wins on fidelity.** At 92%
groundedness it stuck closest to the setlist we handed it, at the second-lowest
cost and fastest-but-one latency. Notably, Command had previously lost a
different seat in this estate (a PR-review reasoning task); it wins this one.
Different jobs, different winners, which is the whole point of evaluating per
seat rather than crowning a single "best" model.

**2. Open-web search is dramatically cheaper and faster from Perplexity than
from Anthropic.** For the identical grounded-web task, `sonar-web` cost
**$0.0043 and took 6.1s**, while `claude-web` cost **$0.1864 and took 35.2s**:
about **43x the cost and 6x the latency**. Claude's 2026 web-search tool runs an
internal code-execution filtering step that inflates both; Sonar is purpose-built
for search-grounded answers. If grounded web answering is a seat you run at any
volume, that gap decides it.

**3. The groundedness number inverts on bad source data, and that is the real
finding.** I split the 25 artists into cohorts. Some names resolve on setlist.fm
to tribute or cover acts: "The Smiths" at a 2026 club date, a Tom Petty tribute,
a covers act booked as Taylor Swift. On that `tribute` cohort the metric flips
meaning:

| arm | active | tribute |
|---|---|---|
| cohere-gen | 94% | 83% |
| claude-gen | 71% | 50% |
| sonar-web | 35% | 0% |
| claude-web | 12% | 0% |

The generation arms score *high* on tribute because they **faithfully reproduce
the tribute setlist we gave them**: they will confidently write that The Smiths
are playing "How Soon Is Now" at a 2026 show. The grounded-web arms score *zero*
because they **refuse to**: they search, find the band broke up in 1987, and say
so (`claude-web` wrote "David Bowie had 0 concerts in 2025"). On bad data, high
groundedness-to-source is the failure and low is the success. A fidelity metric
only measures quality when the source is trustworthy.

**4. The web arms' low groundedness on real artists is mostly coverage, not
hallucination.** `claude-web` and `sonar-web` score low against the oracle on the
`active` cohort largely because they name real, recent songs that are missing
from the three frozen setlists (for example Billie Eilish's "Halley's Comet"),
while pulling 15 to 18 real sources per call. Graded against a richer ground
truth, much of that "miss" is coverage the trusted source lacked.

## Verdict

Keep the two-stage pipeline (setlist.fm plus a faithful, cheap generator) as the
default: it is the cheapest, fastest, and most faithful path when the source is
good, and Cohere Command or Claude both do the generation step well. But the
pipeline has one blind spot, shown above: it launders bad source data into
confident fiction. A grounded-web pass is the safety net that catches that, and
**Perplexity Sonar is what makes the net affordable** at roughly one-fortieth the
cost and one-sixth the latency of the first-party web-search tool. The
recommendation is a hybrid: trusted source first, a Sonar grounded-web check as
validation or fallback when confidence is low (artist not found, stale data,
a defunct act).

Where Sonar is the wrong tool is just as clear. As a pure generator over data we
already hold (`sonar-gen`, 36% groundedness) it is the weakest arm, and for
defunct artists it tends to return canonical-but-not-recent songs rather than
flag the problem, where Claude's web search was more cautious. Sonar's edge is
specifically cheap, search-grounded retrieval, not reasoning over provided data.

## Honest caveats

- **n = 25 artists, single run.** Directional, not a benchmark. Groundedness is
  stochastic run to run.
- **Oracle coverage.** setlist.fm's three most recent setlists per artist are an
  imperfect ground truth; it undercredits the web arms (see finding 4) and, via
  tribute matching, is actively wrong for some names (finding 3).
- **Groundedness vs. format.** Many raw failures are the two-to-three-sentence
  rule, which is a formatting miss, not a trust miss; the two are reported
  separately above.
- **Cost.** `claude-web`'s cost is tokens plus the per-search fee; if Anthropic
  bills its code-execution filtering separately, the true figure is a little
  higher, which widens the gap rather than narrowing it. Perplexity's cost is
  its own reported all-in figure.
- **Cohere trial key** rate-limits at 20 calls/min; the arm backs off and retries.
