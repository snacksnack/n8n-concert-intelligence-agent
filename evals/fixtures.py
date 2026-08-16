"""Setlist inputs for the preview prompt (RC1-258).

Three cases, chosen for the branches the builder actually has rather than for
variety:

* **headliner** — several shows, a stable core set plus an encore. The ordinary
  path.
* **opener** — the same shape with `headliner_name` set, which appends the
  "expect a shorter set (30-45 min)" note. The note is the only thing that
  changes, so it is the only thing the case is scored on.
* **no-setlists** — the branch with a *known exact answer*. The builder asks for
  one specific sentence, so this is the rare golden where the correct output can
  be compared literally instead of characterised.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fixture:
    id: str
    artist: str
    shows: list[dict] = field(default_factory=list)
    headliner: str | None = None
    notes: str = ""

    @property
    def songs(self) -> list[str]:
        return [song for show in self.shows for _, songs in show["sets"] for song in songs]


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        id="headliner",
        artist="Beach House",
        notes="Ordinary path: a stable core set across three shows, plus an encore.",
        shows=[
            {
                "date": "2026-06-14",
                "venue": "The Fillmore",
                "sets": [
                    ("Main Set", ["Space Song", "Myth", "Silver Soul", "PPP"]),
                    ("Encore", ["10 Mile Stereo"]),
                ],
            },
            {
                "date": "2026-06-11",
                "venue": "Paramount Theatre",
                "sets": [
                    ("Main Set", ["Space Song", "Myth", "Levitation", "PPP"]),
                    ("Encore", ["10 Mile Stereo"]),
                ],
            },
            {
                "date": "2026-06-08",
                "venue": "Orpheum",
                "sets": [("Main Set", ["Space Song", "Wishes", "Myth", "PPP"])],
            },
        ],
    ),
    Fixture(
        id="opener",
        artist="Hovvdy",
        headliner="Slowdive",
        notes="The opener note is the only difference; it is what this case scores.",
        shows=[
            {
                "date": "2026-05-30",
                "venue": "Bowery Ballroom",
                "sets": [("Main Set", ["Cranberry", "Everything", "True Love"])],
            },
            {
                "date": "2026-05-28",
                "venue": "Union Transfer",
                "sets": [("Main Set", ["Cranberry", "Runner", "True Love"])],
            },
        ],
    ),
    Fixture(
        id="no-setlists",
        artist="Cindy Lee",
        notes=(
            "The branch with an exact expected answer. Also the case that "
            "collides with `Attach Previews`'s failure fallback — see "
            "evals/workflow.py."
        ),
    ),
)

BY_ID: dict[str, Fixture] = {f.id: f for f in FIXTURES}
