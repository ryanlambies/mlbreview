"""Hype scoring — picks tonight's most-hyped game for the preview section.

The hype score is a composite of four sub-signals that capture viewer
interest independent of any team allegiance.

Formula
-------
hype = W_PITCHING  × pitching_quality
     + W_STAKES    × stakes
     + W_STARS     × star_density
     + W_NATIONAL  × national_broadcast_flag

Sub-signals:
    pitching_quality  = avg(inverse-ERA of both starters), normalized to [0,1]
                        Unknown pitcher → league-average ERA (4.50) fallback
    stakes            = sum of discrete flags, capped at 1.0:
                        +0.4 division rivals
                        +0.3 both teams above .500
                        +0.3 playoff-race delta ≤ 3 games
    star_density      = count(stars across both rosters) / DIVISOR, clamped [0,1]
    national_broadcast = 1.0 if any broadcast is national, else 0.0

All weights are in `config.py`. See `docs/formulas.md` for the full explainer.

Tunable knobs (in config.py):
    HYPE_W_PITCHING, HYPE_W_STAKES, HYPE_W_STARS, HYPE_W_NATIONAL,
    HYPE_STAKES_DIVISION_RIVALS, HYPE_STAKES_BOTH_ABOVE_500,
    HYPE_STAKES_PLAYOFF_RACE_DELTA, HYPE_STAR_DENSITY_DIVISOR
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from mlbreview.config import (
    HYPE_STAKES_BOTH_ABOVE_500,
    HYPE_STAKES_DIVISION_RIVALS,
    HYPE_STAKES_PLAYOFF_RACE_DELTA,
    HYPE_STAR_DENSITY_DIVISOR,
    HYPE_W_NATIONAL,
    HYPE_W_PITCHING,
    HYPE_W_STAKES,
    HYPE_W_STARS,
)
from mlbreview.data.schedule import TonightGame

logger = logging.getLogger(__name__)

LEAGUE_AVG_ERA = 4.50
ERA_NORMALIZATION_CEILING = 1.0 / 1.50

STARS_JSON = Path(__file__).resolve().parent.parent.parent.parent / "config" / "stars.json"


@dataclass(frozen=True)
class GameContext:
    """Additional context the pipeline provides per tonight-game for hype scoring.

    The TonightGame dataclass carries schedule-level data (teams, pitchers,
    broadcasts). GameContext carries the enrichment the pipeline resolves
    separately: pitcher season ERA, division membership, and standings deltas.
    """

    away_pitcher_era: float | None
    home_pitcher_era: float | None
    same_division: bool
    both_above_500: bool
    playoff_delta: int | None
    away_roster_ids: frozenset[int]
    home_roster_ids: frozenset[int]


@dataclass(frozen=True)
class ScoredTonightGame:
    """A tonight-game paired with its hype score."""

    game: TonightGame
    score: float


def load_star_ids(path: Path = STARS_JSON) -> frozenset[int]:
    """Load the curated star-player MLBAM ID set from ``config/stars.json``."""
    try:
        data = json.loads(path.read_text())
        return frozenset(int(entry["id"]) for entry in data)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        logger.warning("Could not load stars.json from %s; star_density will be 0", path)
        return frozenset()


def _pitching_quality(
    away_era: float | None, home_era: float | None
) -> float:
    """Compute the pitching-quality sub-signal.

    Uses inverse-ERA so lower ERA → higher quality. Unknown pitchers
    fall back to league average (4.50 ERA).
    """
    away = 1.0 / max(away_era or LEAGUE_AVG_ERA, 0.50)
    home = 1.0 / max(home_era or LEAGUE_AVG_ERA, 0.50)
    avg_inv = (away + home) / 2.0
    return min(avg_inv / ERA_NORMALIZATION_CEILING, 1.0)


def _stakes(
    same_division: bool,
    both_above_500: bool,
    playoff_delta: int | None,
) -> float:
    """Compute the stakes sub-signal (additive discrete flags, capped at 1.0)."""
    total = 0.0
    if same_division:
        total += HYPE_STAKES_DIVISION_RIVALS
    if both_above_500:
        total += HYPE_STAKES_BOTH_ABOVE_500
    if playoff_delta is not None and playoff_delta <= 3:
        total += HYPE_STAKES_PLAYOFF_RACE_DELTA
    return min(total, 1.0)


def _star_density(
    away_roster: frozenset[int],
    home_roster: frozenset[int],
    stars: frozenset[int],
) -> float:
    """Compute the star-density sub-signal.

    Counts how many stars appear across both rosters, divided by
    HYPE_STAR_DENSITY_DIVISOR (default 4), clamped to [0, 1].
    """
    count = len((away_roster | home_roster) & stars)
    return min(count / HYPE_STAR_DENSITY_DIVISOR, 1.0)


def hype_score(
    game: TonightGame,
    context: GameContext,
    stars: frozenset[int],
) -> float:
    """Compute the composite hype score for a tonight-game.

    Parameters
    ----------
    game : TonightGame
        Schedule-level data (teams, pitchers, broadcasts).
    context : GameContext
        Enrichment data the pipeline resolves (ERA, division, standings).
    stars : frozenset[int]
        Set of MLBAM player IDs considered "stars."

    Returns
    -------
    float
        Hype score in roughly [0, 1]. Higher means more viewer interest.
    """
    pq = _pitching_quality(context.away_pitcher_era, context.home_pitcher_era)
    st = _stakes(context.same_division, context.both_above_500, context.playoff_delta)
    sd = _star_density(context.away_roster_ids, context.home_roster_ids, stars)
    nb = 1.0 if game.is_national else 0.0

    return (
        HYPE_W_PITCHING * pq
        + HYPE_W_STAKES * st
        + HYPE_W_STARS * sd
        + HYPE_W_NATIONAL * nb
    )


def score_tonight_games(
    games: list[TonightGame],
    contexts: dict[int, GameContext],
    stars: frozenset[int],
) -> list[ScoredTonightGame]:
    """Score a batch of tonight-games and return sorted by hype descending."""
    scored = []
    for game in games:
        ctx = contexts.get(game.gamePk)
        if ctx is None:
            scored.append(ScoredTonightGame(game=game, score=0.0))
            continue
        s = hype_score(game, ctx, stars)
        scored.append(ScoredTonightGame(game=game, score=s))
    scored.sort(key=lambda sg: sg.score, reverse=True)
    return scored


def select_most_hyped(
    games: list[TonightGame],
    contexts: dict[int, GameContext],
    stars: frozenset[int],
) -> ScoredTonightGame | None:
    """Return the single most-hyped tonight-game, or None if no games."""
    scored = score_tonight_games(games, contexts, stars)
    return scored[0] if scored else None
