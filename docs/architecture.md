# Architecture

This document describes how mlbreview works at the code level. It covers the pipeline execution flow, every module's responsibility, the data model, scoring formulas, LLM integration, rendering, deployment, and safety mechanisms. Use it to understand what the code does before making changes.

For formula tuning (adjusting weights and thresholds), see [`formulas.md`](formulas.md).

---

## Table of contents

- [System overview](#system-overview)
- [Pipeline execution flow](#pipeline-execution-flow)
- [Module reference](#module-reference)
  - [Entry point (`__main__.py`)](#entry-point-__main__py)
  - [Configuration (`config.py`)](#configuration-configpy)
  - [Pipeline orchestrator (`pipeline.py`)](#pipeline-orchestrator-pipelinepy)
  - [Data layer (`data/`)](#data-layer-data)
  - [Scoring layer (`scoring/`)](#scoring-layer-scoring)
  - [LLM integration (`llm.py`)](#llm-integration-llmpy)
  - [Rendering (`render/`)](#rendering-render)
- [Data model](#data-model)
- [Safety mechanisms](#safety-mechanisms)
- [Deployment](#deployment)
- [Key design decisions](#key-design-decisions)

---

## System overview

mlbreview is a Python pipeline that runs once daily via GitHub Actions. It fetches MLB game data, scores and ranks games, generates prose with Claude, renders static HTML, sends an email, and deploys a dashboard to GitHub Pages.

```
                         GitHub Actions cron
                         (09:30 / 10:30 / 11:30 UTC)
                                  |
                                  v
                          __main__.py (CLI)
                                  |
                                  v
                           pipeline.py (orchestrator)
                          /       |       \        \
                         v        v        v        v
                     data/     scoring/   llm.py   render/
                   (fetch)    (rank)    (prose)   (HTML)
                      |          |         |         |
                      v          v         v         v
                  MLB Stats   config.py  Claude    Jinja2
                    API        (weights)  API     templates/
                                                     |
                                            +--------+--------+
                                            |                 |
                                            v                 v
                                     Resend (email)    GitHub Pages
                                                      (dashboard)
```

The orchestrator (`pipeline.py`) owns sequencing only. All business logic lives in submodules. The pipeline never runs partial digests — if a critical fetch fails, it exits with code 1 and no email is sent.

---

## Pipeline execution flow

The full sequence from cron trigger to delivered email:

```
  GitHub Actions          __main__.py             pipeline.py
  ─────────────          ────────────            ────────────
       |                      |                       |
       |──cron fires─────────>|                       |
       |                      |──parse CLI args       |
       |                      |──resolve target_date  |
       |                      |  (yesterday in ET)    |
       |                      |──Config.load()        |
       |                      |──run()───────────────>|
       |                      |                       |──season check
       |                      |                       |  (Mar 20 – Nov 10?)
       |                      |                       |──idempotency guard
       |                      |                       |  (dashboard already exists?)
       |                      |                       |
       |                      |                       |
       |                      |                       |        MLB Stats API
       |                      |                       |        ─────────────
       |                      |                       |──GET /schedule (finals)────>
       |                      |                       |<───list[Game]──────────────
       |                      |                       |──GET /schedule (tonight)───>
       |                      |                       |<───list[TonightGame]───────
       |                      |                       |
       |                      |                       |──[off-day branch if no finals]
       |                      |                       |
       |                      |                       |──GET /winProbability ×N────>
       |                      |                       |<───dict[gamePk, GameFeed]──
       |                      |                       |──GET /transactions─────────>
       |                      |                       |<───list[Transaction]───────
       |                      |                       |
       |                      |                       |──score_games() ──> list[ScoredGame]
       |                      |                       |──apply_variety_rule() ──> top 3
       |                      |                       |──select_most_hyped() ──> best tonight
       |                      |                       |
       |                      |                       |──collect batter IDs from top plays
       |                      |                       |──GET /people (season stats)────>
       |                      |                       |<───dict[name, BatterSeasonStats]
       |                      |                       |
       |                      |                       |        Claude API
       |                      |                       |        ──────────
       |                      |                       |──write_storyline() ×3──────>
       |                      |                       |<───prose (or fallback)─────
       |                      |                       |──write_preview() ×1────────>
       |                      |                       |<───prose (or fallback)─────
       |                      |                       |
       |                      |                       |
       |                      |                       |        V2 Leaderboards (try/except)
       |                      |                       |        ──────────────────────────
       |                      |                       |──GET /game/{pk}/boxscore ×N───>
       |                      |                       |<───daily player stats────────
       |                      |                       |──write snapshot to public/snapshots/
       |                      |                       |──load last 7 + 15 snapshots
       |                      |                       |──compute_rolling_stats()
       |                      |                       |──fetch Statcast (pybaseball)──>
       |                      |                       |<───season xwOBA, FIP data────
       |                      |                       |──score_leaderboards()
       |                      |                       |──attach Leaderboards to Digest
       |                      |                       |  (None on any failure)
       |                      |                       |
       |                      |                       |──build Digest dataclass
       |                      |                       |──render dashboard HTML (Jinja2)
       |                      |                       |──write to public/digests/YYYY-MM-DD/
       |                      |                       |──render + rebuild archive index
       |                      |                       |──render email HTML + text (Jinja2)
       |                      |                       |
       |                      |                       |        Resend API
       |                      |                       |        ──────────
       |                      |                       |──send email────────────────>
       |                      |                       |<───200 OK─────────────────
       |                      |                       |
       |                      |<──return 0────────────|
       |<──exit 0─────────────|                       |
       |                      |                       |
       |──deploy gh-pages─────────────────────────────────────> GitHub Pages
```

### Early exit paths

The pipeline exits with code 0 (success) on three early-exit conditions. GitHub Actions treats all of these as a successful run:

| Guard | Condition | Log message |
|-------|-----------|-------------|
| **Season pause** | Date is outside Mar 20 – Nov 10 | `Season pause: {date} is outside the active season window` |
| **Idempotency** | `public/digests/{date}/index.html` already exists | `Idempotency guard: {path} already exists` |
| **Off-day** | Zero completed games for target date | `No completed games on {date} — off-day branch` |

The off-day branch still sends an email (with tonight's schedule) and writes a dashboard page. The other two guards skip everything.

### Off-day branch

When no games completed on the target date, the pipeline takes a shorter path:

```
  pipeline.py
  ────────────
       |──fetch_finals() returns []
       |──fetch_tonight() returns tonight's schedule
       |──build Digest(is_off_day=True, tonight_games=...)
       |──render dashboard (off-day template)
       |──render email (off-day headline + tonight schedule)
       |──send email
       |──return 0
```

No scoring, no LLM calls, no transactions fetch. The digest uses the headline and body text from `config.py` (`OFF_DAY_HEADLINE`, `OFF_DAY_BODY`).

---

## Module reference

### Entry point (`__main__.py`)

**File:** `src/mlbreview/__main__.py`

The CLI entry point. Parses arguments, resolves the target date, loads configuration, and calls `pipeline.run()`.

| Argument | Default | Purpose |
|----------|---------|---------|
| `--dry-run` | `False` | Skip email send; print output to stdout instead |
| `--date YYYY-MM-DD` | Yesterday (ET) | Override the target date for backfills |
| `--out-dir` | `./public` | Where rendered dashboard HTML is written |

The target date defaults to "yesterday in America/New_York" — the pipeline always recaps the prior day's games. In GitHub Actions, the cron fires in the morning, so "yesterday" is the previous night's games.

---

### Configuration (`config.py`)

**File:** `src/mlbreview/config.py`

Every tunable constant in the system lives here. Scoring modules import these directly; they do not redefine them.

**Drama formula weights:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `DRAMA_W_MAX_WPA` | 0.5 | Weight for the biggest single-play WPA swing |
| `DRAMA_W_LATE_WPA` | 0.3 | Weight for the biggest WPA swing in innings 7+ |
| `DRAMA_W_MARGIN` | 0.2 | Weight for inverse margin-of-victory |
| `DRAMA_MAX_WPA_CEILING` | 50.0 | Normalization ceiling for WPA values |
| `DRAMA_LATE_INNING_THRESHOLD` | 7 | Innings >= this count as "late" |

**Hype formula weights:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `HYPE_W_PITCHING` | 0.35 | Weight for pitching matchup quality |
| `HYPE_W_STAKES` | 0.25 | Weight for standings/rivalry stakes |
| `HYPE_W_STARS` | 0.20 | Weight for star-player density |
| `HYPE_W_NATIONAL` | 0.20 | Weight for national broadcast flag |
| `HYPE_STAKES_DIVISION_RIVALS` | 0.4 | Stakes bonus for same-division matchups |
| `HYPE_STAKES_BOTH_ABOVE_500` | 0.3 | Stakes bonus for both teams above .500 |
| `HYPE_STAKES_PLAYOFF_RACE_DELTA` | 0.3 | Stakes bonus for tight playoff race |
| `HYPE_STAR_DENSITY_DIVISOR` | 4.0 | Denominator for star count normalization |

**Other:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `VARIETY_THRESHOLD` | 0.10 | Score proximity that triggers the variety rule |
| `MAX_STORYLINES` | 3 | Number of storylines in the digest |
| `LLM_MODEL` | `claude-haiku-4-5` | Model used for prose generation |
| `LLM_MAX_TOKENS` | 200 | Max tokens per LLM call |
| `LLM_RETRY_DELAY` | 2.0 | Seconds to wait before retrying a failed LLM call |

**V2 leaderboard constants:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `ROLLING_WINDOW_DAYS` | 7 | Rolling window for hot/cold lists |
| `BREAKOUT_WINDOW_DAYS` | 15 | Rolling window for breakout detection |
| `MIN_PA_HITTER` | 15 | Minimum plate appearances (7-day) |
| `MIN_IP_PITCHER` | 7.0 | Minimum innings pitched for starters (7-day) |
| `MIN_SV_OPP_CLOSER` | 2 | Minimum save opportunities for closers |
| `LEADERBOARD_SIZE` | 10 | Players per leaderboard section |
| `HITTER_W_AVG / W_HR / W_RBI` | 0.40 / 0.30 / 0.30 | Hitter composite weights |
| `HITTER_CEILING_AVG / HR / RBI` | .500 / 5 / 12 | Hitter composite ceilings |
| `STARTER_W_ERA / W_K9 / W_WHIP` | 0.40 / 0.35 / 0.25 | Starter composite weights |
| `CLOSER_W_ERA / W_SV_PCT / W_K9` | 0.35 / 0.40 / 0.25 | Closer composite weights |
| `PITCHER_CEILING_ERA / K9 / WHIP` | 6.0 / 15.0 / 2.0 | Shared pitcher ceilings |
| `LUCK_XWOBA_THRESHOLD` | 0.320 | Season xwOBA for quality contact |
| `LUCK_FIP_THRESHOLD` | 4.00 | Season FIP for quality pitching |

**Runtime config (`Config` dataclass):**

Loaded from environment variables at startup. `require_secrets=True` (production) raises `EnvironmentError` if any key is missing. `require_secrets=False` (dry run) tolerates missing keys.

| Env var | Required in production | Purpose |
|---------|----------------------|---------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for LLM prose |
| `RESEND_API_KEY` | Yes | Resend API key for email delivery |
| `DIGEST_TO_EMAIL` | Yes | Recipient email address |
| `DIGEST_FROM_EMAIL` | No (has default) | Sender address |

---

### Pipeline orchestrator (`pipeline.py`)

**File:** `src/mlbreview/pipeline.py`

The orchestrator wires the submodules together. It owns sequencing, not business logic.

**`run(target_date, dry_run, out_dir, config)`** — the public entry point. Checks guards, creates the HTTP client, delegates to `_run_pipeline()`, and ensures the client is closed.

**`_run_pipeline()`** — the main work function. Steps:

1. **Fetch finals** — `fetch_finals(target_date)` via the MLB Stats API
2. **Fetch tonight** — `fetch_tonight(target_date + 1 day)` for the preview
3. **Off-day check** — if `finals` is empty, build an off-day digest and return
4. **Fetch game feeds** — `fetch_game_feed(gamePk)` for each final (WPA data)
5. **Score and rank** — `score_games()` then `apply_variety_rule()` for storylines
6. **Score tonight** — `select_most_hyped()` for the preview game
7. **Fetch season stats** — collect batter IDs from top plays, batch-fetch hitting stats via `/api/v1/people`
8. **Generate prose** — `write_storyline()` x3 + `write_preview()` x1 via Claude (storylines include season stats in payload)
9. **Fetch transactions** — `fetch_transactions()` for the off-field section
10. **Build Digest** — assemble the `Digest` dataclass
11. **Render** — dashboard HTML + email HTML/text via Jinja2
12. **Deliver** — send email via Resend (or print in dry-run mode)

**Helper functions:**

| Function | Purpose |
|----------|---------|
| `_is_active_season(date)` | True if date is between Mar 20 and Nov 10 |
| `_fetch_game_feeds(games, client)` | Fetch WPA data for each game; skip failures |
| `_build_hype_contexts(tonight_games)` | Build `GameContext` for each tonight-game |
| `_both_above_500(game)` | Parse W-L records to check if both teams are winning |
| `_collect_batter_ids(top_games)` | Extract unique batter IDs from top plays across storyline games |
| `_generate_storyline_prose(games, llm_client, season_stats)` | Call LLM for each top storyline, enriched with season stats |
| `_generate_preview_prose(most_hyped, llm_client)` | Call LLM for tonight's preview |
| `_write_dashboard(digest, out_dir)` | Write day page + rebuild archive index |
| `_build_index_entries(out_dir)` | Scan `digests/` for existing date folders |
| `_send_email(digest, config)` | Render and send via Resend |
| `_print_dry_run(digest)` | Print email preview to stdout (dry-run mode) |
| `_run_v2_leaderboards(finals, date, mlb_client, out_path)` | V2 pipeline: fetch game logs → write snapshot → load rolling windows → fetch Statcast → score leaderboards. Returns `Leaderboards` or `None` on failure |

---

### Data layer (`data/`)

The data layer fetches from the MLB Stats API and parses responses into immutable dataclasses. No business logic — just fetch, parse, return.

#### `data/client.py` — HTTP client

Provides a shared `httpx.Client` with:
- 30-second timeout
- 2 transport-level retries (connection failures)
- 1 application-level retry on HTTP 5xx
- Custom user-agent header

All fetch functions use `get_json(client, url, params)`, which handles retries and raises `MlbApiError` on persistent failure. The orchestrator catches `MlbApiError` and either fails hard (schedule fetch) or logs a warning and continues (game feed, transactions).

#### `data/schedule.py` — Schedule fetchers

Two fetch functions, two parse functions, and several dataclasses:

**`fetch_finals(date, client)`** — calls `/api/v1/schedule` with `hydrate=team,linescore,decisions`. Returns `list[Game]` filtered to:
- Active game types only: `R` (regular), `F` (wild card), `D` (division), `L` (league), `W` (world series)
- Final statuses only: `Final`, `Game Over`, `Completed Early`

**`fetch_tonight(date, client)`** — calls `/api/v1/schedule` with `hydrate=team,linescore,probablePitcher,broadcasts`. Returns `list[TonightGame]` for all active game types (no status filter — games may not have started yet).

**Key dataclasses:**

```
Game
├── gamePk: int
├── game_type: str
├── status: str
├── away_team_name / away_team_abbr / away_score
├── home_team_name / home_team_abbr / home_score
├── decisions: Decisions (winner, loser, save pitcher names)
├── line_score: tuple[InningLine, ...] (per-inning runs)
├── margin (property): abs(away_score - home_score)
└── extra_innings (property): len(line_score) > 9

TonightGame
├── gamePk: int
├── game_type / game_date_utc
├── away_team_name / away_team_abbr / away_record
├── home_team_name / home_team_abbr / home_record
├── away_probable_pitcher / home_probable_pitcher: ProbablePitcher | None
├── broadcasts: tuple[Broadcast, ...]
└── is_national (property): any broadcast marked national
```

#### `data/game.py` — Win Probability feed

**`fetch_game_feed(gamePk, client)`** — calls `/api/v1/game/{gamePk}/winProbability`. Returns a `GameFeed` with per-play WPA data.

The WPA endpoint returns a JSON array of play objects. Each play has:
- `homeTeamWinProbabilityAdded` — signed WPA in percentage points (the drama formula uses `abs()` for swing magnitude)
- `about.inning`, `about.halfInning` — when the play occurred
- `matchup.batter.fullName`, `matchup.batter.id` — batter name and MLBAM player ID
- `matchup.pitcher.fullName`, `matchup.pitcher.id` — pitcher name and MLBAM player ID
- `result.description` — human-readable description

Player IDs (`batter_id`, `pitcher_id`) are captured on each `Play` and used downstream to batch-fetch season stats from the People API.

**`GameFeed` dataclass:**

```
GameFeed
├── gamePk: int
├── plays: tuple[Play, ...]
├── max_wpa_swing: float        (largest |WPA| across all plays)
├── late_inning_max_wpa: float  (largest |WPA| in innings >= 7)
├── biggest_play: Play | None   (the play with max |WPA|)
└── has_data (property): len(plays) > 0
```

Pre-computed aggregates (`max_wpa_swing`, `late_inning_max_wpa`, `biggest_play`) are calculated during parsing so the scoring layer doesn't need to re-scan plays.

#### `data/transactions.py` — Off-field news

**`fetch_transactions(start, end, client)`** — calls `/api/v1/transactions` for the date range. Returns `list[Transaction]` filtered to three categories:

| Category | Detection |
|----------|-----------|
| `trade` | `typeCode == "TR"` |
| `call_up` | `typeCode == "CU"` |
| `injured_list` | `typeCode == "SC"` with IL-related keywords in description |

Everything else (minor-league moves, status changes, extensions) is silently dropped. The news brief is factual bullets only — no LLM prose.

#### `data/stats.py` — Season stats

**`fetch_batter_season_stats(player_ids, season, client)`** — calls `/api/v1/people` with `hydrate=stats(type=season,season={year},group=[hitting])`. Accepts a set of MLBAM player IDs and returns `dict[str, BatterSeasonStats]` keyed by full name.

A single batched HTTP call fetches all players at once (comma-separated `personIds` parameter). Players without hitting splits (pitchers) are silently filtered out.

**`BatterSeasonStats` dataclass:**

```
BatterSeasonStats
├── player_id: int
├── full_name: str
├── home_runs: int
├── doubles: int
├── triples: int
├── hits: int
├── rbi: int
├── stolen_bases: int
└── avg: str
```

On API failure, returns an empty dict — the pipeline continues without season stats and the LLM produces prose without tallies.

#### `data/snapshots.py` — Daily stat snapshots (V2)

Persistence layer for V2 rolling-window leaderboards. Each pipeline run writes a JSON snapshot of the day's player stats to `public/snapshots/YYYY-MM-DD.json`. The leaderboard code loads the last N snapshots and computes rolling aggregates.

Snapshots are stored on the `gh-pages` branch alongside dashboard HTML. They are never pruned (~80KB/day, ~14MB/season).

**Dataclasses:**

```
DailySnapshot
├── snapshot_date: str (YYYY-MM-DD)
├── hitters: tuple[HitterDayStats, ...]
├── starters: tuple[StarterGameStats, ...]
└── closers: tuple[CloserDayStats, ...]

HitterDayStats
├── player_id, full_name, team_abbr
├── plate_appearances, at_bats, hits
├── doubles, triples, home_runs
├── rbi, stolen_bases, walks, strikeouts

StarterGameStats (per-start, not per-day)
├── player_id, full_name, team_abbr
├── game_date, opponent_abbr
├── outs_recorded (18 = 6.0 IP)
├── hits_allowed, earned_runs, walks
├── strikeouts, home_runs_allowed, pitches_thrown
└── innings_pitched (property: outs / 3)

CloserDayStats
├── player_id, full_name, team_abbr
├── outs_recorded, earned_runs
├── saves, blown_saves, holds
├── strikeouts, walks
└── innings_pitched (property: outs / 3)
```

**Functions:**

| Function | Purpose |
|----------|---------|
| `write_snapshot(snapshot, base_dir)` | Write snapshot as compact JSON; creates `snapshots/` dir if needed |
| `load_snapshot(path)` | Load a single snapshot from a JSON file |
| `load_snapshots(base_dir, n_days)` | Load the most recent N snapshots, newest-first; skips corrupt files |
| `snapshot_path(base_dir, date)` | Return canonical path for a snapshot file |

---

### Scoring layer (`scoring/`)

#### `scoring/drama.py` — Drama score

Ranks yesterday's games by how dramatic they were. The formula:

```
drama = 0.5 * norm_max_wpa + 0.3 * norm_late_wpa + 0.2 * inverse_margin
```

Where:
- `norm_max_wpa = min(|max WPA| / 50.0, 1.0)`
- `norm_late_wpa = min(|late-inning max WPA| / 50.0, 1.0)`
- `inverse_margin = 1 / (1 + margin)`

Each game also gets a **category tag** for the variety rule:

```
Priority: walkoff > extra_innings > comeback > pitchers_duel > feat > default
```

| Category | Detection logic |
|----------|----------------|
| `walkoff` | Home team wins and biggest play is in the final inning's bottom half |
| `extra_innings` | Game went past 9 innings |
| `comeback` | Winning team's win probability dropped below 20% at any point |
| `pitchers_duel` | Combined runs <= 4 and margin <= 1 |
| `feat` | One team scored 10+ runs, or shutout with 7+ runs |
| `default` | None of the above |

**`score_games(games, feeds)`** returns all games scored and sorted by drama descending.

#### `scoring/hype.py` — Hype score

Picks tonight's most-hyped game. The formula:

```
hype = 0.35 * pitching_quality + 0.25 * stakes + 0.20 * star_density + 0.20 * national_broadcast
```

Sub-signals:
- **Pitching quality** — average inverse-ERA of both starters, normalized. Unknown pitchers default to league-average ERA (4.50).
- **Stakes** — additive flags: +0.4 division rivals, +0.3 both above .500, +0.3 playoff race within 3 games. Capped at 1.0.
- **Star density** — count of players from `config/stars.json` in both rosters / 4, clamped to [0, 1].
- **National broadcast** — 1.0 if any broadcast is national, else 0.0.

**`select_most_hyped(games, contexts, stars)`** returns the single highest-scoring `ScoredTonightGame`, or `None`.

The star list (`config/stars.json`) contains ~30 MLBAM player IDs, refreshable via `scripts/refresh_stars.py`. It is league-neutral by design — selection is purely WAR-based with no team caps.

#### `scoring/variety.py` — Variety rule

Prevents the top 3 storylines from being the same category. Algorithm:

1. Take the top 5 candidates by drama score
2. Walk in order. For each candidate:
   - If its category was already selected AND its score is within `VARIETY_THRESHOLD` (10%) of the prior pick with that category → **skip**
   - Otherwise → **accept**
3. Stop at 3 accepted storylines
4. If fewer than 3 remain, backfill from skipped candidates

**`apply_variety_rule(candidates)`** returns `list[ScoredGame]` of length `min(3, len(candidates))`.

#### `scoring/leaderboards.py` — Rolling stats + leaderboard scoring (V2)

Computes 7-day and 15-day rolling aggregates from daily snapshots, scores qualified players by composite formulas, applies a Statcast-based luck filter, and detects breakout players.

**Rolling aggregation (U3):** Sums counting stats across daily snapshots for hitters, starters, and closers. Players below minimum-activity thresholds (15 PA / 7 IP / 2 save opportunities) are filtered out.

**Composite scoring (U4):** Each player role has a weighted composite formula normalized to [0, 1]:
- Hitters: 0.40 × norm_avg + 0.30 × norm_hr + 0.30 × norm_rbi
- Starters: 0.40 × inv_era + 0.35 × norm_k9 + 0.25 × inv_whip
- Closers: 0.35 × inv_era + 0.40 × sv_pct + 0.25 × norm_k9

**Luck filter:** Compares rolling traditional stats against season-level Statcast metrics. Hot hitter with high xwOBA → CONFIRMED; hot hitter with low xwOBA → LUCKY. Mirror logic for cold (UNLUCKY vs CONFIRMED) and pitchers (using FIP threshold). No Statcast data → UNCONFIRMED.

**Breakout detection:** Players who are 7-day hot AND have a 15-day composite above the qualified-player median. Uses 15-day rolling stats for the entry.

**Six leaderboards:** hot hitters, cold hitters, hot pitchers (starters + closers merged), cold pitchers, breakout hitters, breakout pitchers.

**`score_leaderboards(rolling_7d, rolling_15d, statcast_hitters, statcast_pitchers)`** returns a `Leaderboards` dataclass with all six lists plus snapshot counts.

See [`formulas.md`](formulas.md) for the full formula reference and tuning guide.

---

### LLM integration (`llm.py`)

**File:** `src/mlbreview/llm.py`

Generates 2-3 sentence prose for storylines and previews using Claude Haiku 4.5.

#### Call flow

```
  pipeline.py               llm.py                     Claude API
  ────────────              ───────                     ──────────
       |                       |                            |
       |──write_storyline()───>|                            |
       |                       |──build payload (JSON)      |
       |                       |  (teams, scores, top 3     |
       |                       |   plays by |WPA|,          |
       |                       |   decisions, category)     |
       |                       |                            |
       |                       |──messages.create()────────>|
       |                       |<───prose text──────────────|
       |                       |                            |
       |                       |──grounding check           |
       |                       |  (extract names from       |
       |                       |   prose, verify all        |
       |                       |   exist in payload)        |
       |                       |                            |
       |                       |──[pass] return prose       |
       |                       |──[fail] return fallback    |
       |<──prose string────────|                            |
```

#### System prompt

The LLM receives a fixed system prompt:

> "You write 2-3 sentence baseball storyline blurbs grounded strictly in the JSON facts provided. Never invent player names, stat lines, or plays. If the JSON does not contain a fact, do not state it. When batter_season_stats are provided, weave in the season tally naturally (e.g. "his 15th home run") — but only use numbers from the JSON. Plain prose, no markdown, no headlines."

#### Payload construction

**Storyline payload** (`_build_storyline_payload`):
- Team names, abbreviations, scores
- Top 3 plays sorted by `|WPA|` (description, inning, half, WPA, batter, pitcher)
- Decisive moment (the single biggest WPA play)
- Winning/losing/save pitchers
- Category tag, margin
- `batter_season_stats` (optional) — season hitting stats for batters mentioned in the payload, keyed by full name. Includes `home_runs`, `doubles`, `triples`, `rbi`, `stolen_bases`, `avg`. Only present when the pipeline successfully fetches stats from the People API.

**Preview payload** (`_build_preview_payload`):
- Team names, abbreviations, records
- Probable pitchers (or "TBD")
- National broadcast names
- Is-national flag

Both functions also return a `known_names` set used by the grounding check.

#### Grounding check

After receiving LLM prose, `_grounding_check()` verifies that every player name mentioned actually appears in the game data:

1. **Extract names** — regex matches capitalized multi-word sequences (2-3 words) like "Aaron Judge" or "Shohei Ohtani"
2. **Filter non-players** — a frozen set of ~35 known non-player names (team names like "San Francisco Giants", phrases like "World Series") are excluded
3. **Verify** — each extracted name is checked against the `known_names` set from the payload. Last-name-only matches ("Judge") are accepted by building a set of all last names from known full names
4. **Result** — if any name fails verification, the grounding check fails and the deterministic fallback is used

#### Retry and fallback

- **Retry:** On `anthropic.APIError`, retry once after `LLM_RETRY_DELAY` seconds (2s). Second failure uses fallback.
- **Storyline fallback:** `"{away} {away_score}, {home} {home_score}. {winner} earned the win. Key moment: {description}."`
- **Preview fallback:** `"{away} at {home}. {away_pitcher} vs {home_pitcher}. National broadcast on {network}."`
- **No-data shortcut:** If a `ScoredGame` has no play data, a factual one-liner is returned without calling the LLM at all.

#### Dry-run without API key

When `--dry-run` is used without an `ANTHROPIC_API_KEY`, the pipeline skips all LLM calls and uses the deterministic fallbacks directly. This allows full local iteration without any API credentials.

---

### Rendering (`render/`)

**File:** `src/mlbreview/render/pages.py`

All rendering flows through the `Digest` dataclass — the single shape all templates consume. The pipeline constructs it; renderers only read it.

#### Digest dataclass

```
Digest
├── digest_date: date
├── is_off_day: bool
├── games: list[Game]                    (Section 1: scores)
├── storylines: list[Storyline]          (Section 2: top 3)
│   └── Storyline
│       ├── scored: ScoredGame
│       └── prose: str
├── tonight: TonightPreview | None       (Section 3: preview)
│   └── TonightPreview
│       ├── scored: ScoredTonightGame
│       └── prose: str
├── transactions: list[Transaction]      (Section 4: off-field)
├── tonight_games: list[TonightGame]     (tonight's full schedule)
├── leaderboards: Leaderboards | None    (V2: player leaderboards)
├── off_day_headline (property)
├── off_day_body (property)
└── dashboard_url (property)
```

#### Jinja2 environment

The environment is configured with:
- `autoescape` enabled for HTML safety
- `trim_blocks` and `lstrip_blocks` for readable templates
- Custom filters: `format_date`, `ordinal`, `category_label`, `format_avg`, `format_era`, `format_ip`, `luck_badge`, `luck_class`

#### Templates

| Template | Renders | Used by |
|----------|---------|---------|
| `email.html.j2` | HTML email body (table-based layout for email clients) | `render_email_html()` |
| `email.txt.j2` | Plaintext email fallback | `render_email_text()` |
| `dashboard_day.html.j2` | Per-day dashboard page (modern CSS) | `render_dashboard_day()` |
| `dashboard_index.html.j2` | Archive index listing all digests | `render_dashboard_index()` |

The email HTML uses table-based layout for compatibility with email clients (Gmail, Outlook). The dashboard uses modern CSS (flexbox, CSS variables, border-radius). Both receive the same `Digest` dataclass.

#### Gmail clip protection

The email renderer logs a warning if the rendered HTML exceeds 80KB (Gmail clips emails at 102KB). This prevents the email from being silently truncated.

#### Dashboard structure

Each run writes two files:
1. `public/digests/YYYY-MM-DD/index.html` — the day's digest
2. `public/index.html` — the archive index (rebuilt from all `digests/` subdirectories)

The archive index is rebuilt on every run by scanning `public/digests/` for date-named directories, sorted newest-first.

#### V2 dashboard features

The leaderboard section on the dashboard includes:
- **Tab navigation** — Hot Hitters, Cold Hitters, Hot Pitchers, Cold Pitchers, Breakout Hitters, Breakout Pitchers (breakout tabs only appear when breakout players exist)
- **Sortable tables** — click any column header to sort ascending/descending (vanilla JS, no framework)
- **Expandable detail rows** — click a player row to reveal advanced stats (xwOBA, barrel%, FIP, xERA) and the luck status context
- **Luck status badges** — color-coded: Confirmed (green), Lucky (amber), Unlucky (red), Unconfirmed (no badge)
- **"Building up data" notice** — shown when fewer than 7 snapshots have been collected

#### V2 email teaser

The email includes a brief leaderboard teaser section (hottest hitter + hottest pitcher with key stats) and a link to the full dashboard. The teaser is absent when leaderboards are unavailable.

---

## Data model

The complete data flow from API to rendered output:

```
MLB Stats API                    Dataclasses                      Scoring              Rendering
─────────────                    ───────────                      ───────              ─────────

/schedule                        Game ─────────────────────────── ScoredGame ──────── Storyline
  (hydrate=team,linescore,       ├── gamePk                       ├── game: Game       ├── scored
   decisions)                    ├── teams, scores                ├── feed: GameFeed   └── prose: str
                                 ├── decisions                    ├── score: float         |
                                 ├── line_score                   └── category: str        |
                                 ├── margin (prop)                                         v
                                 └── extra_innings (prop)                               Digest
                                                                                        ├── games
/game/{pk}/winProbability        GameFeed                                               ├── storylines
                                 ├── plays: tuple[Play]                                 ├── tonight
                                 │   └── batter_id, pitcher_id                          ├── transactions
                                 ├── max_wpa_swing                                      └── tonight_games
                                 ├── late_inning_max_wpa                                    |
                                 └── biggest_play                                           v

/people?personIds=...            BatterSeasonStats ──────── (enriches LLM payload)
  (hydrate=stats)                ├── player_id, full_name
                                 ├── home_runs, doubles, triples
                                 ├── hits, rbi, stolen_bases
                                 └── avg

/schedule                        TonightGame ─────────────────── ScoredTonightGame ── TonightPreview
  (hydrate=probablePitcher,      ├── gamePk                       ├── game             ├── scored
   broadcasts)                   ├── teams, records               └── score: float     └── prose: str
                                 ├── probable pitchers
                                 └── broadcasts

/transactions                    Transaction
                                 ├── category (trade/IL/call-up)
                                 ├── player_name
                                 └── description
```

All dataclasses are `frozen=True` (immutable). No mutable state crosses module boundaries.

---

## Safety mechanisms

### Idempotency guard

The pipeline checks if `public/digests/{date}/index.html` already exists before running. If it does, the pipeline exits immediately. This prevents duplicate emails when multiple cron slots fire on the same day.

The workflow checks out the `gh-pages` branch into `./public` before running, so the guard sees previously deployed digests.

### Season pause

The pipeline only runs for dates between March 20 and November 10 (covering spring training ramp-up through World Series). Outside this window, the pipeline exits with code 0.

### LLM grounding check

Every LLM-generated prose string is checked for hallucinated player names before use. If a name appears in the prose but not in the game data, the prose is discarded and a deterministic template fallback is used. The digest always ships — it never contains unverified LLM output.

### Retry strategy

| Layer | Retry behavior |
|-------|---------------|
| HTTP transport (httpx) | 2 automatic retries on connection failure |
| MLB API (application) | 1 retry on HTTP 5xx, then `MlbApiError` |
| Claude API | 1 retry after 2s on `APIError`, then fallback |

### Graceful degradation

| Failure | Impact |
|---------|--------|
| One game feed fails to fetch | That game is skipped; others are scored normally |
| Season stats fetch fails | LLM prose omits season tallies; storylines still render |
| Transactions fetch fails | Off-field section is empty; rest of digest is unaffected |
| Tonight's schedule fails | No "Tonight" section; storylines still render |
| LLM call fails (both retries) | Deterministic template fallback is used |
| LLM hallucinates a name | Grounding check catches it; fallback is used |
| V2 game-log fetch fails | No snapshot written; leaderboard section absent; V1 digest ships normally |
| V2 Statcast fetch fails | Leaderboards render with UNCONFIRMED luck status for all players |
| V2 snapshot load fails | Leaderboard section absent; V1 digest ships normally |
| V2 any unexpected error | Entire V2 pipeline returns None; V1 digest ships normally |

The only hard failure is the schedule fetch for finals (`fetch_finals`). If that fails, the pipeline returns exit code 1 and no email is sent.

### Email safety

- HTML size is checked against Gmail's 102KB clip threshold
- Both HTML and plaintext versions are sent (plaintext fallback for clients that don't render HTML)
- The "from" address defaults to Resend's onboarding domain for development

---

## Deployment

### GitHub Actions workflow

**File:** `.github/workflows/digest.yml`

Three cron slots for delay tolerance (GitHub Actions cron is best-effort and routinely delays 30-120+ minutes):

| Slot | UTC | ET (EDT) | ET (EST) |
|------|-----|----------|----------|
| Primary | 09:30 | 5:30am | 4:30am |
| Backup | 10:30 | 6:30am | 5:30am |
| Fallback | 11:30 | 7:30am | 6:30am |

The idempotency guard deduplicates: the first slot to fire publishes and sends; subsequent slots no-op.

### Workflow steps

1. Checkout `main` (source code)
2. Checkout `gh-pages` into `./public` (existing dashboard for idempotency check)
3. Set up Python 3.12
4. `pip install -e .`
5. `python -m mlbreview --out-dir public`
6. Deploy `./public` to `gh-pages` via `peaceiris/actions-gh-pages@v4`

### Secrets

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude API for prose generation |
| `RESEND_API_KEY` | Resend API for email delivery |
| `DIGEST_TO_EMAIL` | Recipient email address |

### Dashboard hosting

GitHub Pages serves the `gh-pages` branch at `https://ryanlambies.github.io/mlbreview/`. Directory structure:

```
gh-pages branch root
├── index.html                     (archive listing, rebuilt daily)
├── snapshots/                     (V2: daily player stat snapshots)
│   ├── 2026-05-07.json
│   ├── 2026-05-08.json
│   └── ...
└── digests/
    ├── 2026-05-07/
    │   └── index.html             (day page)
    ├── 2026-05-08/
    │   └── index.html
    └── ...
```

---

## Key design decisions

### Star-player refresh (`scripts/refresh_stars.py`)

Standalone script that refreshes `config/stars.json` with current-season WAR leaders. Designed to run weekly (manually or via a workflow step) to keep the hype-score star-density sub-signal current as player performance shifts.

**Data source:** Baseball Reference WAR via `pybaseball` (`bwar_bat` / `bwar_pitch`). These functions cache historical data locally and don't rely on FanGraphs scraping (which can be blocked).

**Flow:**
1. Fetch all-time batter WAR data; filter to target season and position players (`pitcher == "N"`)
2. Fetch all-time pitcher WAR data; filter to target season
3. Apply `--min-war` threshold (default: 2.0)
4. Merge, deduplicate (Ohtani appears in both tables), sort by WAR descending
5. Trim to `--top` N players (default: 30)
6. Enrich with position abbreviations via MLB Stats API batch `/people` lookup
7. Write `config/stars.json` in the same format `load_star_ids()` expects

**CLI flags:**

| Flag | Default | Purpose |
|------|---------|---------|
| `--season` | Current year | MLB season to query |
| `--top` | 30 | Number of players in the output |
| `--min-war` | 2.0 | Minimum WAR threshold |
| `--dry-run` | off | Print output without writing to disk |

**Graceful degradation:** Position-lookup failure → batters get "DH", pitchers get "P". pybaseball failure → script exits with code 1 and stars.json is not modified.

**League-neutral guarantee:** WAR is the only selection criterion. Multiple players from the same team is expected when warranted by performance.

---

### Why static HTML, not a JS framework?

The dashboard is a static archive. Each day's page is generated once and never changes. There's no interactivity that justifies a framework — just styled HTML from Jinja2. This keeps the pipeline simple, the deploy fast, and the hosting free.

### Why late-inning peak |WPA| instead of Leverage Index?

Leverage Index is the ideal metric for "how tense was this moment?" but it's not available on the MLB Stats API's public endpoints. It requires Baseball Savant / Statcast via `pybaseball`, which is a V2 dependency. Late-inning peak |WPA| correlates strongly with high-LI moments in late innings and is good enough for V1 ranking. When V2 adds Statcast, the formula slot is ready for a direct swap.

### Why three cron slots?

GitHub Actions cron is best-effort. A single 09:30 UTC slot would miss delivery on mornings when GitHub delays the run by 2+ hours. Three slots (09:30, 10:30, 11:30) ensure at least one fires in time. The idempotency guard prevents duplicate sends — only the first slot to run produces output.

### Why deterministic fallbacks instead of failing?

The digest is a daily ritual. A reader expects it every morning. If the LLM is down or hallucinates, it's better to ship a factual template ("Red Sox 5, Yankees 3. Cole earned the win.") than to skip the storyline entirely or fail the pipeline. The fallback text is boring but correct.

### Why `frozen=True` dataclasses?

Every data structure crossing a module boundary is immutable. This prevents accidental mutation bugs (a scoring function modifying a `Game` object that rendering later reads) and makes the data flow easier to reason about. All state transitions happen by constructing new objects, not by mutating existing ones.

### Why parse and fetch are separate functions?

Each data module exposes both `parse_*(payload)` and `fetch_*(date, client)`. The parse functions take raw JSON dicts; the fetch functions call the API and delegate to parse. This separation makes testing straightforward — tests pass fixture JSON to parse functions without mocking HTTP.
