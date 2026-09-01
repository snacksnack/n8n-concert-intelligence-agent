"""Layer 1: what the workflow assumes about its own prompt (RC1-258).

Free, credential-free, runs on every push. Read out of the committed workflow
JSON at test time — a test asserting against a copy of the prompt passes
forever, including after someone edits the real one.

Nothing parses the model's output in this workflow, so there is no regex
contract to check the way the stakeholder-email repo has. The assumptions worth
freezing here are different, and two of them are load-bearing and undeclared:
the positional alignment in `Attach Previews`, and the fact that its failure
fallback is indistinguishable from a legitimate result.
"""

from __future__ import annotations

import re

import pytest

from evals import fixtures, workflow

# --- both branches of Build Prompt still exist ----------------------------


def test_the_no_setlist_branch_demands_one_exact_sentence():
    """The rare case where the correct output is known literally.

    Everything else in this harness characterises output; this branch specifies
    it, so the golden can compare exactly — but only while the prompt keeps
    quoting the sentence it wants.
    """
    prompt = workflow.build_empty_prompt("Cindy Lee")
    assert "exactly this sentence" in prompt
    assert workflow.expected_empty_sentence("Cindy Lee") == (
        "No recent setlist data available for Cindy Lee."
    )


def test_the_preview_branch_forbids_a_preamble():
    """Nothing parses the output — it is written straight into the email and
    the Notion page. So "Here's a preview of what to expect:" would ship
    verbatim to a reader, which is why the prompt forbids it and why this is
    asserted rather than assumed."""
    prompt = workflow.preview_template()
    assert "no preamble" in prompt.lower()
    assert "output only" in prompt.lower()


def test_the_preview_branch_still_asks_for_two_to_three_sentences():
    assert "2-3 sentence" in workflow.preview_template()


def test_the_opener_note_is_conditional_and_mentions_a_shorter_set():
    with_note = workflow.build_preview_prompt(
        "Hovvdy", fixtures.BY_ID["opener"].shows, headliner="Slowdive"
    )
    without = workflow.build_preview_prompt("Hovvdy", fixtures.BY_ID["opener"].shows)
    assert "opening for Slowdive" in with_note
    assert "shorter set" in with_note
    assert "opening for" not in without, "a headliner must not get the opener note"


def test_the_setlist_formatting_matches_the_shipped_builder():
    """`format_setlists` duplicates JavaScript; the duplication is checked.

    The shipped node builds `Show N (date, venue):` headers and indents each set
    as `  SetName: songs`. If that changes, the prompts this eval sends stop
    resembling the prompts the workflow sends.
    """
    code = workflow.builder_code()
    assert "Show ${i + 1} (${date}, ${venue})" in code
    assert "`  ${setName}: " in code

    rendered = workflow.format_setlists(fixtures.BY_ID["headliner"].shows)
    assert rendered.startswith("Show 1 (2026-06-14, The Fillmore):")
    assert "  Main Set: Space Song, Myth, Silver Soul, PPP" in rendered
    assert "  Encore: 10 Mile Stereo" in rendered


def test_every_fixture_builds_a_prompt_with_no_unfilled_slots():
    for fixture in fixtures.FIXTURES:
        prompt = (
            workflow.build_empty_prompt(fixture.artist)
            if not fixture.shows
            else workflow.build_preview_prompt(
                fixture.artist, fixture.shows, headliner=fixture.headliner
            )
        )
        assert "${" not in prompt, f"{fixture.id} left a template slot unfilled"
        assert fixture.artist in prompt


# --- the undeclared assumptions in Attach Previews ------------------------


def test_previews_are_matched_to_artists_by_position():
    """The riskiest assumption in the workflow, frozen so a change is visible.

    `Attach Previews` walks `Build Prompt`'s items by index and reads the
    Claude response at the same index. If the request node ever reorders, drops
    or retries an item, a preview lands on the wrong artist — and it reads
    perfectly, because a plausible preview under the wrong band name looks like
    content rather than a bug.

    This test does not fix that. It makes the assumption explicit, so anyone
    editing the node knows what they are relying on.
    """
    code = workflow.consumer_code()
    assert "for (let i = 0; i < prompts.length; i++)" in code, (
        "the index-walk changed; check whether previews are still aligned to artists"
    )
    assert "prompts[i].json.artist_id" in code
    assert "claudeOut[i]" in code


def test_the_failure_fallback_is_indistinguishable_from_a_real_result():
    """A finding about the workflow, recorded rather than fixed.

    `Attach Previews` substitutes 'No preview available.' when the response has
    no text — an API failure, a truncated response, a misaligned index. The
    no-setlist branch legitimately produces a very similar sentence. So in the
    delivered email, "we could not reach the model" and "this artist has no
    recent setlists" look the same to a reader.

    Asserted so the collision is on the record; changing it is a workflow
    decision, not an eval one.
    """
    code = workflow.consumer_code()
    assert "'No preview available.'" in code
    legitimate = workflow.expected_empty_sentence("Cindy Lee")
    assert legitimate.startswith("No recent setlist data available"), (
        "the two sentences differ in wording but not in what a reader takes from them"
    )


def test_max_tokens_leaves_room_for_the_requested_length():
    """300 tokens against a 2-3 sentence ask.

    Not a tight bound — it is a sanity check that a prompt edit asking for more
    prose has not left the budget behind, which would truncate mid-sentence and
    ship the fragment.
    """
    assert workflow.max_tokens() >= 150, (
        f"max_tokens={workflow.max_tokens()} is tight for a 2-3 sentence preview"
    )


def test_the_request_sends_the_prompt_the_builder_produced():
    body = workflow.request_body()
    assert "$json.prompt" in body, "the request no longer sends Build Prompt's output"
    assert workflow.model(), "no model pinned in the request node"


@pytest.mark.parametrize("fixture", fixtures.FIXTURES, ids=lambda f: f.id)
def test_fixture_songs_appear_in_the_prompt_they_generate(fixture):
    if not fixture.shows:
        pytest.skip("the no-setlist branch sends no songs")
    prompt = workflow.build_preview_prompt(
        fixture.artist, fixture.shows, headliner=fixture.headliner
    )
    for song in fixture.songs:
        assert song in prompt, f"{song!r} is in the fixture but not in the prompt"


def test_the_builder_caps_the_setlists_it_sends():
    """`.slice(0, 5)` bounds the prompt. Worth freezing: an artist with fifty
    recent shows would otherwise build an unbounded prompt."""
    assert re.search(r"\.slice\(0,\s*5\)", workflow.builder_code()), (
        "the setlist cap was removed; prompt size is now unbounded"
    )


# --- the LLM Observability side branch stays off the digest path (RC1-362) --


def test_the_span_branch_hangs_off_the_request_and_not_in_front_of_the_consumer():
    """`Attach Previews` must still read `Claude Request` directly.

    The span nodes are a second output of the request node, not a hop in the
    chain: putting an HTTP node between the request and the consumer would
    replace each item's JSON with Datadog's response, and the index walk in
    `Attach Previews` would then attach 'No preview available.' to every artist
    — perfectly formatted, silently wrong.
    """
    targets = workflow.outputs_of(workflow.REQUEST_NODE)
    assert targets[0] == workflow.CONSUMER_NODE, "Attach Previews is no longer first"
    assert workflow.SPAN_BUILDER_NODE in targets
    assert workflow.outputs_of(workflow.SPAN_BUILDER_NODE) == [workflow.SPAN_REPORT_NODE]
    assert workflow.outputs_of(workflow.SPAN_REPORT_NODE) == [], (
        "the report node feeds something downstream; it must dead-end"
    )


def test_the_span_branch_can_never_block_the_run():
    for name in (workflow.SPAN_BUILDER_NODE, workflow.SPAN_REPORT_NODE):
        assert workflow.node(name).get("onError") == "continueRegularOutput", (
            f"{name} would fail the run on a Datadog error"
        )


def test_the_report_node_posts_to_the_llm_obs_intake_with_the_key_from_vars():
    params = workflow.node(workflow.SPAN_REPORT_NODE)["parameters"]
    assert params["url"].endswith("/api/intake/llm-obs/v1/trace/spans")
    headers = {h["name"]: h["value"] for h in params["headerParameters"]["parameters"]}
    assert headers["DD-API-KEY"] == "={{ $vars.DD_API_KEY }}"
    assert params["jsonBody"] == "={{ JSON.stringify($json) }}"


def test_the_span_builder_reads_the_same_positional_pair_as_the_consumer():
    """Prompt i, response i — the same assumption `Attach Previews` makes, so
    a reorder that misattributes a preview misattributes the span the same way
    and the two stay comparable."""
    code = workflow.span_builder_code()
    assert "$('Build Prompt').all()" in code
    assert "responses[i]" in code and "prompts[i]" in code
    assert "kind: 'llm'" in code
    assert "ml_app: 'concert-intelligence'" in code
    assert "usage?.input_tokens" in code and "usage?.output_tokens" in code
    assert "latency:amortized" in code, "the amortised-latency tag is the honesty marker"


def test_the_builder_stamps_the_time_the_span_needs():
    assert "requestedAt: Date.now()" in workflow.builder_code()
