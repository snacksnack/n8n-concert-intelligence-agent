"""The five arms of the RC1-505 provider matrix (two controlled experiments).

A flat leaderboard across providers would conflate two independent variables —
the generation *model* and the retrieval *architecture* (trusted structured data
handed to the model vs. the model searching the open web itself). So the arms
are split into two seats:

* **generation** (Exp 1): given the same setlist.fm data, write the preview.
  Only the model varies. Arms: `claude-gen`, `cohere-gen`, `sonar-gen`.
* **grounded-web** (Exp 2): retrieve from the open web *and* write the preview,
  in one call. Arms: `claude-web`, `sonar-web`. The task is identical; the
  retrieval is what differs, which is the point.

Claude and Sonar each appear in both seats (search off / on), which isolates
what open-web search adds and costs for the *same* model. Cohere is
generation-only by necessity: its managed web-search connector was deprecated
in Sept 2025.

Every arm goes through its provider's **official SDK** (Anthropic, Cohere,
Perplexity). For a measurement instrument the SDK owning the response-shape
parsing matters: a hand-rolled JSON walk that silently mis-reads a renamed field
would corrupt the score rather than fail. The SDK versions are pinned in
`requirements.txt` for the same reason the harness itself is pinned.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import cohere
from perplexity import Perplexity

from evals import pricing, workflow

SEAT_GENERATION = "generation"
SEAT_GROUNDED = "grounded-web"

#: Anthropic Messages web-search tool. TODO(RC1-505): confirm this version
#: string against the live tool-use docs before the arm-4 run — a stale version
#: is rejected at the API, which is a loud, early failure rather than a silent one.
ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20250305"


@dataclass(frozen=True)
class ArmResponse:
    """One arm's output for one case, with what it consumed.

    `reported_cost_usd` is the provider's own all-in figure when it returns one
    (Perplexity's Agent API does, search fees included); it is preferred over the
    local estimate so the authoritative number wins when available.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    citations: tuple[str, ...] = ()
    reported_cost_usd: Decimal | None = None


_GIVEN_CLAUSE = "Given these recent setlists for ${artist.artist_name}"
_RESEARCH_CLAUSE = "Research ${artist.artist_name}'s most recent live shows on the web"


def grounded_task_prompt(artist_name: str, *, headliner: str | None = None) -> str:
    """The grounded-web task, *derived* from the workflow's own preview prompt.

    Exp 2 isolates one variable — where the setlists come from — so the task
    must be word-for-word the generation prompt except that clause. Rather than
    keep a near-copy that could drift, this reuses `workflow.preview_template()`
    (and its opener note), swaps "Given these recent setlists" for "Research …
    on the web", and drops the supplied-setlists block the web arm must retrieve
    for itself. Deliberately it does *not* add a "quote the titles" instruction
    the generation prompt lacks — the scorers only check quoted titles either
    way, and an extra nudge on one seat would be the apples-to-oranges we are
    avoiding.

    A changed source clause raises rather than silently leaving the swap undone.
    """
    template = workflow.preview_template()
    instructions = template.split("Recent setlists:")[0]
    if _GIVEN_CLAUSE not in instructions:
        raise ValueError(
            "the preview prompt's setlist-source clause changed; update "
            "arms._GIVEN_CLAUSE so the grounded task stays word-for-word aligned"
        )
    instructions = instructions.replace(_GIVEN_CLAUSE, _RESEARCH_CLAUSE)
    opener = workflow.opener_template() if headliner else ""
    out = instructions.replace("${openerNote}", opener)
    out = out.replace("${artist.artist_name}", artist_name)
    if headliner:
        out = out.replace("${artist.headliner_name}", headliner)
    return out.replace("\\n", "\n").strip()


@dataclass(frozen=True)
class Arm:
    """A provider/model/retrieval combination the matrix runs and scores."""

    name: str
    seat: str
    provider: str
    model: str
    experiments: tuple[int, ...]

    def generate(self, prompt: str) -> ArmResponse:  # pragma: no cover - overridden
        raise NotImplementedError

    def cost(self, response: ArmResponse) -> Decimal:
        if response.reported_cost_usd is not None:
            return response.reported_cost_usd
        return pricing.call_cost(
            self.provider,
            self.model,
            response.input_tokens,
            response.output_tokens,
            response.searches,
        )


@dataclass(frozen=True)
class AnthropicArm(Arm):
    """Claude via the Messages API, with the web_search tool on for the web seat."""

    client: object = None
    max_tokens: int = 300
    max_searches: int = 3

    def generate(self, prompt: str) -> ArmResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.seat == SEAT_GROUNDED:
            kwargs["tools"] = [
                {
                    "type": ANTHROPIC_WEB_SEARCH_TOOL,
                    "name": "web_search",
                    "max_uses": self.max_searches,
                }
            ]
        response = self.client.messages.create(**kwargs)  # type: ignore[union-attr]
        blocks = list(response.content)
        text = "".join(b.text for b in blocks if getattr(b, "type", None) == "text")
        citations: list[str] = []
        for block in blocks:
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if url:
                    citations.append(url)
        searches = 0
        server_tool_use = getattr(response.usage, "server_tool_use", None)
        if server_tool_use is not None:
            searches = getattr(server_tool_use, "web_search_requests", 0) or 0
        if not searches and self.seat == SEAT_GROUNDED:
            searches = sum(
                1 for b in blocks if getattr(b, "type", None) == "web_search_tool_result"
            )
        return ArmResponse(
            text=text,
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            searches=searches,
            citations=tuple(dict.fromkeys(citations)),
        )


@dataclass(frozen=True)
class CohereArm(Arm):
    """Cohere Command via the v2 chat endpoint (official SDK). Generation only."""

    client: object = None

    def generate(self, prompt: str) -> ArmResponse:
        response = self.client.chat(  # type: ignore[union-attr]
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = getattr(response.message, "content", None) or []
        text = "".join(
            getattr(p, "text", "") for p in parts if getattr(p, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)
        units = getattr(usage, "billed_units", None) or getattr(usage, "tokens", None)
        return ArmResponse(
            text=text,
            input_tokens=int(getattr(units, "input_tokens", 0) or 0),
            output_tokens=int(getattr(units, "output_tokens", 0) or 0),
        )


@dataclass(frozen=True)
class PerplexityArm(Arm):
    """Perplexity Sonar via the Agent API (official SDK), web_search on the web seat."""

    client: object = None

    def generate(self, prompt: str) -> ArmResponse:
        kwargs: dict = {"model": self.model, "input": prompt}
        if self.seat == SEAT_GROUNDED:
            kwargs["tools"] = [{"type": "web_search"}]
        response = self.client.responses.create(**kwargs)  # type: ignore[union-attr]
        citations: list[str] = []
        searches = 0
        for item in getattr(response, "output", None) or []:
            kind = getattr(item, "type", None)
            if kind == "search_results":
                queries = getattr(item, "queries", None) or []
                results = getattr(item, "results", None) or []
                searches = len(queries) or (1 if results else 0)
            elif kind == "message":
                for part in getattr(item, "content", None) or []:
                    for annotation in getattr(part, "annotations", None) or []:
                        url = getattr(annotation, "url", None)
                        if getattr(annotation, "type", None) == "url_citation" and url:
                            citations.append(url)
        usage = getattr(response, "usage", None)
        total_cost = getattr(getattr(usage, "cost", None), "total_cost", None)
        return ArmResponse(
            text=getattr(response, "output_text", "") or "",
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            searches=searches,
            citations=tuple(dict.fromkeys(citations)),
            reported_cost_usd=None if total_cost is None else Decimal(str(total_cost)),
        )


def build_arms(
    *,
    anthropic_client: object | None = None,
    cohere_key: str | None = None,
    perplexity_key: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
    max_searches: int = 3,
    cohere_model: str = "command-a-03-2025",
    perplexity_model: str = "perplexity/sonar",
) -> dict[str, Arm]:
    """Every arm whose credential is available, keyed by name.

    An arm whose key is missing is simply omitted — the matrix runner reports it
    as skipped, so a partial run (e.g. before the Perplexity balance is loaded)
    is explicit rather than silent.
    """
    arms: dict[str, Arm] = {}
    if anthropic_client is not None:
        arms["claude-gen"] = AnthropicArm(
            name="claude-gen",
            seat=SEAT_GENERATION,
            provider="anthropic",
            model=model,
            experiments=(1, 2),
            client=anthropic_client,
            max_tokens=max_tokens,
        )
        arms["claude-web"] = AnthropicArm(
            name="claude-web",
            seat=SEAT_GROUNDED,
            provider="anthropic",
            model=model,
            experiments=(2,),
            client=anthropic_client,
            max_tokens=max_tokens,
            max_searches=max_searches,
        )
    if cohere_key:
        arms["cohere-gen"] = CohereArm(
            name="cohere-gen",
            seat=SEAT_GENERATION,
            provider="cohere",
            model=cohere_model,
            experiments=(1,),
            client=cohere.ClientV2(api_key=cohere_key),
        )
    if perplexity_key:
        arms["sonar-gen"] = PerplexityArm(
            name="sonar-gen",
            seat=SEAT_GENERATION,
            provider="perplexity",
            model=perplexity_model,
            experiments=(1,),
            client=Perplexity(api_key=perplexity_key),
        )
        arms["sonar-web"] = PerplexityArm(
            name="sonar-web",
            seat=SEAT_GROUNDED,
            provider="perplexity",
            model=perplexity_model,
            experiments=(2,),
            client=Perplexity(api_key=perplexity_key),
        )
    return arms
