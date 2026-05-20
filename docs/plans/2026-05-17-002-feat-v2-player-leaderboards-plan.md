# V2 Epic Breakdown — Hot/Cold/Breakout Player Leaderboards

## Context

V1 is shipping daily and stable. The spec (R12-R15) defers three player leaderboard features to V2: hot players, cold players, and breakouts. These are the product's fantasy-aware differentiator — traditional stats lead the ranking (what fantasy managers scan for), with Statcast advanced stats as a "luck filter" to flag noise. V2 leverages the dashboard more than email (sortable/expandable tables vs. a brief teaser). A fourth item — dynamic star-player list refresh — is also V2 scope.

The central architectural question is **data persistence**: the V1 pipeline is stateless (fetch today, render, done), but rolling 7-day and 15-day windows require historical data. The plan uses daily JSON snapshots on gh-pages, fitting the existing deploy model perfectly.

---

## Epics

### U1 — Daily stat snapshot infrastructure

**Scope:** Write/read plumbing for persisting daily player stats as JSON snapshots. No pipeline wiring, no fetching — just the storage layer.

**Deliverables:**
- `src/mlbreview/data/snapshots.py` — frozen dataclasses + `write_snapshot()` / `load_snapshots(n_days)` functions
  - `DailySnapshot` — top-level container with date, hitters, starters, closers
  - `HitterDayStats` — daily counting stats (PA, AB, H, 2B, 3B, HR, RBI, SB, BB, K)
  - `StarterGameStats` — per-start stats (IP, H, ER, BB, K, HR allowed, pitch count, game date, opponent). Starters are tracked per-start rather than per-day because their evaluation window is "last 2-3 starts," not a rolling day count.
  - `CloserDayStats` — daily stats (IP, saves, blown saves, ER, K, holds)
- Snapshot path convention: `public/snapshots/YYYY-MM-DD.json`
- Round-trip serialization tests (write → read → assert equal)
- Schema documented in `docs/architecture.md`

**Files touched:** new `data/snapshots.py`, `docs/architecture.md`, new `tests/test_snapshots.py`

**Dependencies:** None
**Testable:** Unit tests pass; snapshot files round-trip correctly

---

### U2 — Statcast / pybaseball integration

**Scope:** Add `pybaseball` dependency and wrap FanGraphs leaderboard fetches for the advanced stats that power the luck filter.

**Deliverables:**
- `pybaseball` added to `pyproject.toml`
- `src/mlbreview/data/statcast.py` — `fetch_statcast_hitters(season)` and `fetch_statcast_pitchers(season)` returning `StatcastHitter` / `StatcastPitcher` frozen dataclasses
- Fields: xwOBA, barrel %, hard-hit %, FIP, xFIP, xERA (whichever pybaseball surfaces cleanly)
- Graceful degradation: Statcast fetch failure returns empty dict, pipeline continues without luck filter
- Fixture-driven tests with saved CSV/JSON from pybaseball

**Files touched:** `pyproject.toml`, new `data/statcast.py`, new `tests/test_statcast.py`, `tests/fixtures/` (new Statcast fixtures)

**Dependencies:** None (parallel with U1)
**Testable:** Unit tests pass; parse functions handle real pybaseball output shapes

---

### U3 — Traditional stats fetch + rolling aggregation

**Scope:** Fetch daily game-log stats for qualified players and compute rolling aggregates from snapshot history.

**Deliverables:**
- `src/mlbreview/data/gamelogs.py` — fetch daily game logs from MLB Stats API, split into three shapes:
  - Hitter daily stats (from boxscore or game-log endpoint)
  - Starter per-start stats (identified by role; one record per start, not per day)
  - Closer daily stats (identified by role + save/hold/blown-save fields)
- `src/mlbreview/scoring/leaderboards.py` — rolling aggregation with role-aware windows:
  - `compute_rolling_hitter_stats(snapshots, window)` → `RollingHitterStats` (7-day counting stats)
  - `compute_recent_starter_stats(snapshots, n_starts)` → `RecentStarterStats` (last 2-3 starts aggregate: ERA, K/9, IP/start, WHIP)
  - `compute_rolling_closer_stats(snapshots, window)` → `RollingCloserStats` (7-day saves, blown saves, ERA, K rate)
- Leaderboard config constants added to `config.py`:
  - `ROLLING_WINDOW_DAYS = 7`
  - `BREAKOUT_WINDOW_DAYS = 15`
  - `MIN_PA_HITTER = 15` (7-day minimum plate appearances)
  - `STARTER_EVAL_STARTS = 3` (number of recent starts to evaluate)
  - `MIN_CLOSER_OPPORTUNITIES = 2` (minimum save opportunities in window to qualify)
  - `LEADERBOARD_SIZE = 10` (players per leaderboard)
- Tests with multi-day snapshot fixtures verifying rolling math for all three player types

**Files touched:** new `data/gamelogs.py`, new `scoring/leaderboards.py`, `config.py`, new `tests/test_gamelogs.py`, new `tests/test_leaderboards.py`

**Dependencies:** U1 (snapshot read/write)
**Testable:** Rolling aggregation tests pass with fixture data; qualified-player filtering works; starter window is start-count-based, not day-based

---

### U4 — Leaderboard scoring + luck filter

**Scope:** The core V2 product logic. Eight leaderboards (hitters, starters, closers — each hot/cold, plus hitter breakouts) ranked by traditional stats, each entry annotated with a luck status from Statcast.

**Deliverables:**
- Eight leaderboards:
  - **Hot/Cold Hitters** — composite of AVG, HR, RBI over 7-day window
  - **Hot/Cold Starters** — ranked by last 2-3 start quality: ERA, K/9, IP depth, WHIP
  - **Hot/Cold Closers** — ranked by save conversion rate, blown saves, ERA over 7-day window
  - **Breakout Hitters** — 7-day hot AND 15-day rolling also elevated
  - **Breakout Starters** — last 3 starts trending up AND 15-day window confirms improvement
- `LuckStatus` enum: `CONFIRMED`, `LUCKY`, `UNLUCKY`, `UNCONFIRMED` (no Statcast data available)
- Luck filter logic:
  - Hot hitter + high xwOBA/barrel% → CONFIRMED
  - Hot hitter + low xwOBA/barrel% → LUCKY (streak may regress)
  - Cold hitter + high xwOBA/barrel% → UNLUCKY (likely to bounce back)
  - Cold hitter + low xwOBA/barrel% → CONFIRMED
  - Hot starter + low FIP/xERA → CONFIRMED (underlying quality matches results)
  - Hot starter + high FIP/xERA → LUCKY (got away with it)
  - Cold starter + low FIP/xERA → UNLUCKY (better than results suggest)
  - Cold starter + high FIP/xERA → CONFIRMED
  - Closers: FIP/xERA filter applies the same way
- Leaderboard entry dataclass with player name, team, role, traditional stats, luck status, and optional detail stats
- `docs/formulas.md` updated with luck-filter explanation and role-specific evaluation logic

**Files touched:** `scoring/leaderboards.py` (extend from U3), `config.py` (luck-filter thresholds), `docs/formulas.md`, `tests/test_leaderboards.py` (extend)

**Dependencies:** U2 (Statcast data), U3 (rolling stats)
**Testable:** Unit tests verify ranking order per role, luck status assignment, breakout qualification, edge cases (no Statcast data → UNCONFIRMED, starter with only 1 start still ranks)

---

### U5 — Dashboard rendering + email teasers + pipeline wiring

**Scope:** Wire everything into the pipeline. Replace the dashboard V2 placeholder with actual leaderboard tables. Add email teasers.

**Deliverables:**
- `Digest` dataclass extended with leaderboard fields (hot/cold/breakout lists)
- `dashboard_day.html.j2` — replace `<!-- V2 placeholder -->` with leaderboard section:
  - Tables with column-header sorting (minimal vanilla JS, progressive enhancement)
  - Expandable detail rows showing advanced stats + luck status badge
  - "Building up data" notice during bootstrap period (< 7 days of snapshots)
- `email.html.j2` / `email.txt.j2` — 2-3 line teaser section ("Hottest hitter: X (.412 AVG, 4 HR) — see full leaderboards on the dashboard →")
- `pipeline.py` gains V2 data flow:
  1. Fetch daily game logs
  2. Fetch Statcast (if available)
  3. Write today's snapshot
  4. Load last N snapshots
  5. Compute rolling stats
  6. Score leaderboards + apply luck filter
  7. Attach to Digest
- All V2 steps wrapped in try/except — V1 always ships even if V2 data fails
- `docs/architecture.md` updated with full V2 pipeline flow

**Files touched:** `pipeline.py`, `render/pages.py`, `templates/dashboard_day.html.j2`, `templates/email.html.j2`, `templates/email.txt.j2`, `docs/architecture.md`, new `tests/test_pipeline_v2.py` or extend `tests/test_pipeline.py`

**Dependencies:** U1-U4
**Testable:** Full dry-run produces dashboard with leaderboard section; email includes teaser; V2 failure gracefully degrades to V1-only output

---

### U6 — Dynamic star-player list refresh

**Scope:** Standalone script to refresh `config/stars.json` based on current-season fWAR leaders via pybaseball. Independent of leaderboards.

**Deliverables:**
- `scripts/refresh_stars.py` — fetches fWAR leaders, writes `config/stars.json`
- Configurable: top N players by fWAR (default ~30), minimum fWAR threshold
- Optional weekly workflow step in `digest.yml` or separate workflow
- Preserves league-neutral guarantee (no team bias in selection)

**Files touched:** new `scripts/refresh_stars.py`, `config/stars.json` (output), optionally `.github/workflows/`, `docs/architecture.md`

**Dependencies:** U2 (pybaseball already added)
**Testable:** Script runs and produces valid JSON; output has expected shape; no single team dominates the list

---

## Resolved decisions

1. **pybaseball reliability** — Graceful degradation only. If Statcast fetch fails, leaderboards render with `UNCONFIRMED` luck status. No caching layer. The luck filter is a quality enhancement, not the core product.

2. **Game-log data source** — Boxscore hydration (`/schedule?hydrate=boxscore`). One call per game (~15/day). Gets every player's daily line and cleanly identifies starters vs closers via game role. Follows V1's existing hydration pattern.

3. **Snapshot retention** — Keep full season. ~14MB is negligible against GitHub Pages' 1GB limit. No pruning logic needed.

4. **Leaderboard thresholds** — Ship with defaults (15 PA / 3 starts / 2 save opps), calibrate after 2 weeks of real output. All values live in `config.py`.

5. **Vanilla JS scope** — Click column header to sort, click row to expand/collapse detail stats + luck status. No filtering, no search, no URL state. Progressive enhancement — tables readable without JS.

## Verification

After all epics ship:
1. Run `python3 -m mlbreview --dry-run` — produces dashboard with leaderboard section (or "building up data" notice if < 7 snapshots exist)
2. After 7+ daily runs, leaderboards populate with ranked players and luck status badges
3. Statcast fetch failure → leaderboards still render with `UNCONFIRMED` luck status
4. Game-log fetch failure → V1 digest ships normally, V2 section absent
5. All existing V1 tests still pass
6. Dashboard loads in browser, tables sort on column click, rows expand to show detail stats
