# Concert Intelligence Agent

An n8n workflow that cross-references your Spotify listening history against upcoming concerts, scores each show based on how much you actually listen to the artist, and emails you a digest with AI-generated setlist previews — plus automatic Google Calendar events and a Notion tracker.

---

## Workflow Diagram

```
                              ┌──────────────────────┐
                              │   Schedule Trigger    │
                              │      (8am daily)      │
                              └───────────┬───────────┘
        ┌───────────────────────────┬─────┴─────┬───────────────────────────────┐
        │ (Spotify, parallel)       │           │                               │
        ▼                           ▼           ▼                               ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐      ┌────────────────────────────┐
│ Recently       │  │ Fave Artist    │  │ Fave Artist    │      │ Build Ticketmaster          │
│ Played         │  │ Short / Medium │  │ Long Term      │      │ Search Windows              │
│ (Spotify)      │  │ (Spotify)      │  │ (Spotify)      │      │ (6 monthly date windows)    │
└───────┬────────┘  └───────┬────────┘  └───────┬────────┘      └──────────────┬──────────────┘
        └───────────────────┼───────────────────┘                             ▼
                            ▼                            ┌────────────────────────────────────┐
                  ┌──────────────────┐            ┌────▶ │ Loop Over Ticketmaster Windows       │──┐
                  │ Merge Spotify    │            │      └──────────────┬───────────────────────┘  │ done
                  │ Data             │            │            loop     ▼                           │
                  └────────┬─────────┘            │      ┌──────────────────┐                       │
                           ▼                      │      │ TicketMaster     │                       │
                  ┌──────────────────┐            │      │ Request          │                       │
                  │ Build Artist     │            │      └────────┬─────────┘                       │
                  │ Profile          │ ┄ ref ┄┐   │               ▼                                 │
                  │ (ranks, recency, │        ┊   │      ┌──────────────────┐                       │
                  │  play counts)    │        ┊   │      │ Compact TM Events│                       │
                  └──────────────────┘        ┊   │      │ (strip payload)  │                       │
                                              ┊   │      └────────┬─────────┘                       │
                                              ┊   │               ▼                                 │
                                              ┊   │      ┌──────────────────┐                       │
                                              ┗┄┄┄┼┄┄┄┄▶ │ Match Concerts   │                       │
                                          (matches │     │ to Artists       │                       │
                                          per page)│     │ (fuzzy, per page)│                       │
                                                   │     └────────┬─────────┘                       │
                                                   │              ▼                                 │
                                                   │     ┌──────────────────┐                       │
                                                   └─────│ Wait Between TM  │                       │
                                                         │ Requests (2s)    │                       │
                                                         └──────────────────┘                       │
                                                                                                    ▼
                                                                                    ┌──────────────────┐
                                                                                    │ Score Concerts   │
                                                                                    │ (recency + freq +│
                                                                                    │  distance +      │
                                                                                    │  price + venue)  │
                                                                                    └────────┬─────────┘
                                                                                             ▼
       ┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
       │ Setlist Prep     │───────▶│ setlist.fm       │───────▶│ Build Prompt     │
       │ (dedupe, 30-day  │        │ Request          │        │ (per artist)     │
       │  cache)          │        │ (last 5 shows)   │        └────────┬─────────┘
       └──────────────────┘        └──────────────────┘                 ▼
                                                              ┌──────────────────┐
                                                              │ Claude Request   │
                                                              │ (2-3 sentence    │
                                                              │  show preview)   │
                                                              └────────┬─────────┘
                                                                       ▼
                                                              ┌──────────────────┐
                                                              │ Attach Previews  │
                                                              │ to scored events │
                                                              └────────┬─────────┘
                                                                       ▼
                                                              ┌──────────────────┐
                                                              │ Get Notion Pages │
                                                              │ (existing rows = │
                                                              │  de-dupe source) │
                                                              └────────┬─────────┘
            ┌─────────────────────────────────────────────────────────┼──────────────────────────────┐
            ▼                                                          ▼                              ▼
   ┌──────────────────┐                                     ┌──────────────────┐           ┌──────────────────┐
   │ Build Email      │                                     │ Calendar Prep    │           │ Notion Prep      │
   │ Digest           │                                     │ (score ≥ 60,     │           │ (all events;     │
   │ (all matches)    │                                     │  not yet on      │           │  tag create/     │
   └────────┬─────────┘                                     │  calendar)       │           │  update)         │
            ▼                                                └────────┬─────────┘           └────────┬─────────┘
   ┌──────────────────┐                                              ▼                              ▼
   │ Send Digest?     │                                     ┌──────────────────┐           ┌──────────────────┐
   │ (IF)             │                                     │ Create an event  │           │ Route Create vs  │
   └────────┬─────────┘                                     │ (Google Calendar)│           │ Update (IF)      │
            ▼                                                └──────────────────┘           └───┬──────────┬───┘
   ┌──────────────────┐                                                                    new │          │ existing
   │ Send Digest      │                                                                        ▼          ▼
   │ (Gmail digest)   │                                                                 ┌──────────┐ ┌──────────┐
   └──────────────────┘                                                                 │ Create a │ │ Update a │
                                                                                        │ database │ │ database │
                                                                                        │ page     │ │ page     │
                                                                                        └──────────┘ └──────────┘
```

> Matching runs **inside** the Ticketmaster loop: each page is compacted and matched against the `Build Artist Profile` output (read by reference) before the workflow waits and fetches the next window. Matches accumulate in per-execution static data; `Score Concerts` runs once after the loop finishes.

---

## How It Works

The workflow runs every morning at 8am and moves through five stages:

### 1. Spotify Data Collection

Three parallel API calls pull your top artists across all time windows:

- **Short term** — last ~4 weeks (50 artists)
- **Medium term** — last ~6 months (50 artists)
- **Long term** — all time (50 artists)

A fourth call fetches your recently played tracks (up to 50). All four streams are merged into a single artist map that captures each artist's rank across all three windows, how many times they've appeared in recent plays, and how many days ago you last listened to them.

Spotify recently-played data is only a small track window, so an artist can be a very recent listener favorite without appearing there. The score therefore treats exact recently-played timestamps as best evidence, then falls back to short-term top-artist rank for the recency component.

Artist names are normalized (lowercased, diacritics stripped, featured-artist suffixes removed) to support fuzzy matching downstream.

### 2. Concert Discovery

Ticketmaster requests are split into monthly windows across the next **6 months** within **50 miles of Downtown Brooklyn**, sorted by soonest date first. Each monthly window can fetch up to 5 pages of 200 events, which avoids Ticketmaster's paging-depth limit while still covering crowded NYC date ranges.

Ticketmaster rejects deep paging when `page * size >= 1000`. With `size=200`, that means a single broad search can only safely fetch pages `0-4`. A 6-month NYC music search can have far more than 1,000 results, so the workflow narrows each request to a month-sized date window and fetches pages `0-4` for each window instead of trying to page through one giant result set.

The Ticketmaster page requests are serialized through `Loop Over Ticketmaster Windows` and `Wait Between Ticketmaster Requests`, currently waiting 2 seconds between requests. This avoids n8n Cloud firing every monthly page request at once. If Ticketmaster still returns rate-limit errors, increase the wait duration before reducing the search window.

After each Ticketmaster response, `Compact Ticketmaster Events` strips the raw API payload down to the fields used for matching and scoring. `Match Concerts to Artists` then runs inside the Ticketmaster loop on that compact page only, so n8n Cloud does not need to load thousands of full Ticketmaster response objects into one Code node. Matched concerts are accumulated in per-execution workflow static data and scored only after the loop finishes. Empty pages and pages with no artist matches emit small loop-control items so n8n still advances to the wait/retry branch; `Score Concerts` ignores those control items and reads only the accumulated real matches.

Each event is matched against your artist map using normalized name comparison, with exact normalized-name matches preferred and only very conservative fuzzy matching allowed for longer names. The production matcher checks every Ticketmaster attraction on an event, so festivals can yield multiple matched artists from one Ticketmaster event. It does not scan arbitrary event text because that produced false positives for artists with common-word names. The matcher also identifies whether your artist is the headliner or an opener when attraction ordering is available.

> Bandsintown integration was tested but is not active: the public events endpoint returned empty event lists even when artist metadata reported upcoming events.

### 3. Concert Scoring

Every matched concert is scored out of **100 points** across five dimensions:

| Dimension | Max | How it's calculated |
|-----------|-----|---------------------|
| **Recency** | 30 | Days since the artist appeared in Spotify recently played when available (0 days = 30pts, ≤7 = 25, ≤30 = 15, ≤90 = 5); if exact recent-play data is missing, falls back to short-term top-artist rank (top 10 = 20, top 25 = 12, top 50 = 6) |
| **Frequency** | 25 | Recent play count (2pts each, cap 15) + rank bonuses across all three Spotify windows |
| **Distance** | 20 | Miles from home (≤5mi = 20, ≤15 = 15, ≤30 = 10, ≤50 = 5) |
| **Price** | 15 | Minimum ticket price (≤$50 = 15, ≤$100 = 10, ≤$200 = 5, else 0) |
| **Venue fit** | 10 | Personalized tier list (see below) |

#### Venue Tier List

Venues are scored by personal preference for smaller, indie spaces:

| Score | Examples |
|-------|----------|
| 10 | Baby's All Right, Brooklyn Made, Union Pool, Saint Vitus, Market Hotel, Trans-Pecos |
| 7 | Music Hall of Williamsburg, Bowery Ballroom, Mercury Lounge, Knockdown Center, Elsewhere, Brooklyn Paramount, Le Poisson Rouge |
| 5 | Brooklyn Steel, Webster Hall, Warsaw, Kings Theatre, Beacon Theatre, Terminal 5, Brooklyn Mirage |
| 3 | Any unlisted venue (default) |
| 1 | Madison Square Garden, Barclays Center, MetLife Stadium, Prudential Center, Forest Hills Stadium, Radio City Music Hall |

### 4. Setlist Previews (via Claude)

For each matched artist (deduplicated, with a 30-day cache), the workflow:

1. Fetches the artist's most recent 5 setlists from **setlist.fm**
2. Formats the setlists into a prompt
3. Calls **Claude Sonnet** to generate a 2-3 sentence preview — set length, key songs, notable production — written like a recommendation from a knowledgeable friend
4. Caches the result per artist for 30 days

If an artist is opening rather than headlining, the prompt notes the shorter expected set length (30-45 min).

### 5. Output

After previews are attached, all scored events fan out to three parallel output branches:

#### Email Digest (all matched events)
An HTML digest is sent via Gmail on every successful run, listing all current matched concerts grouped by score band: Strong matches, Good matches, Maybe worth a look, and Long shots. It is intentionally **not** de-duped against Notion, so repeat runs still send the current ranked list. Each card shows artist, venue, date, distance, price, a visual score breakdown, the Claude-generated setlist preview, and buttons to buy tickets or add to Google Calendar.

#### Google Calendar (score ≥ 60)
High-confidence shows are automatically added to your Google Calendar with venue, ticket link, score breakdown, and setlist preview in the event description.

#### Notion Tracker (all matched events)
Every matched concert — regardless of score — is logged to a **Concert Tracker** Notion database with full metadata: score, visual score breakdown, opener status, distance, price, ticket URL, dates discovered and performed, and the setlist preview. Page titles include the show date (e.g. `ROSALÍA at Madison Square Garden — Jun 18, 2026`) so same-venue multi-night runs stay distinct. New events are created; events already tracked are updated in place (see [De-duplication](#de-duplication)). For newly created pages, the workflow also appends a richer page-body section with the scorecard, show details, setlist preview, and ticket link.

---

## Setup

### Prerequisites

- [n8n](https://n8n.io) (self-hosted via Docker or cloud)
- Accounts and credentials for:
  - Spotify (OAuth2)
  - Ticketmaster Discovery API
  - setlist.fm API
  - Anthropic API
  - Gmail (OAuth2)
  - Google Calendar (OAuth2)
  - Notion (internal integration; used by both database updates and page-body block appends)

### Credentials

Configure the following in n8n:

| Variable | Where used |
|----------|------------|
| `$vars.TICKETMASTER_API_KEY` | Concert discovery |
| `$vars.SETLIST_FM_API_KEY` | Setlist fetching |
| `$vars.ANTHROPIC_API_KEY` | Claude preview generation |
| Spotify OAuth2 (`DYX2e4Iy4Laa0AiF`) | Spotify top artists + recently played |
| Gmail OAuth2 | Email digest |
| Google Calendar OAuth2 | Auto calendar events |
| Notion API | Concert Tracker database + page-body block appends |

### Location

The workflow is hardcoded to **Downtown Brooklyn** (`40.6928, -73.9903`) as the search origin. Update this in the `TicketMaster Request` node (`latlong` parameter) and the `Match Concerts to Artists` node (`USER_LAT` / `USER_LNG`) to match your location.

### Running

1. Import `workflows/concert-intelligence-agent.json` into your n8n instance
2. Configure all credentials
3. Set `TICKETMASTER_API_KEY`, `SETLIST_FM_API_KEY`, and `ANTHROPIC_API_KEY` as n8n variables
4. Activate the workflow — it will run daily at 8am

### Docker (optional)

A `docker-compose.yaml` is included if you want to run n8n locally. This project primarily targets n8n Cloud.

```bash
docker compose up -d
```

---

## Matching QA

Use the local QA script to check whether a saved Ticketmaster response would match expected Spotify artists before importing workflow changes into n8n:

```bash
SPOTIFY_ACCESS_TOKEN=... TICKETMASTER_API_KEY=... \
  node scripts/fetch-qa-inputs.js
```

```bash
node scripts/qa-ticketmaster-matching.js \
  --artists qa/artists.json \
  --ticketmaster qa/ticketmaster-response.json \
  --festival-hints qa/festival-lineup-hints.example.json \
  --expect Lorde \
  --expect "Blood Orange"
```

The fetch script writes `qa/artists.json` and `qa/ticketmaster-response.json`, fetching the same monthly-window Ticketmaster search as production. The Spotify token must be a user OAuth token with `user-top-read` and `user-read-recently-played`. If you prefer manual export, paste the **Build Artist Profile** output to `qa/artists.json` and the raw **TicketMaster Request** output to `qa/ticketmaster-response.json`. The artists file can be a raw array of artist objects or n8n-style items with a `json` wrapper. The Ticketmaster file should be the raw response from the Discovery API.

The script prints matched artists, the exact Ticketmaster event each matched, and every artist with no match sorted by listening recency/rank. By default it mirrors production attraction-only matching. Optional festival hints are QA-only expectations for known festival lineups and are not used by the production workflow. Use `--include-event-text` only as a diagnostic mode for suspected misses, since event-text matching is intentionally too noisy for production. The script exits non-zero if an expected artist was not matched.

---

## Scoring Thresholds

| Threshold | Action |
|-----------|--------|
| Any score | Logged to Notion Concert Tracker |
| Any score | Included in every email digest, grouped by score band |
| ≥ 60 | Google Calendar event created (first time only) |

### De-duplication

De-dupe state is **not** kept in n8n workflow static data (it gets wiped on re-save / version change on n8n Cloud, which caused duplicate entries). Instead, **Notion is the durable source of truth**:

- A single `Get Notion Pages` node reads the Concert Tracker at the start of each run.
- **Notion** — events already present (by `Event ID`) are *updated* (keeping Score / Alerted / On Calendar current); new ones are *created*. The `Status` field is left untouched on update so manual status changes survive. The `Breakdown` property is written as a compact visual score card using colored text bars, so each Notion page has a scan-friendly explanation similar to the email card. Rich page-body blocks are appended only for newly created pages to avoid duplicating the same scorecard on every run.
- **Email** — the digest is sent every successful run and is based on all current scored matches, not on whether Notion has seen the event before. The `Alerted` property remains a useful marker that the event cleared the original alert threshold.
- **Calendar** — created only if the event isn't already in Notion with `On Calendar = true`.

Because the per-run Notion snapshot is read before any writes and the flags are flipped on update, Calendar events are created once even if an event appears in later runs. Email is the exception: it is a current-run digest and intentionally repeats the current list.

---

## Project Structure

```
.
├── docker-compose.yaml
├── README.md
├── scripts/
│   ├── fetch-qa-inputs.js            # pulls live Spotify + Ticketmaster into qa/
│   └── qa-ticketmaster-matching.js   # offline matcher mirroring production
├── qa/
│   ├── festival-lineup-hints.example.json   # QA-only festival lineup expectations
│   ├── artists.json                         # Build Artist Profile export (gitignored)
│   └── ticketmaster-response.json           # raw TM Discovery response (gitignored)
└── workflows/
    └── concert-intelligence-agent.json
```

`scripts/` holds the local matching QA harness (see [Matching QA](#matching-qa)). `qa/` holds its inputs/fixtures: only `festival-lineup-hints.example.json` is committed — `artists.json` and `ticketmaster-response.json` are local exports and are gitignored.

---

## Planned Improvements

- **Configurable search settings** — tune page/window count, radius, and expected festival lineups without editing workflow code
