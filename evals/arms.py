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

Anthropic goes through its SDK (already a dependency); Cohere and Perplexity go
through stdlib `urllib` rather than their SDKs — one fewer pinned version to
drift under a measurement, and the request/response shapes are small and frozen
here deliberately.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from evals import pricing, workflow

SEAT_GENERATION = "generation"
SEAT_GROUNDED = "grounded-web"

#: Anthropic Messages web-search tool. TODO(RC1-505): confirm this version
#: string against the live tool-use docs before the arm-4 run — a stale version
#: is rejected at the API, which is a loud, early failure rather than a silent one.
ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20250305"

_COHERE_CHAT_URL = "https://api.cohere.com/v2/chat"
_PERPLEXITY_AGENT_URL = "https://api.perplexity.ai/v1/agent"
_HTTP_TIMEOUT = 90.0


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
    raw: dict | None = None


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


def _post_json(url: str, body: dict, headers: dict[str, str]) -> dict:
    """POST `body` as JSON and parse the JSON response, or raise with the body.

    Keeps the error text visible: a 4xx from these providers carries the reason
    (bad model id, deprecated tool, empty balance), and swallowing it would turn
    a one-line fix into a guessing game.
    """
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — https literals only, below
        url,
        data=payload,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https request to {url!r}")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {detail}") from exc


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
    """Cohere Command via the v2 chat endpoint. Generation seat only."""

    api_key: str = ""

    def generate(self, prompt: str) -> ArmResponse:
        data = _post_json(
            _COHERE_CHAT_URL,
            {"model": self.model, "messages": [{"role": "user", "content": prompt}]},
            {"Authorization": f"Bearer {self.api_key}"},
        )
        parts = (data.get("message") or {}).get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        usage = data.get("usage") or {}
        tokens = usage.get("tokens") or usage.get("billed_units") or {}
        return ArmResponse(
            text=text,
            input_tokens=int(tokens.get("input_tokens") or 0),
            output_tokens=int(tokens.get("output_tokens") or 0),
            raw=data,
        )


@dataclass(frozen=True)
class PerplexityArm(Arm):
    """Perplexity Sonar via the Agent API, web_search on for the grounded seat."""

    api_key: str = ""

    def generate(self, prompt: str) -> ArmResponse:
        body: dict = {"model": self.model, "input": prompt}
        if self.seat == SEAT_GROUNDED:
            body["tools"] = [{"type": "web_search"}]
        data = _post_json(
            _PERPLEXITY_AGENT_URL, body, {"Authorization": f"Bearer {self.api_key}"}
        )
        text = ""
        citations: list[str] = []
        searches = 0
        for item in data.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        text += part.get("text", "")
                    for annotation in part.get("annotations") or []:
                        if annotation.get("type") == "url_citation" and annotation.get("url"):
                            citations.append(annotation["url"])
            elif kind == "search_results":
                queries = item.get("queries") or []
                results = item.get("results") or []
                searches = len(queries) or (1 if results else 0)
        usage = data.get("usage") or {}
        reported = (usage.get("cost") or {}).get("total_cost")
        return ArmResponse(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            searches=searches,
            citations=tuple(dict.fromkeys(citations)),
            reported_cost_usd=None if reported is None else Decimal(str(reported)),
            raw=data,
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
            api_key=cohere_key,
        )
    if perplexity_key:
        arms["sonar-gen"] = PerplexityArm(
            name="sonar-gen",
            seat=SEAT_GENERATION,
            provider="perplexity",
            model=perplexity_model,
            experiments=(1,),
            api_key=perplexity_key,
        )
        arms["sonar-web"] = PerplexityArm(
            name="sonar-web",
            seat=SEAT_GROUNDED,
            provider="perplexity",
            model=perplexity_model,
            experiments=(2,),
            api_key=perplexity_key,
        )
    return arms
