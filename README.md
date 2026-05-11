# mlbreview

A personal daily MLB digest that replaces the morning app-skim. Every day during the season, it delivers a focused breakfast email by 5:30am ET and publishes a richer web dashboard to GitHub Pages — curated, league-neutral, and fantasy-aware.

**Live dashboard:** [ryanlambies.github.io/mlbreview](https://ryanlambies.github.io/mlbreview)

## What it does (V1)

The pipeline runs on a GitHub Actions cron, pulls data from the MLB Stats API, scores and ranks games, generates prose with Claude, and sends everything out before breakfast.

Each digest has four sections:

- **Scores recap** — line scores for every completed game from the prior day
- **Top 3 storylines** — 2-3 sentence narratives written by Claude, ranked by a drama formula that weighs Win Probability swings, late-inning leverage, and margin of victory. A variety rule keeps the picks diverse (no three blowouts, no three walk-offs)
- **Tonight's most-hyped game** — a single short preview of the best game to watch tonight, ranked by pitching matchup quality, star density, divisional stakes, and national broadcast slot
- **Off-field news brief** — factual bullets at the bottom: trades, IL moves, call-ups. No commentary, no rumors

On off-days (All-Star break, postseason gaps), the digest still sends — a short "no games last night" message with tonight's schedule so the daily ritual stays intact.

### How it works

```
MLB Stats API  ──>  Fetch & parse  ──>  Score & rank  ──>  Claude prose  ──>  Render  ──>  Email + Dashboard
   (games,          (dataclasses)      (drama + hype      (grounded in       (Jinja2)     (Resend + GitHub
    plays,                              formulas)          structured                       Pages)
    standings)                                             game data)
```

The LLM is strictly grounded — structured game data is passed as context, and a post-generation check catches any hallucinated player names before they reach the reader. If the check fails, a deterministic template fallback is used instead.

### Tech stack

| Layer | Tool |
|-------|------|
| Language | Python 3.12 |
| Data | MLB Stats API (`statsapi.mlb.com`) |
| LLM | Claude API (Anthropic SDK) — claude-haiku-4-5 |
| Email | Resend (transactional, free tier) |
| Templates | Jinja2 (email HTML/text + dashboard pages) |
| Scheduling | GitHub Actions cron (three daily slots for delay tolerance) |
| Dashboard | GitHub Pages — static HTML, no JS framework |

## What's next (V2)

V2 adds player-level leaderboards to the dashboard:

- **Hot players** — 7-day streaking hitters and pitchers, ranked by traditional stats (AVG/HR/RBI, ERA/K/WHIP) with advanced Statcast metrics (xwOBA, barrel rate, FIP) as a luck filter
- **Cold players** — the same lens in reverse, surfacing slumps confirmed by underlying numbers
- **Breakouts** — players whose 15-day rolling performance backs up a 7-day hot streak, separating real breakouts from noise

These sections lean heavier on the dashboard (sortable, expandable rows) than the email. V1's data model and layout are designed to accommodate them without rework.

V2 pulls from Baseball Savant / Statcast via `pybaseball` for the advanced metrics that V1 doesn't need.

## Setup

```bash
# Clone and install
git clone https://github.com/ryanlambies/mlbreview.git
cd mlbreview
pip install -e ".[dev]"

# Configure secrets (see .env.example)
cp .env.example .env
# Set ANTHROPIC_API_KEY, RESEND_API_KEY, DIGEST_TO_EMAIL

# Dry run (no LLM calls, no email — just fetches data and prints output)
python -m mlbreview --dry-run

# Full run
python -m mlbreview
```

## Project structure

```
src/mlbreview/
  __main__.py          # CLI entry point
  config.py            # All tunable knobs (formula weights, thresholds)
  pipeline.py          # Orchestrator — ties fetch, score, LLM, render together
  data/                # MLB API client, schedule, game feeds, transactions
  scoring/             # Drama formula, hype formula, variety rule
  llm.py               # Claude integration, grounding check, fallbacks
  render/              # Jinja2 rendering for email and dashboard
templates/             # Jinja2 templates (email HTML/text, dashboard day/index)
config/                # stars.json (configurable star player list for hype scoring)
tests/                 # pytest suite
.github/workflows/     # Daily cron + GitHub Pages deploy
```

## License

MIT
