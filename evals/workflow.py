"""Read the prompt out of the committed workflow JSON (RC1-258).

Sibling of the same module in `n8n-stakeholder-status-email`, and deliberately
not shared with it. The two workflows assemble their prompts differently — that
one interpolates `{{ $json.x }}` inside the httpRequest body, this one builds a
template literal in a `Build Prompt` Code node — so the extraction is genuinely
repo-specific. What they share is `agent-evals`, which is the part that was
worth extracting.

## The contract here is positional, not textual

`Parse Email Content` in the other repo regex-extracts a shape. Nothing parses
here. Instead `Attach Previews` does:

    const prompts = $('Build Prompt').all();
    const claudeOut = $input.all();
    for (let i = 0; i < prompts.length; i++) {
      const aid = prompts[i].json.artist_id;
      const text = claudeOut[i]?.json?.content?.[0]?.text || 'No preview available.';
      ...
    }

**Prompt `i` is assumed to correspond to response `i`.** If the request node
ever reorders, drops or retries an item, a preview is attached to the wrong
artist — and it reads perfectly, because a plausible concert preview under the
wrong band name looks like content rather than a bug. That is the failure worth
freezing, and `tests/test_contract.py` asserts the alignment assumption is still
what the node makes.

The second thing nothing checks: `content[0].text` falls back to the string
`'No preview available.'`, which is also what a *legitimately* empty setlist
produces downstream. So a silent API failure and a genuine absence of data are
indistinguishable in the email. Recorded rather than fixed — it is a finding
about the workflow, not about the eval.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / "workflows" / "concert-intelligence-agent.json"
)

PROMPT_NODE = "Build Prompt"
REQUEST_NODE = "Claude Request"
CONSUMER_NODE = "Attach Previews"


@cache
def workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text())


@cache
def node(name: str) -> dict:
    for candidate in workflow().get("nodes", []):
        if candidate.get("name") == name:
            return candidate
    raise KeyError(
        f"no node named {name!r} in {WORKFLOW_PATH.name}. The workflow was renamed or "
        "restructured — update the eval rather than deleting the assertion."
    )


@cache
def builder_code() -> str:
    return str(node(PROMPT_NODE)["parameters"]["jsCode"])


@cache
def consumer_code() -> str:
    return str(node(CONSUMER_NODE)["parameters"]["jsCode"])


@cache
def request_body() -> str:
    return str(node(REQUEST_NODE)["parameters"]["jsonBody"])


@cache
def model() -> str:
    match = re.search(r'model:\s*"([^"]+)"', request_body())
    return match.group(1) if match else ""


@cache
def max_tokens() -> int:
    match = re.search(r"max_tokens:\s*(\d+)", request_body())
    return int(match.group(1)) if match else 0


# --- the two prompt branches, read from the builder's source --------------
#
# `Build Prompt` has an if/else: no setlists produces a one-sentence canned
# request, anything else produces the preview prompt. Both templates are pulled
# out of the JavaScript rather than copied here, so an edit to either shows up
# as a failing test instead of a stale duplicate.

_EMPTY_TEMPLATE = re.compile(r"prompt = `(Write exactly this sentence[^`]*)`", re.DOTALL)
_PREVIEW_TEMPLATE = re.compile(r"prompt = `(You are a concert preview writer[^`]*)`", re.DOTALL)
_OPENER_TEMPLATE = re.compile(r"\?\s*`(\\nNote: this artist is opening[^`]*)`", re.DOTALL)


@cache
def empty_template() -> str:
    match = _EMPTY_TEMPLATE.search(builder_code())
    if match is None:
        raise ValueError(
            "the no-setlist branch of Build Prompt no longer starts with "
            "'Write exactly this sentence' — the exact-output golden cannot speak for it"
        )
    return match.group(1)


@cache
def preview_template() -> str:
    match = _PREVIEW_TEMPLATE.search(builder_code())
    if match is None:
        raise ValueError("the preview branch of Build Prompt was restructured")
    return match.group(1)


@cache
def opener_template() -> str:
    match = _OPENER_TEMPLATE.search(builder_code())
    return match.group(1) if match else ""


def _substitute(template: str, values: dict[str, str]) -> str:
    """Fill `${expr}` slots by matching on the expression text.

    The builder uses JavaScript template literals, so the slots are expressions
    rather than names: `${artist.artist_name}`, `${formatted}`. Substituting by
    the literal expression keeps this honest — a renamed variable in the node
    raises here instead of silently leaving `${...}` in the prompt.
    """
    out = template
    for expr, value in values.items():
        slot = "${" + expr + "}"
        if slot not in out:
            raise KeyError(f"{slot} is not in the template; Build Prompt changed")
        out = out.replace(slot, value)
    leftover = re.findall(r"\$\{[^}]+\}", out)
    if leftover:
        raise ValueError(f"unfilled slots left in the prompt: {leftover}")
    return out.replace("\\n", "\n")


def build_empty_prompt(artist_name: str) -> str:
    return _substitute(empty_template(), {"artist.artist_name": artist_name})


def expected_empty_sentence(artist_name: str) -> str:
    """The exact sentence the no-setlist branch demands.

    Read out of the prompt itself: it is quoted inside the instruction, so the
    expected output and the instruction cannot drift apart.
    """
    prompt = build_empty_prompt(artist_name)
    match = re.search(r'"([^"]+)"', prompt)
    if match is None:
        raise ValueError("the no-setlist prompt no longer quotes the sentence it demands")
    return match.group(1)


def format_setlists(shows: list[dict]) -> str:
    """Reproduce the builder's setlist formatting.

    Duplicated logic, and checked rather than trusted: `test_contract.py`
    asserts the shipped JavaScript still produces the same shape (a `Show N
    (date, venue):` header and indented `SetName: songs` lines).
    """
    blocks = []
    for index, show in enumerate(shows, start=1):
        header = f"Show {index} ({show['date']}, {show['venue']}):"
        lines = [f"  {name}: {', '.join(songs)}" for name, songs in show["sets"]]
        blocks.append(header + "\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def build_preview_prompt(
    artist_name: str, shows: list[dict], *, headliner: str | None = None
) -> str:
    opener_note = ""
    if headliner:
        opener_note = _substitute(
            opener_template(), {"artist.headliner_name": headliner}
        )
    return _substitute(
        preview_template(),
        {
            "artist.artist_name": artist_name,
            "openerNote": opener_note,
            "formatted": format_setlists(shows),
        },
    )
