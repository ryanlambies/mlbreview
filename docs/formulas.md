# Scoring formulas

This document explains how mlbreview ranks games and picks the daily digest content. It is meant for tuning sessions — you should be able to read this, adjust a weight in `src/mlbreview/config.py`, and predict how rankings will change.

All weights and thresholds referenced below live in `config.py` as module-level constants. The scoring code is in `src/mlbreview/scoring/`.

---

## Drama score (storyline ranking)

The drama score answers: **which of yesterday's games are most worth watching the highlights of?**

It rewards late-game tension and close finishes, not rare statistical events. A walk-off win in a 3-2 game beats an 8-0 no-hitter.

### Formula

```
drama = 0.5 × norm_max_wpa + 0.3 × norm_late_wpa + 0.2 × inverse_margin
```

| Component | What it captures | Calculation | Range |
|-----------|-----------------|-------------|-------|
| `norm_max_wpa` | Single biggest momentum swing in the game | `\|max WPA\| / 50.0` | [0, 1] |
| `norm_late_wpa` | Biggest momentum swing in innings 7+ | `\|late-inning max WPA\| / 50.0` | [0, 1] |
| `inverse_margin` | Penalizes blowouts | `1 / (1 + final margin)` | (0, 1] |

WPA (Win Probability Added) is in percentage points on a 0–100 scale. A play that flips win probability from 50% to 85% has a WPA of +35. The practical single-play ceiling is ~50 points (a play that takes a team from 50% to 100%).

### Why these weights

- **Max WPA (0.5):** The single biggest swing is the best proxy for "did something jaw-dropping happen?" A grand slam in a tie game, a bases-loaded strikeout — these register as 30-50 point swings.
- **Late-inning WPA (0.3):** Late-game drama matters more to viewers than early-game drama. A 40-point swing in the 9th inning is more memorable than the same swing in the 2nd.
- **Inverse margin (0.2):** A game that ends 3-2 gets an inverse_margin of 0.33; a game that ends 12-1 gets 0.077. This ensures blowouts can't rank high on WPA swings alone (a grand slam in a 12-1 game is exciting but the game itself wasn't competitive).

### The leverage substitution

The original design called for Leverage Index (LI) in the second term — a standard sabermetric stat that measures how much a play's outcome affects win probability, scaled relative to league average. LI is exactly what we want: it tells you "how tense was this moment?"

However, Leverage Index is **not available on the MLB Stats API's public endpoints** (`/feed/live`, `/winProbability`). It lives on Baseball Savant / Statcast, accessible via `pybaseball`, which is a V2 dependency.

We substitute **late-inning peak |WPA|** — the largest single-play |WPA| in innings 7 or later. This preserves the formula's intent ("credit late-game high-stakes plays") with data we have:

- High LI → plays where win probability can swing dramatically → high |WPA| (they correlate strongly in late innings where both conditions are common).
- The late-inning filter (configurable via `DRAMA_LATE_INNING_THRESHOLD`, default 7) focuses on the part of the game where tension naturally escalates.
- The main gap: in rare cases a play can have high LI but low WPA (e.g., a strikeout that preserves a tie — the moment was tense but the outcome was neutral). Our proxy misses these. In practice, for ranking daily storylines, the difference is negligible — the top drama games almost always have high-WPA late-inning plays.

When V2 adds Statcast via `pybaseball`, we can swap in real LI. The weight and normalization slot are already in place.

### Category tags

Each game gets a category tag used by the variety rule:

| Category | Detection |
|----------|-----------|
| `walkoff` | Home team wins, biggest play in the final inning's bottom half |
| `extra_innings` | Game went past 9 innings |
| `comeback` | Winning team's win probability dropped below 20% at some point |
| `pitchers_duel` | Combined runs ≤ 4 and margin ≤ 1 |
| `feat` | One team scored 10+ runs, or a shutout with 7+ runs |
| `default` | None of the above |

Priority order: walkoff > extra_innings > comeback > pitchers_duel > feat > default. A game gets exactly one tag.

### Tuning guide

| Want to... | Change in `config.py` |
|------------|----------------------|
| Weight late-game drama more | Increase `DRAMA_W_LATE_WPA`, decrease others proportionally |
| Punish blowouts harder | Increase `DRAMA_W_MARGIN` |
| Lower the "what counts as late" threshold | Decrease `DRAMA_LATE_INNING_THRESHOLD` (e.g., 6 = 6th inning onward) |
| Raise the WPA normalization ceiling | Increase `DRAMA_MAX_WPA_CEILING` (makes all WPA-based scores smaller) |

---

## Hype score (tonight's preview)

The hype score answers: **which of tonight's games should a league-neutral fan pay attention to?**

### Formula

```
hype = 0.35 × pitching_quality + 0.25 × stakes + 0.20 × star_density + 0.20 × national_broadcast
```

| Component | What it captures | Calculation | Range |
|-----------|-----------------|-------------|-------|
| `pitching_quality` | Are the starting pitchers good? | avg(1/ERA) across both starters, normalized | [0, 1] |
| `stakes` | Does this game matter in the standings? | Sum of discrete flags (see below) | [0, 1] |
| `star_density` | Are recognizable stars playing? | count(stars) / 4, clamped | [0, 1] |
| `national_broadcast` | Is this on national TV? | 1.0 if any broadcast is national, else 0.0 | {0, 1} |

### Pitching quality

Uses inverse-ERA: a pitcher with a 2.00 ERA contributes 0.50 to the average; a pitcher with a 4.50 ERA contributes 0.22. The average of both starters' inverse-ERA is normalized against a ceiling of 1/1.50 (≈ 0.67), representing an elite matchup.

When a probable pitcher is unknown (TBD), we use league-average ERA (4.50) as a fallback. This avoids crashing and produces a neutral contribution — the game neither gains nor loses hype from an unknown arm.

### Stakes

Additive discrete flags, capped at 1.0:

| Flag | Value | Meaning |
|------|-------|---------|
| Division rivals | +0.4 | Same division — heightened rivalry |
| Both above .500 | +0.3 | Both teams have winning records — competitive matchup |
| Playoff-race delta ≤ 3 | +0.3 | Teams are within 3 games of each other in standings |

A division-rivalry game between two contenders in a tight race maxes the stakes signal at 1.0.

### Star density

Counts how many players from `config/stars.json` appear across both rosters, divided by 4 and clamped to [0, 1]. The divisor means 4+ stars saturates the signal — we don't want a team stacking stars to dominate.

The star list is manually curated (~30 players) and refreshed pre-season. It is intentionally league-neutral — no team-bias in who counts as a star.

### National broadcast

Binary: 1.0 if the game airs on ESPN, Fox, Apple TV+ Friday Night Baseball, or MLB Network national. The MLB Stats API marks these with `isNational: true` in the broadcast list.

### Tuning guide

| Want to... | Change in `config.py` |
|------------|----------------------|
| Weight pitching matchups more | Increase `HYPE_W_PITCHING` |
| Make standings matter more | Increase `HYPE_W_STAKES` |
| Give more credit to rivalry games | Increase `HYPE_STAKES_DIVISION_RIVALS` |
| Change how many stars saturate the signal | Adjust `HYPE_STAR_DENSITY_DIVISOR` |

---

## Variety rule (storyline diversity)

The variety rule answers: **are we showing three different kinds of stories, or three copies of the same thing?**

### Algorithm

1. Take the top 5 candidates by drama score.
2. Walk them in order. For each candidate:
   - If its category was already selected **and** its score is within 10% of the previous pick with that same category → **skip**, try the next candidate.
   - Otherwise → **accept**.
3. Stop at 3 accepted storylines.
4. If fewer than 3 accepted (because too many were skipped), backfill from the skipped list in score order.

### Example

Given 5 candidates:

| Rank | Game | Score | Category |
|------|------|-------|----------|
| 1 | Walk-off game | 0.92 | walkoff |
| 2 | Multi-HR game A | 0.88 | feat |
| 3 | Multi-HR game B | 0.85 | feat |
| 4 | Comeback game | 0.84 | comeback |
| 5 | Standings swing | 0.80 | default |

- **#1 (walkoff, 0.92):** Accepted. First pick, no prior walkoff.
- **#2 (feat, 0.88):** Accepted. First feat.
- **#3 (feat, 0.85):** Category "feat" already selected at 0.88. Score difference: 0.03, which is within 10% of 0.88 (0.088). **Skipped.**
- **#4 (comeback, 0.84):** Accepted. First comeback.

Result: **[walkoff, feat A, comeback]** — three different types of stories.

### Edge cases

- **All same category:** If all 5 candidates are the same category, the backfill ensures we still return 3 games. The variety rule demotes but never drops below the target count.
- **Threshold = 0:** Disables the variety rule entirely. Always returns the top 3 by raw drama score.
- **Wide score gap:** If two same-category games have scores 0.92 and 0.60, the gap (0.32) exceeds 10% of 0.92 (0.092), so both are accepted. The rule only triggers when same-category games are bunched together in score.

### Tuning guide

| Want to... | Change in `config.py` |
|------------|----------------------|
| Force more category variety | Increase `VARIETY_THRESHOLD` (e.g., 0.20 = 20%) |
| Allow same-category duplicates more freely | Decrease `VARIETY_THRESHOLD` |
| Change how many storylines the digest surfaces | Adjust `MAX_STORYLINES` |

---

## Rolling stats (V2 leaderboard foundation)

Rolling stats power the V2 hot/cold/breakout leaderboards. Each day's pipeline writes a snapshot of every player's game stats, and the leaderboard code loads the most recent N snapshots to compute aggregates.

### Data flow

1. **Boxscore fetch** (`data/gamelogs.py`): For each completed game, fetch `/game/{gamePk}/boxscore` and extract per-player stats.
2. **Snapshot write** (`data/snapshots.py`): Combine all players' stats into a `DailySnapshot` and persist as JSON.
3. **Snapshot load** (`data/snapshots.py`): Load the most recent 7 (or 15) day-named JSON files.
4. **Rolling aggregation** (`scoring/leaderboards.py`): Sum counting stats across snapshots, compute rate stats, and apply qualification filters.

### Player roles

Stats are tracked separately for three roles:

| Role | Tracked stats | Qualification threshold |
|------|--------------|------------------------|
| **Hitters** | PA, AB, H, 2B, 3B, HR, RBI, SB, BB, SO | `MIN_PA_HITTER = 15` PA over the window |
| **Starters** | Starts, outs recorded, H, ER, BB, K, HR, pitches | `MIN_IP_PITCHER = 7.0` IP over the window |
| **Closers** | Appearances, outs, ER, SV, BS, HLD, K, BB | `MIN_SV_OPP_CLOSER = 2` save opportunities |

### Computed rate stats

From the summed counting stats, these rate stats are derived:

**Hitters:**
- AVG = H / AB
- OBP = (H + BB) / PA
- SLG = TB / AB (where TB = 1B + 2×2B + 3×3B + 4×HR)

**Starters:**
- ERA = (ER × 9) / IP
- WHIP = (BB + H) / IP
- K/9 = (K × 9) / IP

**Closers:**
- ERA = (ER × 9) / IP
- SV% = SV / (SV + BS)

Note: closer WHIP is not computed because `CloserDayStats` does not track hits allowed (only walks). Closers are evaluated by ERA and SV% for the V2 leaderboards.

### Window sizes

- **Hot/cold:** 7-day rolling window (`ROLLING_WINDOW_DAYS = 7`)
- **Breakout:** 15-day rolling window (`BREAKOUT_WINDOW_DAYS = 15`)

Both windows count calendar days of available snapshots. Off-days (no games) produce no snapshot, so a 7-day window during a week with one off-day contains 6 days of data.

### Starter classification

A pitcher is classified as a starter if their `gamesStarted` field equals 1 in the boxscore pitching stats. All other pitchers with save, blown save, or hold activity are classified as closers. Middle relievers without any of these are excluded from V2 leaderboards.

### Innings pitched encoding

The MLB API uses a special notation for innings pitched: `"6.1"` means 6 and 1/3 innings (19 outs), not 6.1 innings. The fractional digit is always 0, 1, or 2 (representing additional outs beyond full innings). We convert this to total outs for accurate arithmetic and derive IP as `outs / 3`.

### Tuning guide

| Want to... | Change in `config.py` |
|------------|----------------------|
| Require more playing time to qualify | Increase `MIN_PA_HITTER` / `MIN_IP_PITCHER` |
| Show more players per leaderboard | Increase `LEADERBOARD_SIZE` |
| Use a longer hot/cold window | Increase `ROLLING_WINDOW_DAYS` |
| Require a longer breakout confirmation | Increase `BREAKOUT_WINDOW_DAYS` |

---

## Leaderboard scoring (composite ranking)

Each qualified player receives a composite score on a [0, 1] scale that determines their position on the hot/cold leaderboards. **Hot** = top N by composite (descending). **Cold** = the worst N by composite (ascending) — but with two guards described under [Cold-list selection](#cold-list-selection) below, so the cold list is never just "the relative bottom of a thin pool."

### Hitter composite

```
hitter_composite = 0.40 × norm_avg + 0.30 × norm_hr + 0.30 × norm_rbi
```

| Component | Normalization | Ceiling |
|-----------|--------------|---------|
| `norm_avg` | AVG / 0.500 | A .500 7-day average saturates the signal |
| `norm_hr` | HR / 5 | 5 HR in a week saturates the signal |
| `norm_rbi` | RBI / 12 | 12 RBI in a week saturates the signal |

Each component is clamped to [0, 1]. AVG is the primary signal (a hitter who gets on base consistently); HR and RBI capture power production and run-driving in counting form — fantasy managers think in total HR/RBI over a week, not per-PA rates.

### Starter composite

```
starter_composite = 0.40 × (1 − ERA/6.0) + 0.35 × (K/9 / 15.0) + 0.25 × (1 − WHIP/2.0)
```

ERA and WHIP are inverted: lower values produce a higher composite. A starter with ERA ≥ 6.0 or WHIP ≥ 2.0 scores 0 for that component. K/9 rewards dominant strikeout pitchers.

### Closer composite

```
closer_composite = 0.35 × (1 − ERA/6.0) + 0.40 × SV% + 0.25 × (K/9 / 15.0)
```

Save percentage is the primary signal for closers — a reliable closer converting opportunities is the most valuable fantasy trait. K/9 is computed inline (strikeouts × 9 / IP) since `RollingCloserStats` tracks the raw inputs. ERA uses the same ceiling as starters.

Starters and closers are merged onto a single "pitchers" leaderboard. Both composites map to [0, 1], making scores directly comparable.

A pitcher can qualify in **both** roles within one window — a swingman who makes a start and also records a save/hold in relief lands in the starter pool and the closer pool. Those are collapsed to a single entry per player, keeping the **higher-scoring role** (the one that best represents his contribution), so nobody is listed twice.

### Cold-list selection

"Cold" must mean *genuinely struggling*, not merely *relatively worst in whatever pool exists tonight*. Two guards enforce that:

1. **Hot and cold are disjoint.** Anyone already on the hot list is removed from the cold candidates. There is no fallback that reuses the full pool — if removing the hot players leaves fewer than N candidates, the cold list is simply shorter (or empty). This prevents the failure mode where a thin slate put the single hottest pitcher in baseball onto the cold board.

2. **Absolute cold gate.** A player only appears on the cold list if their composite is at or below an absolute threshold (`COLD_HITTER_COMPOSITE_MAX`, `COLD_PITCHER_COMPOSITE_MAX`). When nobody scores that low, the cold list is **empty** — the honest answer is "nobody's cold right now," not a manufactured list of good players who happened to rank lowest. The caps are role-specific because hitter and pitcher composites center on different values.

The hitter cold list uses the identical two guards.

### Why counting stats for hitters, rate stats for pitchers?

Fantasy managers evaluate hitters over a 7-day window by accumulation: "he hit 4 HR and drove in 10 runs." They evaluate pitchers by quality: "his ERA was 1.50 over two starts." The composite formulas reflect these natural evaluation frames.

---

## Luck filter (Statcast confirmation)

The luck filter answers: **should I trust this streak?** It compares a player's short-window traditional stats against their season-level Statcast metrics.

### How it works

Season-level Statcast data from FanGraphs (fetched via `pybaseball`) provides a baseline measure of a player's true quality — how hard they hit the ball, or how well they suppress contact. The 7-day rolling stats show current form. The luck filter compares the two:

| Rolling streak | Statcast quality | Status | Meaning |
|---------------|-----------------|--------|---------|
| Hot (high composite) | ✅ Good | `CONFIRMED` | Streak aligns with underlying quality — sustainable |
| Hot (high composite) | ❌ Poor | `LUCKY` | Overperforming underlying quality — may regress |
| Cold (low composite) | ✅ Good | `UNLUCKY` | Underperforming underlying quality — expect bounce-back |
| Cold (low composite) | ❌ Poor | `CONFIRMED` | Streak aligns with underlying quality — truly struggling |
| Any | Missing | `UNCONFIRMED` | No Statcast data available for this player |

### Quality thresholds

**Hitters:** A hitter is considered a quality contact-maker if their season xwOBA ≥ 0.320 (`LUCK_XWOBA_THRESHOLD`). xwOBA (expected weighted on-base average) combines exit velocity, launch angle, and expected outcomes — the best single predictor of future offensive production.

**Pitchers:** A pitcher is considered quality if their season FIP ≤ 4.00 (`LUCK_FIP_THRESHOLD`). FIP (Fielding Independent Pitching) strips out balls in play (which are more luck-dependent) and focuses on what the pitcher controls: strikeouts, walks, and home runs.

### Name-matching limitation

Statcast data is keyed by player name (from FanGraphs), while rolling stats use MLB Stats API player IDs with a `full_name` field. The lookup matches on `full_name`. Edge cases where names differ between sources (accent marks, periods in initials) result in `UNCONFIRMED` status. This is acceptable for V2 — affected players still appear on leaderboards, just without luck annotation.

### Graceful degradation

If `pybaseball` fails (import error, scraping failure, rate limit), both Statcast fetch functions return empty dicts. All players receive `UNCONFIRMED` status. The leaderboards still render with traditional stats — the luck column simply shows "—" instead of a badge.

---

## Breakout detection

A breakout player is someone sustaining elevated performance, not just having a single hot week.

### Algorithm

1. Identify 7-day hot players (top N by composite).
2. For each hot player, check if they also appear in the 15-day qualified pool.
3. Compute the 15-day composite for all qualified players in the 15-day window.
4. Calculate the median 15-day composite across all qualified players.
5. If the hot player's 15-day composite ≥ the median → **breakout**.

The median serves as a self-calibrating threshold: during a league-wide hitting surge (e.g., warm summer months), the bar rises naturally. During a cold stretch, it drops.

### Breakout entries

Breakout entries carry the 15-day rolling stats (not the 7-day stats), since the point of the breakout label is sustained performance over the longer window. The luck filter is applied with `is_hot=True` — a breakout is a confirmed hot streak by definition.

### Overlap with hot list

A player can appear on both the hot list and the breakout list. The hot list says "who's performing best right now"; the breakout list says "who's sustaining it." They serve different purposes even with overlap.

### Tuning guide

| Want to... | Change in `config.py` |
|------------|----------------------|
| Require more contact quality to be "confirmed" | Increase `LUCK_XWOBA_THRESHOLD` |
| Require better pitching quality to be "confirmed" | Decrease `LUCK_FIP_THRESHOLD` |
| Weight batting average more in hitter ranking | Increase `HITTER_W_AVG`, decrease others |
| Weight strikeouts more for starters | Increase `STARTER_W_K9` |
| Weight save percentage more for closers | Increase `CLOSER_W_SV_PCT` |
| Adjust what ERA/WHIP/K9 "caps the signal" | Change `PITCHER_CEILING_ERA` / `_K9` / `_WHIP` |
| Require higher HR output for top hitter scores | Decrease `HITTER_CEILING_HR` |
| Make the cold list stricter (fewer, colder players) | Decrease `COLD_HITTER_COMPOSITE_MAX` / `COLD_PITCHER_COMPOSITE_MAX` |
| Allow more borderline players onto the cold list | Increase `COLD_HITTER_COMPOSITE_MAX` / `COLD_PITCHER_COMPOSITE_MAX` |
