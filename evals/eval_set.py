"""The RC1-505 eval set: ~20 real artists with recent setlists frozen as the
shared oracle every arm is graded against.

Two steps, split on purpose so the network half can be reviewed before it runs
and the selection stays reproducible:

* ``sample`` (no key) — deterministically pick N artists from the gitignored
  ``qa/artists.json`` and write ``qa/eval-set-artists.json``: the reproducible
  selection, **id + name + genres only** (ranks and play counts stay out, so no
  listening history leaves the gitignore boundary).
* ``fetch`` (needs ``SETLIST_FM_API_KEY``) — pull each artist's recent setlists
  from setlist.fm and freeze them in ``qa/eval-setlists.json`` in the Fixture
  shape, keeping only artists with real setlist data. That file is the oracle:
  the generation seat is handed it, the grounded-web seat is graded against it.

``load`` turns the frozen file into ``fixtures.Fixture`` objects for the matrix.
Over-sampling (25 to keep ~20) absorbs artists setlist.fm has no data for.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from evals.fixtures import Fixture

QA_DIR = Path(__file__).resolve().parents[1] / "qa"
ARTISTS_PATH = QA_DIR / "artists.json"
SELECTION_PATH = QA_DIR / "eval-set-artists.json"
ORACLE_PATH = QA_DIR / "eval-setlists.json"

#: Cohorts for honest analysis (see RC1-505). setlist.fm matches some artist
#: names to tribute/cover acts or long-inactive originals, so grading every arm
#: against that data is only clean for currently-touring acts. The headline
#: metrics run on `active`; `tribute` and `stale` are the trusted-source
#: failure-mode exhibit (does an arm write a confident preview of a 2026 "show"
#: by a band that broke up decades ago, or catch it?). Editable by hand.
COHORTS: dict[str, str] = {
    "the-smiths": "tribute",  # split 1987; 2026 Sony Hall date is a tribute act
    "david-bowie": "tribute",  # d. 2016
    "tom-petty": "tribute",  # d. 2017; 2026 "Rancho Victoria Vineyard"
    "r-e-m": "tribute",  # split 2011; 2026 brewery gig
    "inxs": "tribute",  # original lineup defunct
    "taylor-swift": "tribute",  # "Ross House", 25 songs — a covers act, not her
    "polvo": "stale",  # real, but newest setlist is 2011
    "yazoo": "stale",  # real, but newest setlist is 2011
}


def cohort(fixture_id: str) -> str:
    """Analysis cohort for an eval-set artist; `active` unless tagged otherwise."""
    return COHORTS.get(fixture_id, "active")


SEED = 1505  # RC1-505; fixed so the selection is reproducible
DEFAULT_N = 25  # over-sample; fetch keeps the artists with real setlist data
MAX_SHOWS = 3  # recent non-empty setlists kept per artist
SETLIST_SEARCH_URL = "https://api.setlist.fm/rest/1.0/search/setlists"
_RATE_SLEEP = 1.1  # setlist.fm free tier rate-limits aggressively; pace below 1 req/s
_MAX_RETRIES = 5
_HTTP_TIMEOUT = 30.0


class _RateLimited(Exception):
    """A 429 from setlist.fm, carrying any Retry-After hint (seconds)."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "artist"


def sample(n: int = DEFAULT_N, seed: int = SEED) -> list[dict]:
    """Deterministically pick `n` artists and write the selection file."""
    artists = json.loads(ARTISTS_PATH.read_text())
    ordered = sorted(artists, key=lambda a: a["id"])  # stable base order
    picks = random.Random(seed).sample(ordered, min(n, len(ordered)))
    selection = [
        {"id": a["id"], "name": a["name"], "genres": a.get("genres", [])} for a in picks
    ]
    selection.sort(key=lambda a: a["name"].lower())
    SELECTION_PATH.write_text(json.dumps(selection, indent=2) + "\n")
    return selection


def _get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https request to {url!r}")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            hint = exc.headers.get("Retry-After", "")
            raise _RateLimited(float(hint) if hint.isdigit() else 0.0) from exc
        raise


def _shows_for(artist_name: str, api_key: str) -> list[dict]:
    """Recent non-empty setlists for one artist, newest first, in Fixture shape."""
    query = urllib.parse.urlencode({"artistName": artist_name, "p": 1})
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    data: dict = {}
    for attempt in range(_MAX_RETRIES):
        try:
            data = _get_json(f"{SETLIST_SEARCH_URL}?{query}", headers)
            break
        except _RateLimited as exc:
            time.sleep(max(exc.retry_after, 2.0**attempt))  # honor Retry-After, else backoff
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # setlist.fm returns 404 for "nothing found"
                return []
            raise
    else:
        raise RuntimeError(f"still rate-limited after {_MAX_RETRIES} retries")
    shows: list[dict] = []
    for setlist in data.get("setlist", []):
        sets = []
        for block in (setlist.get("sets") or {}).get("set", []) or []:
            songs = [s["name"] for s in block.get("song", []) if s.get("name")]
            if not songs:
                continue
            label = "Encore" if block.get("encore") else (block.get("name") or "Main Set")
            sets.append((label, songs))
        if not sets:
            continue
        shows.append(
            {
                "date": setlist.get("eventDate", ""),
                "venue": (setlist.get("venue") or {}).get("name") or "unknown venue",
                "sets": sets,
            }
        )
        if len(shows) >= MAX_SHOWS:
            break
    return shows


def fetch(api_key: str | None = None) -> list[dict]:
    """Freeze the selected artists' setlists from setlist.fm into the oracle file."""
    api_key = api_key or os.environ.get("SETLIST_FM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "SETLIST_FM_API_KEY is not set; it is needed once, to freeze the oracle"
        )
    if not SELECTION_PATH.exists():
        raise RuntimeError(f"{SELECTION_PATH.name} missing — run `sample` first")
    selection = json.loads(SELECTION_PATH.read_text())
    frozen: list[dict] = []
    for artist in selection:
        try:
            shows = _shows_for(artist["name"], api_key)
        except (RuntimeError, urllib.error.URLError) as exc:  # skip, don't lose the run
            print(f"  {artist['name']}: error ({exc}) — skipped")
            continue
        print(f"  {artist['name']}: {len(shows)} show(s)" + ("" if shows else " — dropped"))
        if shows:
            frozen.append(
                {
                    "id": _slug(artist["name"]),
                    "artist": artist["name"],
                    "genres": artist.get("genres", []),
                    "shows": shows,
                }
            )
        time.sleep(_RATE_SLEEP)
    ORACLE_PATH.write_text(json.dumps(frozen, indent=2) + "\n")
    print(f"\nfroze {len(frozen)}/{len(selection)} artists to {ORACLE_PATH.name}")
    return frozen


def load() -> tuple[Fixture, ...]:
    """The frozen oracle as Fixture objects, or empty if it has not been fetched."""
    if not ORACLE_PATH.exists():
        return ()
    raw = json.loads(ORACLE_PATH.read_text())
    out = []
    for artist in raw:
        shows = [
            {"date": s["date"], "venue": s["venue"], "sets": [(n, songs) for n, songs in s["sets"]]}
            for s in artist["shows"]
        ]
        out.append(Fixture(id=artist["id"], artist=artist["artist"], shows=shows))
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.eval_set", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sampler = sub.add_parser("sample", help="pick artists (no key)")
    sampler.add_argument("--n", type=int, default=DEFAULT_N)
    sampler.add_argument("--seed", type=int, default=SEED)
    sub.add_parser("fetch", help="freeze setlists from setlist.fm (needs the key)")
    sub.add_parser("show", help="summarize the frozen oracle")
    args = parser.parse_args(argv)

    if args.cmd == "sample":
        selection = sample(args.n, args.seed)
        print(f"selected {len(selection)} artists -> {SELECTION_PATH.name}")
        for artist in selection:
            print(f"  {artist['name']}  ({', '.join(artist['genres'][:2])})")
        return 0
    if args.cmd == "fetch":
        fetch()
        return 0
    fixtures = load()
    print(f"{len(fixtures)} artist(s) in the oracle")
    for fixture in fixtures:
        print(f"  {fixture.artist}: {len(fixture.songs)} song(s) over {len(fixture.shows)} show(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
