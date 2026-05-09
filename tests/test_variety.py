"""Tests for the variety rule — storyline diversity filtering.

Covers: category dedup within threshold, backfill, edge cases (all same
category, threshold=0), and the acceptance example from the plan (AE1).
"""

from __future__ import annotations

from mlbreview.data.game import GameFeed
from mlbreview.data.schedule import Decisions, Game, InningLine
from mlbreview.scoring.drama import Category, ScoredGame
from mlbreview.scoring.variety import apply_variety_rule


def _scored(
    *, gamePk: int = 1, score: float = 0.5, category: str = Category.DEFAULT
) -> ScoredGame:
    game = Game(
        gamePk=gamePk,
        game_type="R",
        status="Final",
        away_team_name="A",
        away_team_abbr="A",
        away_score=3,
        home_team_name="B",
        home_team_abbr="B",
        home_score=5,
        decisions=Decisions(winner=None, loser=None, save=None),
        line_score=(),
    )
    feed = GameFeed(
        gamePk=gamePk,
        plays=(),
        max_wpa_swing=0.0,
        late_inning_max_wpa=0.0,
        biggest_play=None,
    )
    return ScoredGame(game=game, feed=feed, score=score, category=category)


class TestVarietyRule:
    def test_ae1_plan_example(self) -> None:
        """AE1: walkoff(0.92), feat_a(0.88), feat_b(0.85), comeback(0.84), default(0.80).

        Variety rule should select [walkoff, feat_a, comeback], skipping
        feat_b because it shares category with feat_a and is within 10%.
        """
        candidates = [
            _scored(gamePk=1, score=0.92, category=Category.WALKOFF),
            _scored(gamePk=2, score=0.88, category=Category.FEAT),
            _scored(gamePk=3, score=0.85, category=Category.FEAT),
            _scored(gamePk=4, score=0.84, category=Category.COMEBACK),
            _scored(gamePk=5, score=0.80, category=Category.DEFAULT),
        ]
        result = apply_variety_rule(candidates, k=3, threshold=0.10)

        assert len(result) == 3
        assert result[0].game.gamePk == 1  # walkoff
        assert result[1].game.gamePk == 2  # feat A
        assert result[2].game.gamePk == 4  # comeback (feat B skipped)

    def test_all_same_category_returns_top_k(self) -> None:
        """If all candidates are the same category, still return k games."""
        candidates = [
            _scored(gamePk=i, score=0.90 - i * 0.02, category=Category.DEFAULT)
            for i in range(5)
        ]
        result = apply_variety_rule(candidates, k=3, threshold=0.10)
        assert len(result) == 3

    def test_threshold_zero_disables_variety(self) -> None:
        """threshold=0 means always pick top-k regardless of category."""
        candidates = [
            _scored(gamePk=1, score=0.92, category=Category.FEAT),
            _scored(gamePk=2, score=0.88, category=Category.FEAT),
            _scored(gamePk=3, score=0.85, category=Category.FEAT),
            _scored(gamePk=4, score=0.84, category=Category.COMEBACK),
            _scored(gamePk=5, score=0.80, category=Category.DEFAULT),
        ]
        result = apply_variety_rule(candidates, k=3, threshold=0.0)
        assert len(result) == 3
        assert [r.game.gamePk for r in result] == [1, 2, 3]

    def test_fewer_candidates_than_k(self) -> None:
        candidates = [
            _scored(gamePk=1, score=0.90),
            _scored(gamePk=2, score=0.80),
        ]
        result = apply_variety_rule(candidates, k=3)
        assert len(result) == 2

    def test_exactly_k_candidates(self) -> None:
        candidates = [
            _scored(gamePk=i, score=0.90 - i * 0.05)
            for i in range(3)
        ]
        result = apply_variety_rule(candidates, k=3)
        assert len(result) == 3

    def test_wide_score_gap_allows_same_category(self) -> None:
        """If two same-category games have scores far apart, both are accepted."""
        candidates = [
            _scored(gamePk=1, score=0.92, category=Category.FEAT),
            _scored(gamePk=2, score=0.50, category=Category.FEAT),
            _scored(gamePk=3, score=0.45, category=Category.DEFAULT),
            _scored(gamePk=4, score=0.40, category=Category.COMEBACK),
            _scored(gamePk=5, score=0.35, category=Category.WALKOFF),
        ]
        result = apply_variety_rule(candidates, k=3, threshold=0.10)
        assert len(result) == 3
        pks = [r.game.gamePk for r in result]
        assert 1 in pks
        assert 2 in pks

    def test_empty_candidates(self) -> None:
        assert apply_variety_rule([], k=3) == []

    def test_preserves_score_order(self) -> None:
        candidates = [
            _scored(gamePk=1, score=0.92, category=Category.WALKOFF),
            _scored(gamePk=2, score=0.88, category=Category.COMEBACK),
            _scored(gamePk=3, score=0.85, category=Category.FEAT),
            _scored(gamePk=4, score=0.84, category=Category.DEFAULT),
            _scored(gamePk=5, score=0.80, category=Category.EXTRA_INNINGS),
        ]
        result = apply_variety_rule(candidates, k=3, threshold=0.10)
        assert result[0].score >= result[1].score >= result[2].score
