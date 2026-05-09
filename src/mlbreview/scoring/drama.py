"""Drama scoring — ranks yesterday's games by how dramatic they were.

The drama score captures "great games to watch the highlights of," not
rare-stat counting. A walk-off win in a 3-2 game beats an 8-0 no-hitter.

Formula
-------
drama = W_MAX_WPA   × norm_max_wpa
      + W_LATE_WPA  × norm_late_inning_peak_wpa
      + W_MARGIN    × inverse_margin

Where:
    norm_max_wpa         = |max single-play WPA| / CEILING  → [0, 1]
    norm_late_inning_peak = late-inning max |WPA| / CEILING → [0, 1]
    inverse_margin       = 1 / (1 + final_margin)           → (0, 1]

All weights and the WPA ceiling are defined in `config.py`. See
`docs/formulas.md` for the plain-language explainer, including why
late-inning peak |WPA| substitutes for Leverage Index.

Tunable knobs (in config.py):
    DRAMA_W_MAX_WPA, DRAMA_W_LATE_WPA, DRAMA_W_MARGIN,
    DRAMA_MAX_WPA_CEILING, DRAMA_LATE_INNING_THRESHOLD
"""

from __future__ import annotations

from dataclasses import dataclass

from mlbreview.config import (
    DRAMA_MAX_WPA_CEILING,
    DRAMA_W_LATE_WPA,
    DRAMA_W_MARGIN,
    DRAMA_W_MAX_WPA,
)
from mlbreview.data.game import GameFeed
from mlbreview.data.schedule import Game


class Category:
    WALKOFF = "walkoff"
    COMEBACK = "comeback"
    PITCHERS_DUEL = "pitchers_duel"
    FEAT = "feat"
    EXTRA_INNINGS = "extra_innings"
    DEFAULT = "default"


@dataclass(frozen=True)
class ScoredGame:
    """A game paired with its drama score and category tag."""

    game: Game
    feed: GameFeed
    score: float
    category: str


def drama_score(feed: GameFeed, margin: int) -> float:
    """Compute the drama score for a single completed game.

    Parameters
    ----------
    feed : GameFeed
        Play-level WPA data from the win-probability endpoint.
    margin : int
        Absolute run differential (|away_score - home_score|).

    Returns
    -------
    float
        Drama score in roughly [0, 1]. Higher means more dramatic.
        Returns 0.0 when the feed has no play data.
    """
    if not feed.has_data:
        return 0.0

    norm_max_wpa = min(feed.max_wpa_swing / DRAMA_MAX_WPA_CEILING, 1.0)
    norm_late_wpa = min(feed.late_inning_max_wpa / DRAMA_MAX_WPA_CEILING, 1.0)
    inverse_margin = 1.0 / (1.0 + margin)

    return (
        DRAMA_W_MAX_WPA * norm_max_wpa
        + DRAMA_W_LATE_WPA * norm_late_wpa
        + DRAMA_W_MARGIN * inverse_margin
    )


def classify_game(game: Game, feed: GameFeed) -> str:
    """Assign a storyline category tag to a completed game.

    Categories are used by the variety rule to ensure the top 3 storylines
    aren't all the same type. A game gets exactly one category — the first
    match in priority order wins.

    Priority order:
        walkoff > extra_innings > comeback > pitchers_duel > feat > default
    """
    if not feed.has_data:
        return Category.DEFAULT

    if _is_walkoff(game, feed):
        return Category.WALKOFF
    if game.extra_innings:
        return Category.EXTRA_INNINGS
    if _is_comeback(game, feed):
        return Category.COMEBACK
    if _is_pitchers_duel(game):
        return Category.PITCHERS_DUEL
    if _is_feat(game):
        return Category.FEAT
    return Category.DEFAULT


def score_games(
    games: list[Game], feeds: dict[int, GameFeed]
) -> list[ScoredGame]:
    """Score and classify a batch of completed games.

    Parameters
    ----------
    games : list[Game]
        Yesterday's completed games.
    feeds : dict[int, GameFeed]
        Mapping of gamePk → GameFeed. Games without a feed entry are
        scored at 0.0 with category "default".

    Returns
    -------
    list[ScoredGame]
        All games with scores and categories, sorted by score descending.
    """
    scored = []
    for game in games:
        feed = feeds.get(game.gamePk, _empty_feed(game.gamePk))
        s = drama_score(feed, game.margin)
        cat = classify_game(game, feed)
        scored.append(ScoredGame(game=game, feed=feed, score=s, category=cat))
    scored.sort(key=lambda sg: sg.score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Category detection helpers
# ---------------------------------------------------------------------------


def _is_walkoff(game: Game, feed: GameFeed) -> bool:
    """Walk-off: home team wins and the biggest play is in the final inning."""
    if game.home_score <= game.away_score:
        return False
    if feed.biggest_play is None:
        return False
    last_inning = max((p.inning for p in feed.plays), default=0)
    return (
        feed.biggest_play.inning == last_inning
        and feed.biggest_play.half_inning == "bottom"
    )


def _is_comeback(game: Game, feed: GameFeed) -> bool:
    """Comeback: winning team trailed by 3+ runs at some point.

    Detected by checking if the eventual winner's win probability dipped
    below 20% at any point during the game.
    """
    if not feed.plays:
        return False
    home_won = game.home_score > game.away_score
    for play in feed.plays:
        if play.home_win_probability is not None:
            if home_won and play.home_win_probability < 20.0:
                return True
            if not home_won and play.home_win_probability > 80.0:
                return True
    return False


def _is_pitchers_duel(game: Game) -> bool:
    """Pitchers' duel: combined runs <= 4 and margin <= 1."""
    total_runs = game.away_score + game.home_score
    return total_runs <= 4 and game.margin <= 1


def _is_feat(game: Game) -> bool:
    """Notable feat: one team scored 10+ runs, or a shutout with 7+ runs."""
    high_scoring = game.away_score >= 10 or game.home_score >= 10
    dominant_shutout = (game.away_score == 0 and game.home_score >= 7) or (
        game.home_score == 0 and game.away_score >= 7
    )
    return high_scoring or dominant_shutout


def _empty_feed(gamePk: int) -> GameFeed:
    return GameFeed(
        gamePk=gamePk,
        plays=(),
        max_wpa_swing=0.0,
        late_inning_max_wpa=0.0,
        biggest_play=None,
    )
