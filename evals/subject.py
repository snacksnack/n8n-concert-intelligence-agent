"""Layer 2: the preview prompt, run for real and scored (RC1-258).

Builds each fixture's prompt from the *committed* builder source and calls the
Messages API with the model and token budget the workflow pins. No n8n runtime,
no Spotify, no Ticketmaster, no Setlist.fm — and nothing is emailed or written
to Notion.

## Nothing parses this output, which changes what is worth checking

The stakeholder-email workflow regex-extracts a shape, so "does it parse" is the
first question there. Here `Attach Previews` takes the text verbatim and writes
it into the email and the Notion page. So every flaw ships:

* a preamble ("Here's a preview of what to expect:") reads as broken copy
* an invented song name is a factual error about a real band, in a real email
* a length overrun is truncated at 300 tokens, mid-sentence

Those are the four gating characteristics. The no-setlist case is scored
differently and more strictly — its prompt demands one exact sentence, so it is
compared literally rather than characterised.
"""

from __future__ import annotations

import re
import time

from agent_evals.case import Case
from agent_evals.record import CaseResult, CharacteristicResult, SubjectVersion, Usage

from evals import fixtures, workflow

NAME = "concert-preview"

#: Openers a model reaches for when it ignores "no preamble". Matched at the
#: start only — "Expect a shorter set" is a fine *first* sentence, while
#: "Here's what to expect:" is the preamble the prompt forbids.
_PREAMBLE = re.compile(
    r"^\s*(here'?s?\b|this\s+preview|below\s|sure[,!]|certainly[,!]|"
    r"a\s+preview\s+of|preview:|based\s+on\s+(these|the)\s+setlists?[,:])",
    re.IGNORECASE,
)


def _cases() -> tuple[Case, ...]:
    out = []
    for f in fixtures.FIXTURES:
        if not f.shows:
            expect = ("says-exactly-the-demanded-sentence",)
        else:
            expect = (
                "starts-with-the-preview-itself",
                "names-only-real-songs",
                "is-two-to-three-sentences",
            )
            if f.headliner:
                expect += ("mentions-the-shorter-opening-set",)
        out.append(
            Case(id=f.id, input={"fixture": f.id}, expect=expect, tags=("concert-preview",))
        )
    return tuple(out)


CASES: tuple[Case, ...] = _cases()


def preflight(api_key: str | None) -> None:
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Layer 1 (`pytest`) needs no key and covers "
            "the prompt contract; this layer calls the model."
        )


def prompt_version() -> str:
    import hashlib

    material = (workflow.preview_template() + workflow.empty_template()).encode()
    return f"prompt-sha256:{hashlib.sha256(material).hexdigest()[:12]}"


def version() -> SubjectVersion:
    import hashlib

    return SubjectVersion(
        subject=NAME,
        code_version=(
            "workflow-sha256:"
            + hashlib.sha256(workflow.WORKFLOW_PATH.read_bytes()).hexdigest()[:12]
        ),
        model=workflow.model(),
        prompt_version=prompt_version(),
    )


def _no_preamble(text: str) -> CharacteristicResult:
    match = _PREAMBLE.match(text)
    return CharacteristicResult(
        name="starts-with-the-preview-itself",
        passed=match is None,
        detail=(
            "starts directly with the description"
            if match is None
            else f"opens with a preamble: {match.group(0).strip()!r} — this ships verbatim"
        ),
    )


def _real_songs(text: str, fixture: fixtures.Fixture) -> CharacteristicResult:
    """Every quoted title must be one the setlists actually contain.

    Only *quoted* strings are checked, and that is the deliberate limit: song
    titles are ordinary words ("Myth", "Runner", "Everything"), so scanning
    unquoted prose for invented titles would flag normal sentences. A model that
    names a song without quoting it slips through — precision over recall, the
    same trade the planner's groundedness checker makes.

    Two rounds of false positives shaped the matching, both found by running it:

    * **Trailing punctuation is stripped.** The first run flagged 'PPP,',
      'Levitation,' and 'Silver Soul,' as invented. Every one was in the
      setlists; the model had used American-style punctuation inside the quotes.
    * **The apostrophe is not a quote mark.** The second run flagged
      "t duck out before the encore —", captured between the apostrophe in
      "don't" and a later one. Only double and curly quotes delimit a title now;
      an apostrophe is part of a word, which is the same conclusion the
      planner's groundedness checker reached about stripping them.
    """
    quoted = [
        q.strip().strip(",.;:!?").strip()
        for q in re.findall(r"[\"“”]([^\"“”]{2,40})[\"“”]", text)
    ]
    known = {s.lower() for s in fixture.songs}
    invented = [q for q in quoted if q and q.lower() not in known]
    return CharacteristicResult(
        name="names-only-real-songs",
        passed=not invented,
        detail=(
            f"{len(quoted)} quoted title(s), all in the setlists"
            if not invented
            else f"not in any setlist: {', '.join(repr(i) for i in invented[:3])}"
        ),
    )


def _sentence_count(text: str) -> CharacteristicResult:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.split()) > 2]
    ok = 2 <= len(sentences) <= 4
    return CharacteristicResult(
        name="is-two-to-three-sentences",
        passed=ok,
        detail=(
            f"{len(sentences)} sentence(s)"
            + ("" if ok else " — the prompt asks for 2-3, and 300 tokens truncates a long one")
        ),
    )


def _opener_note(text: str) -> CharacteristicResult:
    lowered = re.sub(r"[\s\-–—]+", " ", text.lower())
    signals = ("shorter set", "opening", "opener", "30 45", "30 to 45", "support")
    hit = [s for s in signals if s in lowered]
    return CharacteristicResult(
        name="mentions-the-shorter-opening-set",
        passed=bool(hit),
        detail=(
            f"acknowledges the opening slot ({hit[0]!r})"
            if hit
            else "never mentions the shorter opening set the prompt supplies"
        ),
    )


def _exact_sentence(text: str, fixture: fixtures.Fixture) -> CharacteristicResult:
    """The one branch with a literally known answer.

    Compared after stripping surrounding quotes: the prompt quotes the sentence
    it wants, and a model echoing those quotes has still complied.
    """
    expected = workflow.expected_empty_sentence(fixture.artist)
    got = text.strip().strip('"“”').strip()
    return CharacteristicResult(
        name="says-exactly-the-demanded-sentence",
        passed=got == expected,
        detail=(
            "matches the demanded sentence exactly"
            if got == expected
            else f"expected {expected!r}, got {got[:90]!r}"
        ),
    )


def run(case: Case, client) -> CaseResult:
    fixture = fixtures.BY_ID[case.input["fixture"]]
    prompt = (
        workflow.build_empty_prompt(fixture.artist)
        if not fixture.shows
        else workflow.build_preview_prompt(
            fixture.artist, fixture.shows, headliner=fixture.headliner
        )
    )
    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=workflow.model(),
            max_tokens=workflow.max_tokens(),
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            usage=Usage(latency_ms=(time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )
    latency_ms = (time.perf_counter() - started) * 1000

    if not fixture.shows:
        results = [_exact_sentence(text, fixture)]
    else:
        results = [_no_preamble(text), _real_songs(text, fixture), _sentence_count(text)]
        if fixture.headliner:
            results.append(_opener_note(text))

    return CaseResult(
        case_id=case.id,
        characteristics=results,
        usage=Usage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            latency_ms=latency_ms,
        ),
        observations={
            "artist": fixture.artist,
            "stop_reason": getattr(response, "stop_reason", None),
            "output": text,
        },
    )
