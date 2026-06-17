# Logging Setup — `run_log` audit trail

This workflow writes an **append-only audit log** (one row per run event) to a new n8n Data Table called `run_log`. Because n8n Data Table columns are created in the n8n UI — not by the workflow JSON — you need a little one-time setup before re-importing. Total time: ~5 minutes.

## Step 1 — Create the `run_log` Data Table

In your n8n project: **Data Tables → Create Data Table → name it `run_log`**, then add these columns **exactly** (names and types must match, or the log nodes won't bind):

| Column | Type |
|---|---|
| `timestamp` | String |
| `event` | String |
| `level` | String |
| `executionId` | String |
| `artists` | Number |
| `tmRequests` | Number |
| `tmFailedWindows` | Number |
| `matches` | Number |
| `strong` | Number |
| `good` | Number |
| `maybe` | Number |
| `longshot` | Number |
| `setlistFetched` | Number |
| `claudeFailures` | Number |
| `calendarEligible` | Number |
| `detail` | String |
| `failingNode` | String |

> n8n always adds a built-in `id` and timestamps of its own — you don't need to create those. Only add the 17 columns above.

## Step 2 — Re-import the workflow

Import `workflows/concert-intelligence-agent.json` into n8n (Workflows → ⋯ → Import from File), overwriting the existing one. Editing the file on disk does **not** update the running instance — the re-import is what applies all of this.

## Step 3 — Point the log nodes at the table (one-time)

The two writer nodes ship with a **placeholder** table reference (`REPLACE_WITH_run_log_TABLE_ID`) because the real table ID doesn't exist until you create it in Step 1. After importing, open each of these nodes and pick **`run_log`** from the Data Table dropdown:

- `Log — Run Summary`
- `Log — Errored`

## Step 4 — Re-assign credentials if needed

n8n import sometimes drops credential bindings on nodes it sees as new. After import, spot-check that the Spotify, Gmail, Google Calendar, and Notion nodes still have their credentials assigned, and re-wire any that came up blank.

## What gets logged

| Event | When | Source | Metrics included? |
|---|---|---|---|
| `run_summary` | Every successful run, at the `Attach Previews` chokepoint (before the Email/Calendar/Notion fan-out) | `Build Run Log` → `Log — Run Summary` | ✅ full pipeline-health metrics |
| `errored` | Any uncaught failure in the workflow | `Error Trigger` → `Build Error Log` → `Log — Errored` | error `detail` + `failingNode` |

`run_summary` columns capture discovery → scoring → preview health:

- `artists` — artists in the Spotify profile
- `tmRequests` — Ticketmaster page requests issued (windows × pages)
- `tmFailedWindows` — page requests that returned a rate-limit/error response (see the `ticketmaster_error` handling in `Compact Ticketmaster Events`); **non-zero sets `level` to `warn`**
- `matches` — scored concert matches
- `strong` / `good` / `maybe` / `longshot` — score-band counts (≥60 / 40–59 / 25–39 / <25)
- `setlistFetched` — artists that needed a fresh setlist.fm fetch (cache misses)
- `claudeFailures` — artists whose preview fell back to "No preview available."
- `calendarEligible` — matches at score ≥ 60 (the calendar threshold)

`level` is one of `info`, `warn` (a window failed), or `error` (the run crashed).

### Design notes

- **Append-only.** Nothing is ever updated — every event is a new row. Standard event-log pattern; avoids cross-execution update races.
- **Best-effort, never fatal.** The writer nodes are set to *continue on error* and retry up to 3×, so a logging hiccup can never block or fail an actual run.
- **`console.log` is dev-only.** The Code nodes also emit `[Node Name]` console lines (e.g. `[Compact Ticketmaster Events]`, `[Score Concerts]`, `[Build Run Log]`), but on n8n Cloud those only show in the editor during manual runs — the `run_log` table is your real system of record.
- **Single summary row per run.** The Email/Calendar/Notion branches fan out in parallel with no clean join, so the summary is captured at `Attach Previews`. Actual Notion create/update and calendar-created counts are visible in Notion/Calendar themselves; if you want them in the log too, add branch-end writer nodes.
- **Growth is a non-issue.** One or two rows per day keeps the table tiny for years; no pruning needed.

## Reading the log

In n8n, open the `run_log` Data Table to browse. To audit failures, filter `level = error` (or `event = errored`). To spot degraded runs where Ticketmaster rate-limited, filter `tmFailedWindows > 0` or `level = warn`. To track match volume over time, sort by `timestamp` and read `matches` / the score bands.

## Future upgrades (not done here)

- Promote the in-workflow `Error Trigger` to a **shared error workflow** once you have more than one workflow (Workflow Settings → Error Workflow).
- Add branch-end writer nodes to log actual Notion `created`/`updated` and calendar `created` counts.
