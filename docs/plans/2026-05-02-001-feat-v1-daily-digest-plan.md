---
title: "feat: V1 daily MLB digest pipeline"
type: feat
status: active
date: 2026-05-02
origin: docs/brainstorms/2026-05-02-mlbreview-digest-requirements.md
---

# feat: V1 daily MLB digest pipeline

## Summary

Build the V1 daily digest as a Python project that runs as a GitHub Actions cron job at 5:30am EST, fetches MLB data, scores stories and games, generates LLM-written prose with Claude Haiku 4.5, renders email + dashboard from Jinja2 templates, sends the email via Resend, and deploys the dashboard to GitHub Pages with a per-day archive at `/digests/YYYY-MM-DD/`. Six implementation units: project skeleton, data fetch layer, scoring formulas, renderers, orchestrator, and the GitHub Actions workflow. Drama, hype, and variety-rule logic ship with explicit docstrings and a companion `docs/formulas.md` so the heart of the product stays legible and tunable.

---

## Problem Frame

The brainstorm doc covers the user-facing pain in detail (see origin). For planning purposes the salient framing is: this is a greenfield Python project with no existing patterns to extend, multiple integrations (MLB Stats API, Anthropic Claude, Resend, GitHub Actions, GitHub Pages), and three custom scoring/curation rules (drama, hype, variety) that are the product's identity. The plan's job is to make all of that buildable as a sequence of focused, well-tested units while keeping the formula logic visible and easy to iterate on.

---

## Requirements

Carrying forward from origin (see `docs/brainstorms/2026-05-02-mlbreview-digest-requirements.md`). All R-IDs match origin numbering. Plan-derived requirements use a `P-` prefix to distinguish from origin requirements.

**Origin requirements addressed by V1**

- R1. Daily digest email at 5:30am EST during regular season + postseason
- R2. Public dashboard at `https://ryanlambies.github.io/mlbreview/digests/YYYY-MM-DD/`
- R3. Email contains above-the-fold preview + prominent "Open in dashboard →" link
- R4. Pause delivery during spring training and offseason
- R5. Scores recap section
- R6. Top 3 storylines section (LLM prose, drama-ranked)
- R7. Tonight's most-hyped game preview section
- R8. Off-field news brief section (factual bullets only)
- R9. Drama-formula ranking with WPA, leverage, margin
- R10. League-neutral ranking + variety rule
- R11. Composite hype score (pitching + stars + stakes + national broadcast)
- R16. LLM grounded in structured game data; no fabrication
- R17. Off-day "no games last night" handling
- R18. Static HTML dashboard via Jinja2, deployed by the same Action
- R19. Public dashboard, no auth
- R20. Dashboard index page lists recent digests

**Origin requirements explicitly deferred to V2**

- R12, R13, R14, R15 (hot/cold/breakouts) — V1 must not paint these into a corner; data model and dashboard layout designed-for them but not implemented

**Plan-derived requirements**

- P1. Drama formula, hype formula, and storyline variety rule each ship with full docstrings on the relevant functions AND are explained in plain language in `docs/formulas.md`. The formulas are the heart of the product; future tuning sessions must be able to read the doc and the code and understand the logic without spelunking.
- P2. All scoring weights, thresholds, and the star-player list live in a single, well-commented `config.py` (or `config/` directory) so calibration can happen without touching scoring code.
- P3. The pipeline supports a `--dry-run` mode that fetches real data, runs scoring + LLM, prints rendered HTML to stdout, and skips Resend send + git commit. Used for local iteration.
- P4. The Anthropic API key and Resend API key are loaded from environment variables only (never committed). `.env.example` documents what's needed; GitHub Actions secrets supply them in production.

**Origin actors:** none specified (single-user system; the user is the only human actor)
**Origin flows:** F1 (Nightly digest generation and delivery), F2 (Off-day / no-games handling)
**Origin acceptance examples:** AE1 (covers R6, R10), AE2 (covers R8), AE3 (covers R17), AE4 (covers R1, R4), AE5 (covers R3)

---

## Scope Boundaries

- **No V2 work.** Hot/cold/breakout leaderboards, Statcast/pybaseball integration, and 7d/15d player rolling stats are deferred. V1's data model leaves clean seams (especially in the dashboard template's section list and the data layer's player-stats placeholders) but does not implement them.
- **No SMS, push notifications, or alternate delivery channels.** Email is the only push channel for V1.
- **No team-bias / favorite-team mode.** League-neutral is the product identity.
- **No live re-polling for missing WPA data.** Pipeline runs at 5:30am ET when all games are final; if `winProbability` is genuinely missing for a play, drop it from the candidate set rather than retry-loop. Retry on the rare full-game-missing case is a future iteration.
- **No CI tests against the live MLB API.** Tests use recorded JSON fixtures + mocked clients. Live integration is verified manually via dry-run.
- **No custom domain.** The default `https://ryanlambies.github.io/mlbreview/` URL is fine for V1.
- **No real-time monitoring beyond GitHub's built-in failure notifications.** V1 success criterion is ≥99% job success; GitHub native alerts cover that.
- **No auth on the dashboard.** Content is non-sensitive baseball recaps.

### Deferred to Follow-Up Work

- **V2 player leaderboards** (hot/cold/breakouts): separate plan, separate implementation, after V1 is producing daily output reliably for ≥2 weeks.
- **Domain verification on Resend**: when polish matters more than zero-config startup. Switches sender from `onboarding@resend.dev` to `digest@<verified-domain>`.
- **Star-player list refresh automation**: V1 ships with a manually curated `config/stars.json`; a "refresh based on rolling fWAR" script is V2.
- **Formula calibration pass**: scheduled for week 2 of V1 in production, after observing real rankings on real games.

---

## Context & Research

### Relevant Code and Patterns

- The repo is greenfield — no existing patterns to follow. CLAUDE.md establishes lens, scope, and tech stack.
- The brainstorm doc (`docs/brainstorms/2026-05-02-mlbreview-digest-requirements.md`) is the source of truth for product intent.

### Institutional Learnings

- None — `docs/solutions/` does not exist in this repo yet.

### External References

Findings from the framework-docs research pass:

- **GitHub Pages — two viable options, V1 chooses the `gh-pages` branch.** Option A: `actions/deploy-pages@v4` + `actions/upload-pages-artifact@v3` with Pages source set to "GitHub Actions". Each deploy is full-replace, so the archive must be rebuilt locally and committed back to `main` each run. Option B: `peaceiris/actions-gh-pages@v4` pushes to a dedicated `gh-pages` branch. V1 chooses Option B because (a) the workflow's write permission is scoped to a single branch instead of `main`, (b) `main` stays free of daily bot commits, and (c) the action handles branch creation, archive preservation, and publishing in a single step — fewer moving parts and a smaller blast radius if the workflow is ever compromised. ([peaceiris/actions-gh-pages](https://github.com/peaceiris/actions-gh-pages), [actions/deploy-pages](https://github.com/actions/deploy-pages))
- **MLB Stats API:** Base `https://statsapi.mlb.com/api/v1/`, no auth, no documented rate limits. Key endpoints: `/schedule?sportId=1&date=...&hydrate=team,linescore,decisions` for finals, `/game/{gamePk}/feed/live` (richer than `/winProbability`) for play-by-play with leverageIndex + WPA, `/schedule?...&hydrate=probablePitcher,broadcasts,linescore,team` for tonight's preview, `/transactions?startDate=...&endDate=...` for the news brief. National broadcast detection: filter `broadcasts[].isNational == true`. ([MLB-StatsAPI Endpoints wiki](https://github.com/toddrob99/MLB-StatsAPI/wiki/Endpoints))
- **MLB-StatsAPI Python wrapper:** `MLB-StatsAPI` v1.9.0 is current and stable. Useful for `schedule()` post-processing (it pulls out `home_probable_pitcher`, `national_broadcasts` directly). For WPA / leverage there is no high-level wrapper — call `/feed/live` with `httpx` directly.
- **Anthropic Python SDK:** `claude-haiku-4-5` (alias) is the right model for short grounded prose at 2-3 sentences. Plain `messages.create` with a system prompt instructing strict grounding; skip prompt caching for V1 (system prompt too short to benefit). Estimated cost ≈ $0.006/day. ([Claude models overview](https://platform.claude.com/docs/en/about-claude/models/overview))
- **Resend Python SDK:** `resend` package, free tier 100/day, `onboarding@resend.dev` works on the free tier without domain verification (single recipient on your account). Both `html` and `text` keys in the same send produce a multipart message. ([Resend Python quickstart](https://resend.com/docs/send-with-python))
- **GitHub Actions cron quirks:** UTC-only, 5–30 min delays normal, occasional drops. Best practice: schedule both `30 9 * * *` and `30 10 * * *` to cover EST/EDT, guard inside the script with `zoneinfo.America/New_York` hour check, always include `workflow_dispatch` for manual reruns. ([Community discussion #156282](https://github.com/orgs/community/discussions/156282))

---

## Key Technical Decisions

- **GitHub Pages via `peaceiris/actions-gh-pages@v4` to a dedicated `gh-pages` branch:** Chosen for two reasons. (1) **Security:** the workflow's `GITHUB_TOKEN` only ever writes to `gh-pages`. `main` (source code) is never modified by the runner, which meaningfully reduces blast radius if the workflow is ever compromised. (2) **Simplicity:** one workflow step handles branch creation, archive preservation, and publish. The pipeline writes rendered HTML to a local out_dir; the action publishes that dir as the new `gh-pages` HEAD. Pages source set to "Deploy from a branch → `gh-pages`" in repo Settings → Pages.
- **`httpx` direct + `MLB-StatsAPI` for `schedule()` only:** The wrapper covers the easy endpoint well; for `/feed/live` and `/transactions`, raw `httpx` is simpler than fighting the wrapper. Hybrid avoids over-coupling to one library.
- **Claude Haiku 4.5 for storyline + preview prose:** Short grounded prose is exactly Haiku's wheelhouse; Sonnet is overkill at 3× the cost. Plain `messages.create` with a strict-grounding system prompt; no caching for V1.
- **Static curated `config/stars.json` for the hype score:** Manual list of ~30 players keyed by MLBAM player ID. Refreshed manually pre-season. "Dynamic star detection" is a V2 problem.
- **All weights and thresholds in `src/mlbreview/config.py`:** Drama formula weights, hype formula weights, variety-rule thresholds, max storylines, off-day phrasing — every tunable lives in one file with comments. Calibration sessions touch one file.
- **Formula documentation is a first-class deliverable:** Every scoring function has a full docstring (formula, inputs, outputs, rationale, tunable knobs), and `docs/formulas.md` carries a plain-language explainer of drama, hype, and the variety rule for non-implementer readers / future-self tuning sessions. (Per P1.)
- **`onboarding@resend.dev` sender for V1:** Skip domain verification. Polish later. Single recipient on the free tier.
- **DST guard inside the pipeline, not in the workflow:** Schedule both `30 9 * * *` and `30 10 * * *` UTC; the pipeline checks `datetime.now(ZoneInfo("America/New_York")).hour == 5` and `SystemExit(0)` otherwise. Simpler than conditional cron logic.
- **Off-day and season-pause handling are orchestrator branches, not separate units:** Both are early-exit / alternate-template paths inside the main pipeline; making them separate units would over-fragment the work.
- **Dry-run mode for local iteration:** `python -m mlbreview --dry-run` runs the full pipeline against live data but writes rendered HTML to a local `./public/` (or `--out-dir`) and skips Resend send + Pages publish. Lets us iterate on prompts and templates without touching production.
- **Test strategy:** Golden JSON fixtures (real games captured once and committed) drive scoring + rendering tests. Mocked `httpx` and `anthropic` clients drive orchestrator tests. No live network in CI.

---

## Open Questions

### Resolved During Planning

- **GitHub Pages mechanism:** `peaceiris/actions-gh-pages@v4` publishing to a dedicated `gh-pages` branch. Chosen over `actions/deploy-pages@v4`-on-main because the runner only needs write access to one branch, `main` stays free of daily bot commits, and the action handles archive preservation in one step. (Was R2 deferred-to-planning in origin.)
- **Email sender for V1:** `onboarding@resend.dev` — resolved per research. Domain verification is a follow-up item.
- **WPA / drama formula initial weights:** `0.5 × normalized_max_WPA + 0.3 × normalized_late_leverage + 0.2 × inverse_margin`. Calibratable in `config.py`. Re-tuned in week 2 against real digests. (Was R9 deferred-to-planning in origin.)
- **Hype formula initial weights:** `0.35 × pitching_quality + 0.25 × stakes + 0.20 × star_density + 0.20 × national_broadcast_flag`. Same calibration plan. (Was R11 deferred-to-planning in origin.)

### Deferred to Implementation

- **Exact LLM prompt + JSON payload shape for storylines / preview:** Will be iterated during U5/U6 against real games. The plan locks the model and the strict-grounding system prompt; the user-message JSON shape and few-shot exemplars (if any) emerge from dry-run iteration.
- **Dashboard visual design (color, type, chart library, responsive breakpoints):** Best handled iteratively during U4 with screenshots. Plan locks "static HTML, minimal CSS, hand-drawn SVG for WPA charts if any" but defers the look.
- **Star-player list initial contents:** Curate during U3 against current top-30 by 2025 fWAR / star recognition. ~30 player IDs. Stored as `config/stars.json`.
- **Storyline variety rule precise threshold:** Plan default — when any two of the top 5 candidates share a category and their scores are within 10% of each other, demote the second and pick the next-highest from a different category. The 10% threshold is calibratable. Will revisit after a week of real output.

---

## Output Structure

```
mlbreview/
├── CLAUDE.md
├── README.md
├── pyproject.toml                          # NEW (replaces requirements.txt)
├── .env.example                            # already exists
├── .python-version                         # NEW (3.12)
├── docs/
│   ├── brainstorms/
│   │   └── 2026-05-02-mlbreview-digest-requirements.md
│   ├── plans/
│   │   └── 2026-05-02-001-feat-v1-daily-digest-plan.md  (this file)
│   └── formulas.md                         # NEW — plain-language formula explainer
├── src/
│   └── mlbreview/
│       ├── __init__.py                     # already exists (empty)
│       ├── __main__.py                     # NEW — entrypoint
│       ├── config.py                       # NEW — all tunable knobs
│       ├── data/
│       │   ├── __init__.py
│       │   ├── client.py                   # NEW — httpx wrapper
│       │   ├── schedule.py                 # NEW — schedule + finals
│       │   ├── game.py                     # NEW — feed/live, WPA
│       │   └── transactions.py             # NEW — news-brief data
│       ├── scoring/
│       │   ├── __init__.py
│       │   ├── drama.py                    # NEW — drama formula
│       │   ├── hype.py                     # NEW — hype formula
│       │   └── variety.py                  # NEW — variety rule
│       ├── llm.py                          # NEW — Anthropic client + prompt
│       ├── render/
│       │   ├── __init__.py
│       │   └── pages.py                    # NEW — Jinja2 wrapper
│       └── pipeline.py                     # NEW — orchestrator
├── templates/
│   ├── email.html.j2                       # NEW
│   ├── email.txt.j2                        # NEW
│   ├── dashboard_day.html.j2               # NEW
│   └── dashboard_index.html.j2             # NEW
├── config/
│   └── stars.json                          # NEW — curated star-player MLBAM IDs
├── tests/
│   ├── __init__.py                         # NEW
│   ├── fixtures/                           # NEW — recorded JSON
│   │   ├── schedule_2025-08-15.json
│   │   ├── feed_live_walkoff.json
│   │   ├── feed_live_blowout.json
│   │   └── transactions_sample.json
│   ├── test_drama.py                       # NEW
│   ├── test_hype.py                        # NEW
│   ├── test_variety.py                     # NEW
│   ├── test_pipeline.py                    # NEW
│   └── test_render.py                      # NEW
└── .github/
    └── workflows/
        └── digest.yml                      # NEW — cron + Pages deploy
```

> *This is a scope declaration showing the expected layout, not a constraint. Implementation may adjust if a better structure surfaces.*
>
> *The rendered dashboard archive (`digests/YYYY-MM-DD/index.html`, root `index.html`) lives on the `gh-pages` branch, not in `main`. It is generated into a local `./public/` directory at run time and published by `peaceiris/actions-gh-pages` — `main` only contains source code, brainstorm docs, and plans.*

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
GitHub Actions cron (5:30 ET, both UTC slots)
        │
        ▼
  pipeline.run()
        │
   DST/season guard ──▶ (off-season) early exit
        │
        ▼
  data.fetch_yesterday() ──┬──▶ schedule + finals
                           ├──▶ /feed/live for each game (WPA, leverage, plays)
                           └──▶ /transactions (24h window)
        │
        ▼
  scoring.score_storylines(games) ──▶ drama_score per game ──▶ top-3 + variety filter
        │
        ▼
  data.fetch_tonight() ──▶ scoring.score_hype() ──▶ top-1 hyped game
        │
        ▼
  llm.write_storylines(top3) + llm.write_preview(hyped) ──▶ prose strings
        │
        ▼
  render.render_dashboard(date, ...) ──▶ ./public/digests/YYYY-MM-DD/index.html
  render.render_index(recent_dates) ──▶ ./public/index.html
  render.render_email(...) ──▶ html + text strings
        │
        ▼
  resend.send(html, text)
        │
        ▼
  peaceiris/actions-gh-pages publishes ./public/ ──▶ gh-pages branch ──▶ live dashboard
```

Two early-exit branches inside `pipeline.run()` cover the off-day case (R17, AE3 — fewer games yield "no games" template + tonight's schedule) and the season-pause case (R4, AE4 — exit silently between World Series end and Opening Day).

---

## Implementation Units

- U1. **Project skeleton and configuration**

**Goal:** Stand up the Python project so all subsequent units have a place to land — package layout, dependency manifest, config module, env loading, logging, dry-run flag, basic test harness.

**Requirements:** P2, P3, P4

**Dependencies:** none

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `src/mlbreview/__main__.py`
- Create: `src/mlbreview/config.py`
- Create: `src/mlbreview/data/__init__.py`, `src/mlbreview/scoring/__init__.py`, `src/mlbreview/render/__init__.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Modify: `requirements.txt` (delete — replaced by `pyproject.toml`) or keep as a pinned export
- Modify: `.env.example` (already exists; verify keys match what `config.py` reads)

**Approach:**
- `pyproject.toml` declares deps: `httpx`, `anthropic>=0.42`, `resend`, `jinja2`, `MLB-StatsAPI>=1.9`, `python-dateutil`, plus dev deps `pytest`, `pytest-mock`
- `config.py` exposes a single `Config` dataclass loaded from env + the constants for all formula weights and thresholds. Every tunable lives here with a comment naming the formula it belongs to.
- `__main__.py` is a thin CLI wrapper: parses `--dry-run` and `--date YYYY-MM-DD` flags, calls `pipeline.run()`. Default is "yesterday's date in America/New_York at run time."
- Logging: standard library `logging` at INFO level; one module-level logger per file.

**Patterns to follow:**
- Standard `src/<package>/` layout (PyPA packaging guide). No framework.

**Test scenarios:**
- Happy path: `pytest` discovers and runs the (still-empty) test suite without error.
- Happy path: `python -m mlbreview --help` prints usage; `python -m mlbreview --dry-run` exits 0 (with subsequent units installed it does real work; for now it should at least not crash on import).
- Edge case: `Config.load()` raises a clear error if `ANTHROPIC_API_KEY` or `RESEND_API_KEY` is missing AND not in `--dry-run` mode (dry-run skips the LLM and email so missing keys are tolerated).

**Verification:**
- The package imports cleanly. The CLI entrypoint runs. `pytest` runs zero tests successfully. Dependencies install via `pip install -e .[dev]`.

---

- U2. **MLB Stats API data fetch layer**

**Goal:** Pure functions that fetch the data we need from MLB Stats API and return well-typed Python structures. No scoring, no rendering, no LLM. This is the I/O boundary.

**Requirements:** R5, R6, R7, R8, R17

**Dependencies:** U1

**Files:**
- Create: `src/mlbreview/data/client.py` — shared `httpx.Client` with timeout + retries
- Create: `src/mlbreview/data/schedule.py` — `fetch_finals(date)` and `fetch_tonight(date)`
- Create: `src/mlbreview/data/game.py` — `fetch_game_feed(gamePk)` returning play list with WPA + leverage
- Create: `src/mlbreview/data/transactions.py` — `fetch_transactions(start, end)`
- Create: `tests/fixtures/schedule_2025-08-15.json`, `tests/fixtures/feed_live_walkoff.json`, `tests/fixtures/feed_live_blowout.json`, `tests/fixtures/transactions_sample.json` (recorded once via dev script, committed)
- Create: `tests/test_data.py`

**Approach:**
- One `httpx.Client` with `timeout=30, transport=httpx.HTTPTransport(retries=2)`.
- `fetch_finals(date)` calls `/schedule?sportId=1&date=YYYY-MM-DD&hydrate=team,linescore,decisions`. Returns a list of `Game` dataclasses (gamePk, away/home name + abbr, away/home score, status, decisions). Filter to status == 'Final' or 'Game Over'.
- `fetch_game_feed(gamePk)` calls `/game/{gamePk}/feed/live` and pulls out the play list with WPA + leverage. Returns a `GameFeed` dataclass with plays, max_wpa_swing, late_inning_leverage_max, margin, line_score.
- `fetch_tonight(date)` calls `/schedule?...&hydrate=probablePitcher,broadcasts,linescore,team`. Returns `TonightGame` dataclasses with probable pitchers and broadcast info (including `is_national: bool`).
- `fetch_transactions(start, end)` calls `/transactions?startDate=...&endDate=...`. Returns `Transaction` dataclasses with type (trade, IL, debut), date, player, team, description.
- Filter scope: only `gameType in ('R', 'F', 'D', 'L', 'W')` (regular + postseason) — drops spring training (S) and exhibitions (E). Used in season-pause guard as well.

**Patterns to follow:**
- None — greenfield. Establish the pattern: pure fetch functions returning dataclasses, no business logic.

**Test scenarios:**
- Happy path (fixture-driven): `fetch_finals('2025-08-15')` against the recorded fixture returns the expected number of `Game` instances with correct teams + scores.
- Happy path: `fetch_game_feed(walkoff_pk)` against the walkoff fixture returns plays with non-null WPA and a max_wpa_swing > 0.3 (calibration sanity).
- Edge case: empty `/schedule` (no games on this date — All-Star break) returns `[]` without error.
- Edge case: a play with `winProbabilityAdded == None` is silently dropped from the contributing list, not surfaced as `None` to scoring. *Covers AE3 indirectly (off-day path needs clean empty-list handling).*
- Error path: HTTP 500 from MLB API triggers retry; persistent failure raises a clear `MlbApiError` (caller decides to fail the run).

**Verification:**
- All fetch functions return typed Python objects. Tests pass against recorded fixtures. No live network in CI.

---

- U3. **Drama, hype, and variety scoring formulas**

**Goal:** Pure scoring functions for storyline drama, game hype, and the storyline variety rule — the heart of the product. Every formula is fully documented in code AND in `docs/formulas.md`.

**Requirements:** R9, R10, R11, P1, P2

**Dependencies:** U1, U2 (uses the dataclasses defined there)

**Files:**
- Create: `src/mlbreview/scoring/drama.py` — `drama_score(game_feed) -> float` and `select_top_storylines(games, k=3) -> list[Game]`
- Create: `src/mlbreview/scoring/hype.py` — `hype_score(tonight_game, stars, standings) -> float` and `select_most_hyped(games) -> Game`
- Create: `src/mlbreview/scoring/variety.py` — `apply_variety_rule(scored_candidates, threshold=0.10) -> list[Game]`
- Create: `config/stars.json` — curated MLBAM player IDs (~30 players) with name + position
- Create: `docs/formulas.md` — plain-language explainer
- Create: `tests/test_drama.py`, `tests/test_hype.py`, `tests/test_variety.py`

**Approach:**
- **Drama formula** (initial weights, calibratable in `config.py`):
  - `drama = 0.5 × norm_max_wpa + 0.3 × norm_late_leverage + 0.2 × inverse_margin`
  - `norm_max_wpa` is the max single-play |WPA| in the game, normalized to [0,1] by dividing by 0.5 (the practical ceiling for a single play)
  - `norm_late_leverage` is the max leverage index for any play in innings ≥ 7, normalized by dividing by 5.0
  - `inverse_margin` is `1 / (1 + final_margin)` — penalizes blowouts
  - Each game also carries a `category` tag: `walkoff`, `comeback`, `pitchers_duel`, `feat` (cycle/no-no/multi-HR), `standings_swing`, `extra_innings`, `default`
- **Hype formula** (initial weights, calibratable):
  - `hype = 0.35 × pitching_quality + 0.25 × stakes + 0.20 × star_density + 0.20 × national_broadcast_flag`
  - `pitching_quality` = average inverse-ERA across both starters, normalized
  - `stakes` is a discrete score: division rivals (+0.4), both teams above .500 (+0.3), playoff-race delta within 3 games (+0.3)
  - `star_density` = count of stars on either roster's lineup (clamped to [0,1] via dividing by 4)
  - `national_broadcast_flag` is 1.0 if any broadcast has `is_national == true`, else 0.0
- **Variety rule:**
  - Sort all candidate storylines by drama_score desc, take top 5
  - Walk in order; for each candidate, check if its category was already picked AND its score is within `variety_threshold` (default 0.10) of the prior pick. If so, skip and try the next-highest of a different category. Else accept.
  - Stop at 3 selected.
- **Documentation requirement (P1):** Each function above carries a complete docstring covering: formula in math, inputs (with units), output (range + meaning), rationale (why this weight, why this normalization), tunable knobs (where in `config.py` to change). `docs/formulas.md` carries the same explanation in plain English for readers who don't want to read code.

**Patterns to follow:**
- Pure functions with explicit, tested numeric behavior. Inputs are dataclasses from U2; outputs are floats or lists.

**Test scenarios:**
- *Drama, happy path:* `drama_score(walkoff_fixture)` > `drama_score(blowout_fixture)`. Walk-off scores in the top quartile [0.75, 1.0]; blowout scores in the bottom quartile.
- *Drama, edge case:* a 2-1 game with no late leverage scores higher than a 12-1 game even if max_wpa is similar (margin penalty kicks in).
- *Drama, edge case:* a game with empty plays list (data error) returns 0.0, never crashes.
- *Hype, happy path:* `hype_score(yankees_dodgers_friday_apple_tv)` > `hype_score(rockies_athletics_local)` — covers all four sub-signals.
- *Hype, edge case:* unknown probable pitcher (None) treated as league-average, doesn't crash.
- *Variety, happy path:* given 5 candidates `[walkoff (0.92), multi_hr_a (0.88), multi_hr_b (0.85), feat (0.84), standings_swing (0.80)]` and threshold 0.10, the rule selects `[walkoff, multi_hr_a, feat]` (dropping multi_hr_b for category dup within threshold). **Covers AE1.**
- *Variety, edge case:* if all 5 candidates are the same category, return the top 3 anyway (don't return fewer than k).
- *Variety, edge case:* threshold = 0 means "always pick top 3 regardless of category" — sanity-check the toggle.
- *Documentation:* `docs/formulas.md` exists and contains a plain-language section per formula plus a "tuning guide" section noting which `config.py` keys to change for what behavior. (Verified by grep, not by unit test.)

**Verification:**
- All scoring tests pass. `docs/formulas.md` reads cleanly and a non-implementer can understand why a given game beat another.

---

- U4. **Jinja2 renderers — email + dashboard**

**Goal:** Convert the structured digest data (scores, top 3 storylines, hyped game, news brief) into rendered HTML/text. Email + per-day dashboard + index.

**Requirements:** R2, R3, R5, R6, R7, R8, R18, R19, R20

**Dependencies:** U1

**Files:**
- Create: `src/mlbreview/render/pages.py` — Jinja2 environment + `render_email(...)`, `render_dashboard_day(...)`, `render_dashboard_index(recent_dates)`
- Create: `templates/email.html.j2` — above-the-fold preview + "Open in dashboard →" CTA + sections in order
- Create: `templates/email.txt.j2` — plaintext fallback
- Create: `templates/dashboard_day.html.j2` — full visual digest
- Create: `templates/dashboard_index.html.j2` — recent-digests archive list
- Create: `tests/test_render.py`

**Approach:**
- Single `jinja2.Environment` configured with `autoescape=True`, `trim_blocks=True`, `lstrip_blocks=True`.
- Email template (HTML): inline CSS only, ≤102KB rendered (Gmail clip threshold), single-column responsive table layout. Above-the-fold: "MLB Digest — Aug 15, 2025" headline, scores summary, "Open in dashboard →" button, then top 3 storyline blurbs, then tonight's preview, then news brief at bottom.
- Email template (text): plain ASCII version of the same content, no decoration; same dashboard URL.
- Dashboard day template: same content, but uses block-level layout, can include WPA mini-charts (hand-drawn SVG generated server-side from the play list — defer the chart implementation as a stretch goal; placeholder div for V1 is fine), expandable game cards. Reserves visual space at the bottom for V2 hot/cold/breakouts (commented placeholder section so V1 doesn't paint into a corner).
- Dashboard index template: list of last 30 digests with date + storyline #1 headline; older items collapsed.
- All templates receive the same `digest` dataclass shape; render functions take that dataclass + render-time context (date, dashboard URL).

**Patterns to follow:**
- Jinja2 best practices for email-safe HTML (avoid `<style>` blocks; inline everything; use tables for layout on email).

**Test scenarios:**
- *Happy path:* `render_email(digest_fixture)` returns a string containing all four section headers (Scores, Storylines, Tonight, Off-field). **Covers AE5** (above-the-fold preview + dashboard link).
- *Happy path:* `render_dashboard_day(digest_fixture)` includes a `<a href="...">` link to the same date's dashboard URL.
- *Edge case (off-day):* `render_email(empty_digest)` produces a "no games last night" email with tonight's schedule and a placeholder dashboard link. **Covers AE3.**
- *Edge case:* news brief with zero items renders as a section that simply omits the brief (or shows "No notable transactions") — does not break layout.
- *Edge case:* email HTML output is ≤102KB for a typical digest. Soft assertion: log a warning at >80KB so we know we're approaching the cliff.
- *Integration:* the dashboard index template renders correctly given a list of 30 daily entries with mismatched headline lengths.

**Verification:**
- All renders produce valid, scannable HTML and plaintext. Manual review of one full render pass via dry-run shows the email looks right in Gmail and the dashboard looks right in a browser.

---

- U5. **Anthropic LLM client and prompt**

**Goal:** Generate the storyline narratives and the tonight-game preview as 2-3 sentence prose, strictly grounded in the structured game JSON we pass in.

**Requirements:** R6, R7, R16

**Dependencies:** U1, U2 (uses Game dataclass to build payloads)

**Files:**
- Create: `src/mlbreview/llm.py` — `write_storyline(game)` and `write_preview(tonight_game)`, plus the system prompt as a module constant
- Modify: `tests/test_pipeline.py` (created here, expanded in U6) — covers LLM client with mocked `anthropic.Anthropic`

**Approach:**
- Single `Anthropic()` client at module level, `claude-haiku-4-5` model.
- System prompt: "You write 2-3 sentence baseball storyline blurbs grounded strictly in the JSON facts provided. Never invent player names, stat lines, or plays. If the JSON does not contain a fact, do not state it. Plain prose, no markdown, no headlines."
- `max_tokens=200`, no streaming.
- `write_storyline(game)` builds a JSON payload with: teams + scores, key plays (top 3 by |WPA|), winning + losing pitchers, decisive moment description (highest-leverage play). User message: `"Game facts:\n{json.dumps(payload)}"`.
- `write_preview(tonight_game)` builds a similar payload with probable pitchers, season records, division standings delta, broadcast slot.
- Light retry: on `anthropic.APIError`, retry once after 2s. On second failure, fall back to a deterministic template-based prose ("X beat Y N-M behind a 2-run shot from Z" — generic but factual). Logged loudly.
- Strict-grounding verification: for storylines, post-process to check that any player names mentioned in the prose appear in the input payload's player list. If not, flag and fall back to template.

**Patterns to follow:**
- Establish the pattern: pure-ish function with a single external dependency, mockable in tests.

**Test scenarios:**
- *Happy path (mocked):* `write_storyline(walkoff_game)` returns a 2-3 sentence string. Mocked Anthropic response is a known prose blurb; assert the function returns it cleanly.
- *Edge case:* the LLM fabricates a player name not in the payload → grounding check kicks in → function falls back to template prose. (Test with a mocked response containing "Aaron Judge" when the payload has no Judge.)
- *Error path:* mocked `APIError` on first call, success on retry → returns the second-call prose.
- *Error path:* mocked `APIError` on both calls → returns the deterministic template-based fallback. Logs a warning.
- *Edge case:* payload with no plays (empty game) → returns a short factual default ("No game data available" or similar) without calling the LLM.

**Verification:**
- All tests pass with mocked Anthropic. One manual dry-run against a real game produces sensible 2-3 sentence prose that matches the input facts.

---

- U6. **Pipeline orchestrator + CLI entrypoint behaviors**

**Goal:** Tie everything together. Single `pipeline.run(date, dry_run, out_dir)` function that fetches data → scores → calls LLM → renders dashboard files into `out_dir` → sends email. Handles off-day and season-pause branches. The workflow (U7) handles the actual publish to `gh-pages`.

**Requirements:** R1, R3, R4, R17, P3

**Dependencies:** U1, U2, U3, U4, U5

**Files:**
- Create: `src/mlbreview/pipeline.py` — `run(date, dry_run, out_dir)` and helper sub-functions
- Modify: `src/mlbreview/__main__.py` — wire the CLI flags to `pipeline.run()`
- Create/expand: `tests/test_pipeline.py`

**Approach:**
- DST guard at the top of `run()`: if `datetime.now(ZoneInfo("America/New_York")).hour != 5` AND `not dry_run`, `SystemExit(0)`. (Allows dry-run at any hour.)
- Season-pause guard: if not in regular-season or postseason window (date-based check using last year's World Series end and Opening Day, or the schedule API as authoritative), `SystemExit(0)`. **Covers AE4.**
- Fetch yesterday's data → if `len(finals) == 0`, take the off-day branch: render "no games" email + placeholder dashboard into `out_dir`, send email, exit. **Covers AE3.**
- Otherwise: full path. Fetch game feeds in parallel (asyncio? for simplicity, sequential with the shared httpx client is fine for ~15 games). Score storylines → variety rule → top 3. Fetch tonight's schedule → score hype → top 1. Generate prose for all 4 (3 storylines + 1 preview). Fetch transactions for news brief. Render email + dashboard.
- Production path: write `{out_dir}/digests/YYYY-MM-DD/index.html` and regenerate `{out_dir}/index.html` (the archive index, derived from listing existing date folders inside `out_dir/digests/`). Send email via Resend. The orchestrator does NOT touch git; the workflow's `peaceiris/actions-gh-pages` step (U7) takes `out_dir` and publishes it to the `gh-pages` branch.
- Dry-run path: write rendered files into a local `out_dir` (default `./public/`), print the email HTML + text to stdout, do not send the email.
- Idempotency: if `{out_dir}/digests/YYYY-MM-DD/index.html` already exists when the run starts (after the workflow has checked out the existing `gh-pages` branch into `out_dir`), skip the run with a logged warning. Avoids accidental duplicate sends.

**Patterns to follow:**
- One orchestration function. All business logic lives in submodules; orchestrator is just sequencing.

**Test scenarios:**
- *Happy path (fully mocked):* `run('2025-08-15', dry_run=True)` against fixtures produces a non-empty rendered email containing the expected storyline count (3) and the tonight preview. **Covers F1.**
- *Edge case (off-day):* `run` against a fixture with `len(finals) == 0` renders the off-day email and exits 0. **Covers AE3, F2.**
- *Edge case (season pause):* `run('2026-01-15', dry_run=True)` exits 0 without rendering or sending. **Covers AE4.**
- *Edge case (DST guard):* with the current hour mocked to 4, `run(date, dry_run=False)` exits 0 silently.
- *Edge case (idempotent re-run):* second call to `run` with the same date and an existing dashboard file logs a warning and exits 0 without re-sending.
- *Integration:* in dry-run, the sequence of fetches + scoring + LLM calls + renders happens in the right order with no exceptions raised. (Verified by mocking each module and asserting call counts/order.)
- *Error path:* MLB API failure on game-feed fetch → run continues with the games it did fetch (skip the failed one, log loudly), still produces a digest unless zero games succeeded.

**Verification:**
- A dry-run against a real recent date produces a sensible email and dashboard preview. All mocked tests pass. The orchestrator never silently swallows errors that should fail the run.

---

- U7. **GitHub Actions workflow + `gh-pages` deployment**

**Goal:** Wire `pipeline.run()` to a daily cron job that runs at 5:30am EST, sends the email, and publishes the dashboard archive to the `gh-pages` branch.

**Requirements:** R1, R2, R18, R19

**Dependencies:** U6 (pipeline must be runnable end-to-end)

**Files:**
- Create: `.github/workflows/digest.yml`

**Approach:**
- Trigger: `on.schedule` with both `30 9 * * *` and `30 10 * * *` (UTC, covers EST + EDT). Plus `on.workflow_dispatch` for manual reruns.
- Permissions: `contents: write` only (needed for `peaceiris/actions-gh-pages` to push to the `gh-pages` branch). No `pages: write` or `id-token: write` needed — Pages source is "Deploy from a branch", not "GitHub Actions".
- Single job, sequential steps:
  1. `actions/checkout@v4` — checkout `main` (source code).
  2. `actions/checkout@v4` with `ref: gh-pages, path: public, fetch-depth: 1, continue-on-error: true` — pull the existing `gh-pages` content into `./public/` so the orchestrator can read prior dates and regenerate the index. The `continue-on-error` covers the first-ever run when `gh-pages` doesn't exist yet.
  3. `mkdir -p public` — fallback for the first-run case.
  4. `actions/setup-python@v5` with `python-version: 3.12` and pip cache.
  5. `pip install -e .`
  6. `python -m mlbreview --out-dir public` (production mode). Reads MLB data + Anthropic + Resend secrets from env, writes new digest into `./public/digests/YYYY-MM-DD/`, regenerates `./public/index.html`, sends the email.
  7. `peaceiris/actions-gh-pages@v4` with `github_token: ${{ secrets.GITHUB_TOKEN }}`, `publish_dir: ./public`, `user_name: 'github-actions[bot]'`, `user_email: '...@users.noreply.github.com'`. Publishes `./public/` as the new `gh-pages` HEAD. (No `keep_files` flag needed — step 2 pre-loaded prior days into `./public/`.)
- Secrets used: `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `DIGEST_TO_EMAIL`, `DIGEST_FROM_EMAIL` (defaulting to `MLB Digest <onboarding@resend.dev>`). The default `GITHUB_TOKEN` covers the gh-pages push — no PAT required.
- Failure notification: GitHub's built-in workflow-failure email (configured in user settings) is sufficient for V1. Document this in the workflow comment for clarity.
- Note for repo owner: GitHub Pages source must be set to "Deploy from a branch" → `gh-pages` (root) in repo Settings → Pages (one-time manual step, after the first successful run creates the branch). Workflow comment calls this out.

**Patterns to follow:**
- Standard `actions/checkout@v4` + `actions/setup-python@v5` + `peaceiris/actions-gh-pages@v4` pattern, widely used and documented.

**Test scenarios:**
- *Test expectation: none — workflow YAML is configuration, not behavior.* Verification is via manual `workflow_dispatch` trigger and observing the run.
- (Implicit) The first manual run produces an email + creates the `gh-pages` branch with the day's content; subsequent runs preserve prior days and append new ones.

**Verification:**
- Manually triggered `workflow_dispatch` run completes successfully end-to-end: a new commit on `gh-pages` adds `digests/YYYY-MM-DD/` and updates `index.html`, the email arrives, and the dashboard URL is live.
- `main` shows zero bot commits — the workflow does not modify source.
- A second consecutive trigger correctly hits the idempotency guard (the day's folder already exists in the gh-pages checkout) and exits cleanly without a duplicate email.
- A trigger during the wrong UTC slot (the DST guard hour) exits cleanly without a digest.

---

## System-Wide Impact

- **Interaction graph:** Pipeline calls scoring, LLM, and renderer modules in sequence. Each module is independent; no callbacks or hooks. The Action also performs git operations on `main`.
- **Error propagation:** Fetch errors and LLM errors degrade gracefully (skip failed games; fall back to template prose). Renderer errors and Resend errors fail the run loudly — the digest is the entire product, so half-sends are worse than no-sends. Pipeline catches `MlbApiError` only when it has at least one good game; otherwise raises and fails the run.
- **State lifecycle risks:** The orchestrator writes dashboard files into a local `out_dir` and sends the email; the workflow then publishes `out_dir` to `gh-pages`. If the email send succeeds but the gh-pages push then fails, the user gets the email but the dashboard URL is stale until the next day's run. Conversely, if rendering fails, no email is sent and no publish happens — failures are loud, not silent. Manual remediation is to re-trigger via `workflow_dispatch`; the idempotency guard ensures no duplicate emails on re-runs.
- **API surface parity:** None — single-user pipeline, no public API surface beyond the dashboard URL.
- **Integration coverage:** Cross-layer behaviors (data → scoring → LLM → render → send → deploy) are exercised end-to-end by dry-run during development and by the live cron in production. CI tests cover individual layers and the orchestrator's sequencing with mocks; the full live integration is verified manually.
- **Unchanged invariants:** `CLAUDE.md` and the brainstorm doc remain the source of truth for product intent — the plan does not add product behavior beyond what origin specified.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| MLB Stats API quietly changes a hydrate field name or response shape | Recorded JSON fixtures pin the contract for tests; if the live shape drifts, the dry-run will surface it before production. |
| GitHub Actions cron is delayed or dropped on a given day | `workflow_dispatch` allows manual re-run; idempotency guard prevents duplicate sends; failure notifications surface drops. |
| `gh-pages` push fails after the email has already been sent | User gets email but dashboard URL is stale for the day. Next day's run includes both days' content, restoring the archive. Acceptable for V1. |
| LLM hallucinates a player name or stat despite grounding | Post-process verification flags hallucinated names; falls back to deterministic template prose. Grounded strict-prompt + low temperature is the primary defense. |
| Drama/hype formula initial weights produce bad rankings | All weights in `config.py`; calibration session in week 2 against real digests; `docs/formulas.md` makes the tuning surface obvious. |
| Resend free-tier sender (`onboarding@resend.dev`) blocked by some recipient mail filter | Single user, recipient is the user's own Gmail — verified compatible during dry-run. Domain verification is the V2-or-later upgrade path. |
| `gh-pages` branch grows large over many seasons | At ~10KB per daily HTML × 200 days/season × 5 seasons ≈ 10MB total. The orphan-style branch can be force-rewritten to drop history at any time without affecting `main`. Revisit only if branch crosses 50MB. |

---

## Documentation / Operational Notes

- **`docs/formulas.md` is non-negotiable.** Drama, hype, and variety logic must be readable by a non-implementer. Update it whenever weights or thresholds change. Treat it as production documentation, not a README.
- **`config.py` is the calibration surface.** All weights, thresholds, the variety threshold, and `MAX_STORYLINES` live there with comments. Calibration sessions edit only this file.
- **Manual one-time setup steps** (called out in `CLAUDE.md` and in the workflow comment):
  1. Add `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `DIGEST_TO_EMAIL` as repo Action secrets
  2. Re-auth `gh` with `gh auth refresh -h github.com -s workflow` to allow the first push of `.github/workflows/digest.yml` from the local machine
  3. After the first successful workflow run creates the `gh-pages` branch, set GitHub Pages source to "Deploy from a branch" → `gh-pages` (root) in repo Settings → Pages
- **Operational rhythm:** After V1 ships, watch the first 14 days for ranking quality. Calibrate weights in week 2. Open a V2 plan once daily delivery is solid for ≥2 weeks.
- **Failure modes the user will see:** GitHub workflow failure email arrives instead of the digest. Manual `workflow_dispatch` re-run is the recovery path.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-02-mlbreview-digest-requirements.md](../brainstorms/2026-05-02-mlbreview-digest-requirements.md)
- **Project context:** [CLAUDE.md](../../CLAUDE.md)
- **MLB Stats API endpoints:** https://github.com/toddrob99/MLB-StatsAPI/wiki/Endpoints
- **GitHub Pages publishing options:** https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
- **`peaceiris/actions-gh-pages`** (chosen for V1): https://github.com/peaceiris/actions-gh-pages
- **`actions/deploy-pages`** (alternative considered): https://github.com/actions/deploy-pages
- **Anthropic Claude models:** https://platform.claude.com/docs/en/about-claude/models/overview
- **Resend Python quickstart:** https://resend.com/docs/send-with-python
- **GitHub Actions cron delays discussion:** https://github.com/orgs/community/discussions/156282
