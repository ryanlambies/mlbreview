# mlbreview

A personal MLB digest that runs every morning. Sends a focused, ~10-minute breakfast email at 5:30am EST and publishes a richer Claude-designed web dashboard to GitHub Pages. The product replaces the user's MLB-app skim — curated, league-neutral, fantasy-aware.

## Source of truth

The full product definition lives in `docs/brainstorms/2026-05-02-mlbreview-digest-requirements.md`. Read that for requirements (R1–R20), acceptance examples, scope boundaries, and key decisions. This file is the orientation; the brainstorm doc is the spec.

## Lens

- **League-neutral.** No favorite-team bias. Top stories are picked across MLB.
- **Fantasy-aware.** Player performance threads (hot/cold/breakouts in V2) lead with traditional headline stats and use advanced/Statcast as a luck filter.
- **On-field first.** Storylines are dramatic game moments, individual feats, or standings/playoff race events. Off-field news (trades, IL, debuts) gets a small factual bottom section, not a storyline slot.
- **Focused, not comprehensive.** The product's identity is signal-to-noise. Top 3 storylines, one hyped game preview. If a section can be trimmed without losing the morning's gist, trim it.

## V1 (current scope)

Four sections, in email order:

1. **Scores recap** — line scores for all completed games from prior day
2. **Top 3 storylines** — LLM-written 2-3 sentence narratives, ranked by Win Probability / drama
3. **Tonight's most-hyped game** — single short preview, ranked by composite hype score (pitching matchup + stars + stakes + national broadcast)
4. **Off-field news brief** — factual bullets only (trades, IL, debuts) at bottom

Email contains an above-the-fold preview + prominent "Open in dashboard →" link to the full visual version.

## V2 (planned next phase — design V1 to not paint into a corner)

- **Hot players** — 7-day streaking leaderboard with traditional stats, advanced-stat luck filter
- **Cold players** — same in reverse
- **Breakouts** — 7-day hot streaks confirmed by 15-day rolling performance

V2 leverages the dashboard surface more than email (sortable, expandable rows).

## Key definitions

- **Storyline drama score:** WPA peak swings + leverage index of late-game plays + inverse margin-of-victory. Captures "great games to watch the highlights of," not rare-stat counting.
- **Storyline variety rule:** When tied/close in score, prefer category variety (don't pick three multi-HR games).
- **Hype score (composite):** combined-pitcher quality (ERA/FIP) + star players in lineup + stakes (rivals, contender vs. contender, playoff race delta) + national broadcast slot (ESPN, Apple TV+ Friday, MLB Network national, Fox).
- **Hot/cold window (V2):** 7-day rolling for the hot/cold list; 15-day rolling required to label a player a "breakout."
- **Off-day handling:** When zero MLB games completed prior day, send a short "no games last night" email + tonight's schedule. Daily ritual is preserved.
- **Active season:** Regular season + postseason. Pause spring training and offseason.

## Tech stack

- **Language:** Python
- **Data:** MLB Stats API (`statsapi.mlb.com`) for V1; Baseball Savant / Statcast (via `pybaseball`) added for V2
- **LLM:** Claude API (Anthropic SDK) for storyline + preview prose, grounded with structured game data
- **Email:** Resend (transactional, free tier covers one daily send)
- **Templating:** Jinja2 for both email HTML and dashboard pages
- **Run env:** GitHub Actions cron (schedule both `30 09 * * *` and `30 10 * * *` UTC to cover EST/EDT, guard with timezone check)
- **Dashboard hosting:** GitHub Pages, served from the `gh-pages` branch (published by `peaceiris/actions-gh-pages@v4`). Per-day archive at `https://ryanlambies.github.io/mlbreview/digests/YYYY-MM-DD/`. `main` contains source code only — no rendered HTML.
- **Secrets:** `RESEND_API_KEY` + `ANTHROPIC_API_KEY` in GitHub Actions secrets (min-privilege scope, upstream spend caps)

## Working agreements

- **Don't add team-bias features.** League-neutral is the product identity, not a default to be changed later.
- **Keep the news brief factual.** No commentary, rumor coverage, or LLM prose in the off-field section.
- **Ground the LLM strictly.** Pass structured game data; instruct the model not to fabricate plays, names, or stats. Verify outputs against source data.
- **The dashboard is static.** No JS framework, no server, no auth. Static HTML + minimal CSS / SVG.
- **V1 must not paint V2 into a corner.** Data model and dashboard layout should accommodate V2 leaderboards from day one.
- **Email is the habit, dashboard is the depth.** Don't bury the dashboard link, don't make the email require the dashboard.

## Workflow conventions

This project uses a **PR-per-unit** flow. Branch protection is not enabled (direct pushes to `main` are still possible as an escape hatch), but the convention below is the default for substantive work, especially anything implementing a plan unit.

- **One branch per logical change.** For implementation-plan work, that means one branch per U-ID — e.g., `u1-project-skeleton`, `u2-data-fetch-layer`. For other changes, use `chore/<topic>`, `fix/<topic>`, or `docs/<topic>`.
- **Open a PR with `gh pr create`.** Title format: short imperative (e.g., `feat: add MLB data fetch layer (U2)`). Body should reference the plan unit by U-ID and call out anything the reviewer should look at first.
- **Pause for review before starting the next unit.** When implementing a plan, finish the unit, push the branch, open the PR, and stop. Wait for the user to merge (or leave comments) before opening the next branch. This is the whole point of the PR flow — don't pipeline multiple units past the review gate.
- **Self-merge is fine.** This is a solo project; the PR is for the diff-review surface, not multi-person approval.
- **Direct pushes to `main` are reserved for:** trivial doc edits (typo fixes), tagging releases, or unblocking a stuck PR. If you ever find yourself doing direct pushes for real code changes, that's a signal to add branch protection as a forcing function.
- **`gh` CLI must have the `workflow` scope** to push branches that touch `.github/workflows/`. If you hit "refusing to allow an OAuth App to create or update workflow", run `gh auth refresh -h github.com -s workflow` and retry.

## Repo layout

```
mlbreview/                              # main branch (source code, docs)
├── CLAUDE.md                           # this file — orientation
├── README.md
├── docs/
│   ├── brainstorms/
│   │   └── 2026-05-02-mlbreview-digest-requirements.md   # full product spec
│   ├── plans/
│   │   └── 2026-05-02-001-feat-v1-daily-digest-plan.md   # V1 implementation plan
│   └── formulas.md                     # plain-language drama / hype / variety explainer (built in U3)
├── src/
│   └── mlbreview/                      # Python package (built in V1)
├── templates/                          # Jinja2 (email + dashboard) — built in U4
├── config/                             # stars.json — built in U3
├── tests/                              # built alongside each unit
├── .github/workflows/                  # cron + gh-pages publish — built in U7
├── pyproject.toml                      # built in U1 (replaces requirements.txt)
└── .env.example
```

The rendered dashboard (per-day HTML + index) lives on the **`gh-pages` branch** — it's generated at run time and published by the workflow, never committed to `main`.

## Next steps for future Claude sessions

Both the product spec (`docs/brainstorms/`) and the V1 implementation plan (`docs/plans/2026-05-02-001-feat-v1-daily-digest-plan.md`) are settled. The next session should run `/ce-work` against the plan and walk through implementation units U1 → U7 in order, opening a PR per unit per the workflow convention above. Each unit's required files, test scenarios, and verification steps are spelled out in the plan.
