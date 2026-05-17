"""Tests for drama scoring — storyline ranking by game drama.

Covers: formula numeric behavior, category classification, batch scoring.
Fixture-driven tests use the committed winprob JSON files from U2.
"""

from __future__ import annotations

import json
from pathlib import Path

from mlbreview.data.game import GameFeed, Play, parse_winprob
from mlbreview.data.schedule import Decisions, Game, InningLine
from mlbreview.scoring.drama import (
    Category,
    ScoredGame,
    classify_game,
    drama_score,
    score_games,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _game(
    *,
    gamePk: int = 1,
    away_score: int = 3,
    home_score: int = 5,
    innings: int = 9,
) -> Game:
    return Game(
        gamePk=gamePk,
        game_type="R",
        status="Final",
        away_team_name="Away",
        away_team_abbr="AWY",
        away_score=away_score,
        home_team_name="Home",
        home_team_abbr="HME",
        home_score=home_score,
        decisions=Decisions(winner="W", loser="L", save=None),
        line_score=tuple(
            InningLine(inning=i + 1, away_runs=0, home_runs=0)
            for i in range(innings)
        ),
    )


def _feed(
    *,
    gamePk: int = 1,
    max_wpa: float = 30.0,
    late_wpa: float = 25.0,
    biggest_inning: int = 9,
    biggest_half: str = "bottom",
) -> GameFeed:
    biggest = Play(
        description="big play",
        event="Home Run",
        inning=biggest_inning,
        half_inning=biggest_half,
        wpa=max_wpa,
        home_win_probability=None,
        away_win_probability=None,
        batter="Batter",
        batter_id=None,
        pitcher="Pitcher",
        pitcher_id=None,
    )
    return GameFeed(
        gamePk=gamePk,
        plays=(biggest,),
        max_wpa_swing=max_wpa,
        late_inning_max_wpa=late_wpa,
        biggest_play=biggest,
    )


def _empty_feed(gamePk: int = 1) -> GameFeed:
    return GameFeed(
        gamePk=gamePk,
        plays=(),
        max_wpa_swing=0.0,
        late_inning_max_wpa=0.0,
        biggest_play=None,
    )


# ---------------------------------------------------------------------------
# drama_score — numeric behavior
# ---------------------------------------------------------------------------


class TestDramaScore:
    def test_walkoff_scores_higher_than_blowout(self) -> None:
        walkoff_feed = parse_winprob(_load("winprob_walkoff.json"), gamePk=1)
        blowout_feed = parse_winprob(_load("winprob_blowout.json"), gamePk=2)

        walkoff_s = drama_score(walkoff_feed, margin=1)
        blowout_s = drama_score(blowout_feed, margin=8)

        assert walkoff_s > blowout_s

    def test_walkoff_scores_high(self) -> None:
        feed = parse_winprob(_load("winprob_walkoff.json"), gamePk=1)
        s = drama_score(feed, margin=1)
        assert s >= 0.60

    def test_blowout_in_bottom_quartile(self) -> None:
        feed = parse_winprob(_load("winprob_blowout.json"), gamePk=2)
        s = drama_score(feed, margin=8)
        assert s <= 0.25

    def test_close_game_beats_blowout_with_similar_wpa(self) -> None:
        """A 2-1 game with moderate WPA beats a 12-1 game with the same WPA."""
        close_feed = _feed(max_wpa=20.0, late_wpa=15.0)
        blowout_feed = _feed(max_wpa=20.0, late_wpa=15.0)

        close_s = drama_score(close_feed, margin=1)
        blowout_s = drama_score(blowout_feed, margin=11)

        assert close_s > blowout_s

    def test_empty_feed_returns_zero(self) -> None:
        s = drama_score(_empty_feed(), margin=3)
        assert s == 0.0

    def test_score_is_bounded(self) -> None:
        extreme_feed = _feed(max_wpa=100.0, late_wpa=100.0)
        s = drama_score(extreme_feed, margin=0)
        assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# classify_game — category tagging
# ---------------------------------------------------------------------------


class TestClassifyGame:
    def test_walkoff_detected(self) -> None:
        game = _game(home_score=5, away_score=4)
        feed = _feed(biggest_inning=9, biggest_half="bottom")
        assert classify_game(game, feed) == Category.WALKOFF

    def test_not_walkoff_when_away_wins(self) -> None:
        game = _game(home_score=3, away_score=5)
        feed = _feed(biggest_inning=9, biggest_half="top")
        assert classify_game(game, feed) != Category.WALKOFF

    def test_extra_innings(self) -> None:
        game = _game(innings=11, home_score=4, away_score=3)
        feed = _feed(biggest_inning=11, biggest_half="bottom")
        # walkoff takes priority over extra_innings
        cat = classify_game(game, feed)
        assert cat == Category.WALKOFF

    def test_extra_innings_non_walkoff(self) -> None:
        game = _game(innings=11, home_score=3, away_score=5)
        feed = _feed(biggest_inning=10, biggest_half="top")
        assert classify_game(game, feed) == Category.EXTRA_INNINGS

    def test_comeback(self) -> None:
        """Away team wins after home had >80% win probability → comeback."""
        plays = (
            Play("", "", 1, "top", -5.0, 85.0, 15.0, None, None, None, None),
            Play("", "", 5, "bottom", 5.0, 88.0, 12.0, None, None, None, None),
            Play("", "", 9, "top", 30.0, 30.0, 70.0, None, None, None, None),
        )
        feed = GameFeed(
            gamePk=1,
            plays=plays,
            max_wpa_swing=30.0,
            late_inning_max_wpa=30.0,
            biggest_play=plays[2],
        )
        game = _game(home_score=4, away_score=5)
        assert classify_game(game, feed) == Category.COMEBACK

    def test_pitchers_duel(self) -> None:
        game = _game(home_score=2, away_score=1)
        feed = _feed(biggest_inning=5, biggest_half="top")
        assert classify_game(game, feed) == Category.PITCHERS_DUEL

    def test_not_pitchers_duel_high_scoring(self) -> None:
        game = _game(home_score=4, away_score=3)
        feed = _feed(biggest_inning=5, biggest_half="top")
        assert classify_game(game, feed) != Category.PITCHERS_DUEL

    def test_feat_high_scoring(self) -> None:
        game = _game(home_score=3, away_score=12)
        feed = _feed(biggest_inning=3, biggest_half="top")
        assert classify_game(game, feed) == Category.FEAT

    def test_feat_dominant_shutout(self) -> None:
        game = _game(home_score=0, away_score=8)
        feed = _feed(biggest_inning=3, biggest_half="top")
        assert classify_game(game, feed) == Category.FEAT

    def test_default_category(self) -> None:
        game = _game(home_score=6, away_score=4)
        feed = _feed(biggest_inning=5, biggest_half="top")
        assert classify_game(game, feed) == Category.DEFAULT

    def test_empty_feed_returns_default(self) -> None:
        game = _game()
        assert classify_game(game, _empty_feed()) == Category.DEFAULT


# ---------------------------------------------------------------------------
# score_games — batch scoring
# ---------------------------------------------------------------------------


class TestScoreGames:
    def test_returns_sorted_descending(self) -> None:
        games = [_game(gamePk=i, away_score=i, home_score=5) for i in range(3)]
        feeds = {
            0: _feed(gamePk=0, max_wpa=10.0, late_wpa=5.0),
            1: _feed(gamePk=1, max_wpa=30.0, late_wpa=25.0),
            2: _feed(gamePk=2, max_wpa=20.0, late_wpa=15.0),
        }
        scored = score_games(games, feeds)
        assert scored[0].game.gamePk == 1
        assert scored[0].score >= scored[1].score >= scored[2].score

    def test_missing_feed_scores_zero(self) -> None:
        game = _game(gamePk=99)
        scored = score_games([game], {})
        assert scored[0].score == 0.0
        assert scored[0].category == Category.DEFAULT

    def test_empty_games_list(self) -> None:
        assert score_games([], {}) == []
