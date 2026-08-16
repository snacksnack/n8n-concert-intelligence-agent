"""The layer-2 scorers, checked in both directions (RC1-258).

Free. Layer 2 is billed and stays out of CI, but its scoring is ordinary code
and the place mistakes live — this one produced a false positive on its first
real run, so it has a test proving both what it now tolerates and what it still
catches.
"""

from __future__ import annotations

import pytest

from evals import fixtures, subject, workflow

HEADLINER = fixtures.BY_ID["headliner"]
OPENER = fixtures.BY_ID["opener"]
EMPTY = fixtures.BY_ID["no-setlists"]


def test_punctuation_inside_quotes_is_not_an_invented_song():
    """The false positive from the first real run.

    The model wrote: anchoring sets with "Myth" and "PPP," so expect... —
    American-style punctuation inside the quotes. Every title was real; the
    regex had captured the comma.
    """
    real = 'Anchoring sets with "Myth" and "PPP," with "Space Song" opening.'
    assert subject._real_songs(real, HEADLINER).passed


def test_an_apostrophe_does_not_delimit_a_song_title():
    """The second false positive from a real run.

    "don't duck out before the encore" had the span between two apostrophes
    read as a quoted title. An apostrophe is part of a word.
    """
    real = "Don't duck out before the encore — they've been closing with \"10 Mile Stereo\"."
    assert subject._real_songs(real, HEADLINER).passed


def test_a_genuinely_invented_song_is_still_caught():
    fake = 'Expect "Space Song" and the unreleased "Chandelier Dreams" to close.'
    result = subject._real_songs(fake, HEADLINER)
    assert not result.passed
    assert "Chandelier Dreams" in result.detail


def test_a_preamble_is_caught_but_a_normal_opening_is_not():
    assert subject._no_preamble("Beach House opens with 'Space Song'.").passed
    assert subject._no_preamble("Expect a shorter set built around 'Cranberry'.").passed

    for preamble in (
        "Here's a preview of what to expect:",
        "Sure! Beach House will open with...",
        "Based on these setlists, expect...",
    ):
        result = subject._no_preamble(preamble)
        assert not result.passed, f"{preamble!r} should be caught"
        assert "ships verbatim" in result.detail


def test_sentence_count_bounds_both_ends():
    assert subject._sentence_count("One sentence here. And a second one too.").passed
    assert not subject._sentence_count("Only a single sentence about the band.").passed
    long = " ".join(f"This is sentence number {i} about the band." for i in range(6))
    assert not subject._sentence_count(long).passed


def test_the_opener_note_check_reads_synonyms_and_separators():
    for text in (
        "Expect a shorter set as they open for Slowdive.",
        "As support, they play a 30-45 minute slot.",
        "A brief opening set of about 30 to 45 minutes.",
    ):
        assert subject._opener_note(text).passed, f"{text!r} acknowledges the slot"

    result = subject._opener_note("A career-spanning two-hour headline performance.")
    assert not result.passed


def test_the_exact_sentence_case_is_compared_literally():
    expected = workflow.expected_empty_sentence(EMPTY.artist)
    assert subject._exact_sentence(expected, EMPTY).passed
    assert subject._exact_sentence(f'"{expected}"', EMPTY).passed, "echoed quotes still comply"

    result = subject._exact_sentence("There is no setlist data for this artist.", EMPTY)
    assert not result.passed
    assert "expected" in result.detail


@pytest.mark.parametrize("case", subject.CASES, ids=lambda c: c.id)
def test_each_case_expects_what_its_branch_implies(case):
    fixture = fixtures.BY_ID[case.id]
    if not fixture.shows:
        assert case.expect == ("says-exactly-the-demanded-sentence",)
    else:
        assert "starts-with-the-preview-itself" in case.expect
        assert ("mentions-the-shorter-opening-set" in case.expect) == bool(fixture.headliner)


def test_the_subject_version_separates_the_prompt_from_the_workflow():
    version = subject.version()
    assert version.prompt_version.startswith("prompt-sha256:")
    assert version.code_version.startswith("workflow-sha256:")
    assert version.model == workflow.model()
