---
date: 2026-05-02
topic: mlbreview-daily-digest
---

# mlbreview — Daily Baseball Digest

## Summary

A daily 5:30am EST digest delivered as a clean push email + a Claude-designed web dashboard. The email is a focused 10-minute breakfast read (scores, three drama-ranked storylines, tonight's most-hyped game preview, brief off-field news) with a prominent "Open in dashboard →" link to a richer visual version with WPA charts, expandable game cards, and a permanent archive at `/digests/YYYY-MM-DD`. V1 ships those four sections; V2 adds hot/cold/breakout player leaderboards.

---

## Problem Frame

The user currently skims the MLB app at breakfast and finds it overwhelming — too many games, too much noise, no curation. They have ~10 minutes while eating, and want a focused recap of what mattered last night plus a clear pointer to the most worthwhile game to watch tonight. Existing newsletters either lean too heavily on team-bias, off-field rumor coverage, and hot takes (The Athletic), or read like raw box scores (MLB.com recap email). Nothing on the market combines drama-weighted curation, fantasy-relevant player signals, and a beautifully designed visual surface for a single, league-neutral, fantasy-aware reader.

---

## Key Flows

- F1. **Nightly digest generation and delivery**
  - **Trigger:** GitHub Actions cron at 5:30am EST (10:30 UTC during EDT, 09:30 UTC during EST — schedule both)
  - **Steps:**
    1. Pull the prior day's completed games, play-by-play, and standings from MLB Stats API
    2. Pull tonight's schedule, probable pitchers, broadcast info
    3. Pull last 24h of transactions (trades, IL, debuts) for the news brief
    4. Score game candidates using the Win Probability / drama formula; pick top 3 storylines
    5. Score tonight's games with the composite hype formula; pick top 1
    6. Send structured game data to Claude API; receive 2-3 sentence prose narratives per storyline (and the preview)
    7. Render dashboard HTML (Jinja2 → static page) and publish to GitHub Pages at `/digests/YYYY-MM-DD/`
    8. Render email HTML (skim-optimized preview + "Open in dashboard →" link); send via Resend
  - **Outcome:** User receives email by 5:30am EST; dashboard URL is live and archived
  - **Covered by:** R1, R2, R3, R5, R6, R7, R8, R9, R10, R12, R14, R16

- F2. **Off-day / no-games handling**
  - **Trigger:** Same daily cron, but the prior day had zero completed MLB games (e.g., All-Star break, postseason off-day)
  - **Steps:**
    1. Detect zero-game state from the schedule API
    2. Send a short "no games last night" email with tonight's schedule (if any) and a placeholder dashboard page
  - **Outcome:** Daily ritual is preserved; user is not left wondering whether the job failed
  - **Covered by:** R17

---

## Requirements

**Delivery & cadence**
- R1. The system sends a daily digest email at 5:30am EST, every day during the regular season and postseason.
- R2. Each digest also generates a public web dashboard page at `https://ryanlambies.github.io/mlbreview/digests/YYYY-MM-DD/` containing the full digest in a richer visual form.
- R3. The email body contains an above-the-fold readable preview of all V1 sections plus a prominent "Open in dashboard →" link near the top.
- R4. The system pauses delivery during spring training and the offseason (no email or dashboard updates between the day after the World Series and Opening Day).

**V1 content sections (in email order)**
- R5. **Scores recap.** Final scores for every completed MLB game from the prior day, displayed in a scannable line-score format.
- R6. **Top 3 storylines.** Three on-field narratives ranked by Win Probability / drama, written as 2-3 sentence prose narratives by an LLM grounded in structured game data. Storylines may come from any of: dramatic game outcomes/moments, individual performance feats (cycle, no-hitter, multi-HR), or standings/playoff race events.
- R7. **Tonight's most-hyped game.** A short preview (2-3 sentences) of the single most-hyped upcoming game, ranked by a composite hype score combining pitching matchup quality, star players in the lineup, stakes (division rivals, contender vs. contender, playoff implications), and national broadcast slot.
- R8. **Off-field news brief.** A small bottom-of-email section listing notable transactions from the prior 24 hours: trades, injury updates (IL moves), and debuts/call-ups. Factual bullet style — no LLM prose, no rumors, no commentary.

**Storyline ranking**
- R9. Storyline candidates are scored by a Win Probability / drama formula incorporating WPA peak swings, leverage index of late-game plays, and margin-of-victory inverse weighting. The top 3 by score are selected.
- R10. The top-3 list is league-neutral (no team-bias weighting). When ties or near-ties exist, prefer variety in storyline category (don't pick three multi-HR games).

**Hype score**
- R11. Tonight's most-hyped game is selected by a composite score combining (a) starting-pitcher quality (combined ERA/FIP), (b) presence of star players (configurable star list), (c) stakes (division rivals, both teams above .500, playoff race delta), (d) national broadcast slot (ESPN, Apple TV+ Friday, MLB Network national, Fox).

**V2 content sections (planned, deferred)**
- R12. **Hot players (V2).** Leaderboard of the top streaking hitters and pitchers from a rolling 7-day window, ranked by traditional headline stats (AVG/HR/RBI for hitters; ERA/K/WHIP for pitchers) and confirmed by advanced/Statcast stats (xwOBA, barrel %, FIP) to filter out luck-driven noise.
- R13. **Cold players (V2).** Leaderboard of the top slumping hitters and pitchers from a rolling 7-day window, with the same advanced-stat luck filter applied in reverse.
- R14. **Breakouts (V2).** Players whose 15-day rolling performance also confirms the 7-day streak, surfaced as "real" breakouts vs short-term hot streaks.
- R15. V2 leaderboards take advantage of the dashboard surface (sortable, expandable rows) more than they do the email.

**Voice & data**
- R16. Storyline and game-preview prose is generated by Claude API, with structured game data passed as context. Prompts instruct the model to stay grounded in the provided facts and avoid fabricating plays, names, or stats.
- R17. When zero MLB games were completed the prior day (off-day / All-Star break), the digest still sends — a short "no games last night" message with tonight's schedule. The daily ritual is preserved.

**Dashboard**
- R18. Each daily dashboard is a static HTML page generated by the same Python pipeline using Jinja2 templates and committed to the GitHub Pages branch by the same Action.
- R19. The dashboard is publicly accessible (no auth, no unguessable URL — content is non-sensitive baseball data).
- R20. A simple index page lists the most recent N digests with date and headline (storyline #1's title) for browsing the archive.

---

## Acceptance Examples

- AE1. **Covers R6, R10.** Given last night had a walk-off HR ending a 3-2 thriller, a no-hitter through 8 innings broken up in the 9th, and three multi-HR performances, when storylines are ranked, the digest selects the walk-off, the near no-hitter, and the most dramatic multi-HR game (not all three multi-HR games) — variety is preferred when scores are close.
- AE2. **Covers R8.** Given a player was traded yesterday, another was placed on the 15-day IL, and a top prospect made their MLB debut, when the email is sent, the off-field news section contains three short factual bullets (one per item) at the bottom, with no commentary or LLM prose.
- AE3. **Covers R17.** Given the prior day was the All-Star Game's off-day with zero regular-season games, when the cron fires at 5:30am EST, the system still sends an email — a short "no games last night" note plus tonight's schedule (or "back tomorrow" if also empty) — and publishes a placeholder dashboard page for that date.
- AE4. **Covers R1, R4.** Given today is January 15th (offseason), when the cron would fire, the system does NOT send an email or generate a dashboard page; the next send is on Opening Day.
- AE5. **Covers R3.** Given a user opens the daily email on mobile, when they scroll, the first viewport shows scores + a clear "Open in dashboard →" button before the storyline prose begins. The dashboard link is not buried at the bottom.

---

## Success Criteria

- **Human outcome:** The user can read the email in ≤10 minutes during breakfast and feel confidently caught up on what mattered last night and what to watch tonight, replacing their MLB-app skim.
- **Habit signal:** The email becomes part of the user's daily routine within 2-3 weeks of V1 going live (the user keeps opening it, doesn't unsubscribe / pause).
- **Dashboard pull-through:** The user actively clicks through to the dashboard at least 2-3x/week — the visual surface is being used, not just decorative.
- **Reliability:** The job succeeds (email sent, dashboard published) on ≥99% of scheduled days. Failures are visible (GitHub Actions notification).
- **Handoff quality:** A future Claude session reading `CLAUDE.md` can pick up the project context (lens, ranking definitions, V1/V2 split, infra choices) without needing to re-derive them from code.

---

## Scope Boundaries

- **Off-field analysis, rumors, hot takes** — the news brief stays factual (who/what/when). No "what this trade means," no rumor coverage.
- **Team-bias / favorite-team mode** — explicitly league-neutral.
- **Multi-user, subscriptions, login, web app dynamics** — single-user personal product. The dashboard is static HTML.
- **SMS delivery** — deliberately deferred. Email is the primary push channel; SMS can be added later if email-fatigue becomes a real signal.
- **Spring training coverage, in-game / live updates, push notifications** — out of scope.
- **Custom domain** — `github.io` URL is fine; can move to a domain later without changing the build.
- **Search, filters, JS interactivity** beyond simple expand/collapse on the dashboard.
- **V2 work** is **deferred, not omitted** — hot/cold/breakout leaderboards are a planned next phase and must be designed-for in V1's data model and dashboard layout (i.e., V1 doesn't paint into a corner).

---

## Key Decisions

- **Hybrid delivery (email + dashboard):** Newsletters from Stratechery to Morning Brew use this exact pattern — push email is the habit-former, the dashboard is where visual design (WPA charts, color, layout) actually shines. We don't have to choose; the marginal cost over email-only is small (one extra build step, free hosting on GitHub Pages).
- **GitHub Actions for the run env:** Free, native to the repo, zero infra to maintain. Cron can be a few minutes off — acceptable for a 5:30am breakfast email. Schedule both `30 09 * * *` and `30 10 * * *` to cover EST/EDT, then guard with a timezone check inside the script to no-op on the wrong one.
- **Resend for email:** Modern dev-friendly transactional email; free tier covers one daily email comfortably; cleaner HTML rendering than SMTP.
- **Claude API for storyline prose:** User is Anthropic-aligned and the grounding pattern (structured data + clear "stay grounded" prompt) is straightforward. Cost is trivial (~$0.01-0.05/day).
- **MLB Stats API for V1 data, Baseball Savant/Statcast for V2:** Free, official, comprehensive for V1 needs (scores, play-by-play, WPA, schedule, probable pitchers, broadcast info, transactions). V2 layers Statcast for the advanced-stat luck filter via `pybaseball`.
- **Static dashboard generated by the same pipeline:** Same Python script renders Jinja2 templates → HTML, committed to the `gh-pages` branch (or `docs/` directory on `main`) by the Action. No separate web framework, no server.
- **League-neutral, fantasy-aware lens:** Defines storyline ranking (drama > rare-stat coverage), hype score (no team-bias), and V2 player leaderboards (fantasy-relevant stats with advanced confirmation).
- **Drama-based storyline ranking:** Win Probability / leverage / margin captures "great games to watch the highlights of" better than rare-stat counting (a no-hitter in an 8-0 blowout shouldn't beat a wild walk-off).
- **Hybrid stat lens for V2:** Lead with traditional stats in the email/leaderboard (familiar, scannable); use advanced/Statcast as a luck filter so we don't surface BABIP- or ERA-mirage players.
- **Off-field news included as a small bottom section, not a storyline category:** User added this after the synthesis; categorically separate from on-field storylines, factual-only, no LLM prose.
- **Public dashboard, no auth:** MLB recap content has no privacy concerns; auth would add complexity without value.

---

## Dependencies / Assumptions

- **Language:** Python (`mlb-statsapi`, `pybaseball`, `jinja2`, `resend`, `anthropic` SDKs).
- **Secrets:** `RESEND_API_KEY` and `ANTHROPIC_API_KEY` stored as GitHub Action secrets; minimum-privilege scope on each; spend caps set in the upstream consoles; quarterly rotation cadence.
- **Hosting:** GitHub Pages (free) at `ryanlambies.github.io/mlbreview/`.
- **Email format:** HTML primary, plaintext fallback for clients that don't render HTML.
- **MLB Stats API stability:** The free `statsapi.mlb.com` endpoints are unofficial-but-stable; this project assumes continued availability. If MLB ever rate-limits or breaks the API, a paid alternative (SportsData.io) would be a fallback.
- **DST:** GitHub Actions cron runs in UTC. We schedule both EST and EDT slots and guard inside the script.
- **Single recipient:** The product is for one user; nothing is multi-tenant.

---

## Outstanding Questions

### Resolve Before Planning

- *(none — all product decisions resolved)*

### Deferred to Planning

- [Affects R6, R16][Technical] What's the exact prompt template for storyline prose generation, and how do we structure the grounding payload (game JSON shape) to maximize accuracy and minimize tokens?
- [Affects R9][Technical] What are the exact weights in the WPA / drama formula, and how do we calibrate them after a week of real digests? (Likely needs iteration once we see actual rankings.)
- [Affects R11][Technical] Same calibration question for the composite hype score weights.
- [Affects R18, R20][Technical] Dashboard visual design — color system, typography, chart library (or hand-drawn SVG?), responsive breakpoints. Best handled iteratively in implementation with screenshots and the design-iterator skill.
- [Affects R12, R13, R14][Technical] V2: exact thresholds for "hot" / "cold" — minimum PAs / IPs to qualify, percentile cutoffs, how many players to surface per leaderboard.
- [Affects R2][Needs research] Is GitHub Pages with a `docs/` directory on `main` simpler than a `gh-pages` branch given Actions deploys are now first-class via `actions/deploy-pages`? Plan stage should pick the cleaner workflow.
