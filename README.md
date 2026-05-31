# Concert Intelligence Agent

An n8n workflow that cross-references your Spotify listening history against upcoming concerts, scores each show based on how much you actually listen to the artist, and emails you a digest with AI-generated setlist previews — plus automatic Google Calendar events and a Notion tracker.

---

## Workflow Diagram

```
                        ┌─────────────────────┐
                        │   Schedule Trigger   │
                        │      (8am daily)     │
                        └──────────┬──────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
 ┌────────────────┐     ┌──────────────────┐    ┌──────────────────┐
 │ Recently       │     │  Top Artists     │    │   Ticketmaster   │
 │ Played Tracks  │     │  Short / Medium  │    │   Events API     │
 │ (Spotify)      │     │  / Long Term     │    │  (50mi radius,   │
 └───────┬────────┘     │  (Spotify)       │    │   next 6 months) │
         │              └────────┬─────────┘    └────────┬─────────┘
         │                       │                       │
         └──────────┬────────────┘                       │
                    ▼                                     │
         ┌──────────────────┐                            │
         │  Merge & Build   │                            │
         │  Artist Map      │                            │
         │  (ranks, recency,│                            │
         │  play counts)    │                            │
         └────────┬─────────┘                            │
                  │                                      │
                  └──────────────────┬───────────────────┘
                                     ▼
                          ┌──────────────────┐
                          │  Match Concerts  │
                          │  to Artists      │
                          │  (fuzzy name     │
                          │   matching)      │
                          └────────┬─────────┘
                                   │
                                   ▼
                          ┌──────────────────┐
                          │  Score Concerts  │
                          │  (recency +      │
                          │   frequency +    │
                          │   distance +     │
                          │   price + venue) │
                          └────────┬─────────┘
                                   │
                    ┌──────────────┘
                    ▼
         ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
         │  Setlist Prep    │────▶│  setlist.fm API  │────▶│  Build Prompt    │
         │  (dedupe, cache  │     │  (last 5 shows)  │     │  (per artist)    │
         │   check, 30 days)│     └──────────────────┘     └────────┬─────────┘
         └──────────────────┘                                        │
                                                                     ▼
                                                          ┌──────────────────┐
                                                          │  Claude Sonnet   │
                                                          │  (2-3 sentence   │
                                                          │   show preview)  │
                                                          └────────┬─────────┘
                                                                   │
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
                              ┌──────────────────────────────────┼──────────────────────────────┐
                              ▼                                  ▼                              ▼
                   ┌──────────────────┐              ┌──────────────────┐           ┌──────────────────┐
                   │  Filter & De-dupe│              │  Calendar Prep   │           │   Notion Prep    │
                   │  (score ≥ 40,    │              │  (score ≥ 60,    │           │  (all events;    │
                   │   not yet        │              │   not yet on     │           │   tag create/    │
                   │   alerted)       │              │   calendar)      │           │   update)        │
                   └────────┬─────────┘              └────────┬─────────┘           └────────┬─────────┘
                            ▼                                 ▼                              ▼
                   ┌──────────────────┐             ┌──────────────────┐          ┌──────────────────┐
                   │  Gmail Alert     │             │  Google Calendar │          │ Route Create vs  │
                   │  (HTML digest)   │             │  Event           │          │ Update           │
                   └──────────────────┘             └──────────────────┘          └───┬──────────┬───┘
                                                                                 new │          │ existing
                                                                                     ▼          ▼
                                                                            ┌──────────┐  ┌──────────┐
                                                                            │  Create  │  │  Update  │
                                                                            │  Notion  │  │  Notion  │
                                                                            │  page    │  │  page    │
                                                                            └──────────┘  └──────────┘
```

---

## How It Works

The workflow runs every morning at 8am and moves through five stages:

### 1. Spotify Data Collection

Three parallel API calls pull your top artists across all time windows:

- **Short term** — last ~4 weeks (50 artists)
- **Medium term** — last ~6 months (50 artists)
- **Long term** — all time (50 artists)

A fourth call fetches your recently played tracks (up to 50). All four streams are merged into a single artist map that captures each artist's rank across all three windows, how many times they've appeared in recent plays, and how many days ago you last listened to them.

Artist names are normalized (lowercased, diacritics stripped, featured-artist suffixes removed) to support fuzzy matching downstream.

### 2. Concert Discovery

A single Ticketmaster API call fetches up to 200 upcoming music events within **50 miles of Downtown Brooklyn** over the next **6 months**.

Each event is matched against your artist map using normalized name comparison and Levenshtein distance (tolerates up to 2 character edits, capped at 30% of name length). The matcher also identifies whether your artist is the headliner or an opener.

> Bandsintown integration is stubbed out and disabled pending an API key — it will supplement Ticketmaster once available.

### 3. Concert Scoring

Every matched concert is scored out of **100 points** across five dimensions:

| Dimension | Max | How it's calculated |
|-----------|-----|---------------------|
| **Recency** | 30 | Days since you last played the artist (0 days = 30pts, ≤7 = 25, ≤30 = 15, ≤90 = 5, else 0) |
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

#### Email Alert (score ≥ 40)
An HTML digest is sent via Gmail listing only events not already alerted (de-duped against Notion's `Alerted` flag — see [De-duplication](#de-duplication)). Each card shows artist, venue, date, distance, price, score breakdown, the Claude-generated setlist preview, and buttons to buy tickets or add to Google Calendar.

#### Google Calendar (score ≥ 60)
High-confidence shows are automatically added to your Google Calendar with venue, ticket link, score breakdown, and setlist preview in the event description.

#### Notion Tracker (all matched events)
Every matched concert — regardless of score — is logged to a **Concert Tracker** Notion database with full metadata: score, breakdown, opener status, distance, price, ticket URL, dates discovered and performed, and the setlist preview. Page titles include the show date (e.g. `ROSALÍA at Madison Square Garden — Jun 18, 2026`) so same-venue multi-night runs stay distinct. New events are created; events already tracked are updated in place (see [De-duplication](#de-duplication)).

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
  - Notion (internal integration)

### Credentials

Configure the following in n8n:

| Variable | Where used |
|----------|------------|
| `$vars.TICKETMASTER_API_KEY` | Concert discovery |
| `$vars.SETLIST_FM_API_KEY` | Setlist fetching |
| `$vars.ANTHROPIC_API_KEY` | Claude preview generation |
| Spotify OAuth2 (`DYX2e4Iy4Laa0AiF`) | Spotify top artists + recently played |
| Gmail OAuth2 | Alert emails |
| Google Calendar OAuth2 | Auto calendar events |
| Notion API | Concert Tracker database |

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

## Scoring Thresholds

| Threshold | Action |
|-----------|--------|
| Any score | Logged to Notion Concert Tracker |
| ≥ 40 | Email alert sent (first time only) |
| ≥ 60 | Google Calendar event created (first time only) |

### De-duplication

De-dupe state is **not** kept in n8n workflow static data (it gets wiped on re-save / version change on n8n Cloud, which caused duplicate entries). Instead, **Notion is the durable source of truth**:

- A single `Get Notion Pages` node reads the Concert Tracker at the start of each run.
- **Notion** — events already present (by `Event ID`) are *updated* (keeping Score / Alerted / On Calendar current); new ones are *created*. The `Status` field is left untouched on update so manual status changes survive.
- **Email** — sent only if the event isn't already in Notion with `Alerted = true`.
- **Calendar** — created only if the event isn't already in Notion with `On Calendar = true`.

Because the per-run Notion snapshot is read before any writes and the flags are flipped on update, an event that later climbs across a threshold (e.g. after a re-listen) is alerted exactly once and never repeats.

---

## Project Structure

```
.
├── docker-compose.yaml
├── README.md
└── workflows/
    └── concert-intelligence-agent.json
```

---

## Planned Improvements

- **Bandsintown integration** — additional event source, pending API key
