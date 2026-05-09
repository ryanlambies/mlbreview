"""Drama, hype, and variety scoring formulas.

Public API:
    drama: drama_score, classify_game, score_games, ScoredGame, Category
    hype:  hype_score, select_most_hyped, load_star_ids, GameContext, ScoredTonightGame
    variety: apply_variety_rule
"""

from mlbreview.scoring.drama import (
    Category,
    ScoredGame,
    classify_game,
    drama_score,
    score_games,
)
from mlbreview.scoring.hype import (
    GameContext,
    ScoredTonightGame,
    hype_score,
    load_star_ids,
    score_tonight_games,
    select_most_hyped,
)
from mlbreview.scoring.variety import apply_variety_rule

__all__ = [
    "Category",
    "GameContext",
    "ScoredGame",
    "ScoredTonightGame",
    "apply_variety_rule",
    "classify_game",
    "drama_score",
    "hype_score",
    "load_star_ids",
    "score_games",
    "score_tonight_games",
    "select_most_hyped",
]
