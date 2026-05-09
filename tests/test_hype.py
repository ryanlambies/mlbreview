"""Tests for hype scoring — tonight's most-hyped game selection.

Covers: formula sub-signals, composite score, batch selection, edge cases.
"""

from __future__ import annotations

from mlbreview.data.schedule import Broadcast, ProbablePitcher, TonightGame
from mlbreview.scoring.hype import (
    GameContext,
    ScoredTonightGame,
    hype_score,
    load_star_ids,
    score_tonight_games,
    select_most_hyped,
)


def _tonight(
    *,
    gamePk: int = 1,
    away_abbr: str = "AWY",
    home_abbr: str = "HME",
    national: bool = False,
    away_pp: ProbablePitcher | None = None,
    home_pp: ProbablePitcher | None = None,
) -> TonightGame:
    broadcasts = ()
    if national:
        broadcasts = (Broadcast(name="ESPN", type="TV", is_national=True),)
    return TonightGame(
        gamePk=gamePk,
        game_type="R",
        game_date_utc="2025-08-16T23:10:00Z",
        away_team_name="Away Team",
        away_team_abbr=away_abbr,
        away_record="60-50",
        home_team_name="Home Team",
        home_team_abbr=home_abbr,
        home_record="65-45",
        away_probable_pitcher=away_pp,
        home_probable_pitcher=home_pp,
        broadcasts=broadcasts,
    )


def _ctx(
    *,
    away_era: float | None = 3.50,
    home_era: float | None = 3.00,
    same_division: bool = False,
    both_above_500: bool = True,
    playoff_delta: int | None = 2,
    away_stars: frozenset[int] | None = None,
    home_stars: frozenset[int] | None = None,
) -> GameContext:
    return GameContext(
        away_pitcher_era=away_era,
        home_pitcher_era=home_era,
        same_division=same_division,
        both_above_500=both_above_500,
        playoff_delta=playoff_delta,
        away_roster_ids=away_stars or frozenset(),
        home_roster_ids=home_stars or frozenset(),
    )


SAMPLE_STARS: frozenset[int] = frozenset({660271, 592450, 665489, 665742})


# ---------------------------------------------------------------------------
# hype_score — composite behavior
# ---------------------------------------------------------------------------


class TestHypeScore:
    def test_marquee_beats_weak_matchup(self) -> None:
        """Yankees-Dodgers on ESPN beats Rockies-Athletics local."""
        marquee = _tonight(national=True)
        marquee_ctx = _ctx(
            away_era=2.50,
            home_era=2.80,
            same_division=False,
            both_above_500=True,
            playoff_delta=1,
            away_stars=frozenset({660271, 592450}),
            home_stars=frozenset({665489, 665742}),
        )
        weak = _tonight(national=False)
        weak_ctx = _ctx(
            away_era=5.50,
            home_era=5.80,
            same_division=False,
            both_above_500=False,
            playoff_delta=15,
            away_stars=frozenset(),
            home_stars=frozenset(),
        )

        assert hype_score(marquee, marquee_ctx, SAMPLE_STARS) > hype_score(
            weak, weak_ctx, SAMPLE_STARS
        )

    def test_unknown_pitcher_doesnt_crash(self) -> None:
        game = _tonight()
        ctx = _ctx(away_era=None, home_era=None)
        s = hype_score(game, ctx, SAMPLE_STARS)
        assert 0.0 <= s <= 1.0

    def test_national_broadcast_boosts_score(self) -> None:
        base_ctx = _ctx()
        local = _tonight(national=False)
        national = _tonight(national=True)
        assert hype_score(national, base_ctx, SAMPLE_STARS) > hype_score(
            local, base_ctx, SAMPLE_STARS
        )

    def test_division_rivalry_boosts_stakes(self) -> None:
        no_rivalry = _ctx(same_division=False, both_above_500=False, playoff_delta=10)
        rivalry = _ctx(same_division=True, both_above_500=False, playoff_delta=10)
        game = _tonight()
        assert hype_score(game, rivalry, SAMPLE_STARS) > hype_score(
            game, no_rivalry, SAMPLE_STARS
        )

    def test_star_density_increases_with_more_stars(self) -> None:
        no_stars_ctx = _ctx(away_stars=frozenset(), home_stars=frozenset())
        many_stars_ctx = _ctx(
            away_stars=frozenset({660271, 592450}),
            home_stars=frozenset({665489, 665742}),
        )
        game = _tonight()
        assert hype_score(game, many_stars_ctx, SAMPLE_STARS) > hype_score(
            game, no_stars_ctx, SAMPLE_STARS
        )

    def test_score_bounded_zero_to_one(self) -> None:
        game = _tonight(national=True)
        ctx = _ctx(
            away_era=1.50,
            home_era=1.50,
            same_division=True,
            both_above_500=True,
            playoff_delta=0,
            away_stars=frozenset({660271, 592450}),
            home_stars=frozenset({665489, 665742}),
        )
        s = hype_score(game, ctx, SAMPLE_STARS)
        assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# load_star_ids
# ---------------------------------------------------------------------------


class TestLoadStarIds:
    def test_loads_from_config(self) -> None:
        stars = load_star_ids()
        assert len(stars) >= 25
        assert 660271 in stars  # Ohtani

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        stars = load_star_ids(tmp_path / "nonexistent.json")
        assert stars == frozenset()


# ---------------------------------------------------------------------------
# select_most_hyped / score_tonight_games
# ---------------------------------------------------------------------------


class TestSelectMostHyped:
    def test_returns_highest_scoring(self) -> None:
        games = [_tonight(gamePk=i) for i in range(3)]
        contexts = {
            0: _ctx(away_era=5.0, home_era=5.0, same_division=False, both_above_500=False, playoff_delta=10),
            1: _ctx(away_era=2.0, home_era=2.0, same_division=True, both_above_500=True, playoff_delta=1),
            2: _ctx(away_era=4.0, home_era=4.0, same_division=False, both_above_500=True, playoff_delta=5),
        }
        result = select_most_hyped(games, contexts, SAMPLE_STARS)
        assert result is not None
        assert result.game.gamePk == 1

    def test_empty_games_returns_none(self) -> None:
        assert select_most_hyped([], {}, SAMPLE_STARS) is None

    def test_missing_context_scores_zero(self) -> None:
        games = [_tonight(gamePk=1)]
        scored = score_tonight_games(games, {}, SAMPLE_STARS)
        assert scored[0].score == 0.0
