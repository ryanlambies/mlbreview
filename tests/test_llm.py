"""Tests for the LLM prose generation module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

from mlbreview.data.game import GameFeed, Play
from mlbreview.data.schedule import (
    Broadcast,
    Decisions,
    Game,
    InningLine,
    ProbablePitcher,
    TonightGame,
)
from mlbreview.data.stats import BatterSeasonStats
from mlbreview.llm import (
    _build_storyline_payload,
    _extract_names,
    _grounding_check,
    _storyline_fallback,
    write_preview,
    write_storyline,
)
from mlbreview.scoring.drama import ScoredGame
from mlbreview.scoring.hype import ScoredTonightGame


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_play(
    description: str = "Single to left",
    event: str = "Single",
    inning: int = 5,
    half_inning: str = "top",
    wpa: float = 3.5,
    batter: str | None = "Mike Trout",
    batter_id: int | None = 545361,
    pitcher: str | None = "Gerrit Cole",
    pitcher_id: int | None = 543037,
) -> Play:
    return Play(
        description=description,
        event=event,
        inning=inning,
        half_inning=half_inning,
        wpa=wpa,
        home_win_probability=55.0,
        away_win_probability=45.0,
        batter=batter,
        batter_id=batter_id,
        pitcher=pitcher,
        pitcher_id=pitcher_id,
    )


def _make_game_feed(plays: tuple[Play, ...] | None = None) -> GameFeed:
    if plays is None:
        plays = (
            _make_play(
                description="Walk-off homer by Mike Trout",
                event="Home Run",
                inning=9,
                half_inning="bottom",
                wpa=42.0,
                batter="Mike Trout",
                pitcher="Gerrit Cole",
            ),
            _make_play(
                description="Double by Shohei Ohtani",
                event="Double",
                inning=7,
                half_inning="top",
                wpa=-15.0,
                batter="Shohei Ohtani",
                pitcher="Tyler Glasnow",
            ),
            _make_play(
                description="Strikeout of Mookie Betts",
                event="Strikeout",
                inning=8,
                half_inning="bottom",
                wpa=5.0,
                batter="Mookie Betts",
                pitcher="Gerrit Cole",
            ),
        )
    biggest = max(plays, key=lambda p: abs(p.wpa)) if plays else None
    max_wpa = max((abs(p.wpa) for p in plays), default=0.0)
    return GameFeed(
        gamePk=12345,
        plays=plays,
        max_wpa_swing=max_wpa,
        late_inning_max_wpa=max_wpa,
        biggest_play=biggest,
    )


def _make_scored_game(feed: GameFeed | None = None) -> ScoredGame:
    if feed is None:
        feed = _make_game_feed()
    return ScoredGame(
        game=Game(
            gamePk=12345,
            game_type="R",
            status="Final",
            away_team_name="New York Yankees",
            away_team_abbr="NYY",
            away_score=3,
            home_team_name="Los Angeles Angels",
            home_team_abbr="LAA",
            home_score=4,
            decisions=Decisions(
                winner="Tyler Anderson",
                loser="Gerrit Cole",
                save="Carlos Estevez",
            ),
            line_score=(
                InningLine(1, 0, 1),
                InningLine(2, 1, 0),
                InningLine(3, 0, 0),
                InningLine(4, 0, 1),
                InningLine(5, 1, 0),
                InningLine(6, 0, 0),
                InningLine(7, 0, 1),
                InningLine(8, 1, 0),
                InningLine(9, 0, 1),
            ),
        ),
        feed=feed,
        score=0.85,
        category="walkoff",
    )


def _make_scored_tonight_game(
    away_pitcher: ProbablePitcher | None = None,
    home_pitcher: ProbablePitcher | None = None,
) -> ScoredTonightGame:
    if away_pitcher is None:
        away_pitcher = ProbablePitcher(player_id=543037, full_name="Gerrit Cole")
    if home_pitcher is None:
        home_pitcher = ProbablePitcher(player_id=660271, full_name="Shohei Ohtani")
    return ScoredTonightGame(
        game=TonightGame(
            gamePk=67890,
            game_type="R",
            game_date_utc="2025-08-15T23:10:00Z",
            away_team_name="New York Yankees",
            away_team_abbr="NYY",
            away_record="62-50",
            home_team_name="Los Angeles Dodgers",
            home_team_abbr="LAD",
            home_record="70-42",
            away_probable_pitcher=away_pitcher,
            home_probable_pitcher=home_pitcher,
            broadcasts=(
                Broadcast(name="ESPN", type="TV", is_national=True),
                Broadcast(name="YES", type="TV", is_national=False),
            ),
        ),
        score=0.92,
    )


def _mock_anthropic_response(text: str) -> MagicMock:
    """Build a mock that looks like anthropic.types.Message."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


# ---------------------------------------------------------------------------
# Tests — write_storyline
# ---------------------------------------------------------------------------


class TestWriteStoryline:
    def test_happy_path(self):
        """Mocked Anthropic returns prose; function passes it through."""
        client = MagicMock(spec=anthropic.Anthropic)
        expected = (
            "Mike Trout launched a walk-off homer in the ninth to lift the "
            "Angels over the Yankees 4-3. Gerrit Cole took the loss after "
            "surrendering the decisive blast."
        )
        client.messages.create.return_value = _mock_anthropic_response(expected)

        result = write_storyline(_make_scored_game(), client=client)

        assert result == expected
        client.messages.create.assert_called_once()

    def test_empty_feed_skips_llm(self):
        """A game with no play data returns a short factual line without calling the LLM."""
        client = MagicMock(spec=anthropic.Anthropic)
        empty_feed = GameFeed(
            gamePk=99999,
            plays=(),
            max_wpa_swing=0.0,
            late_inning_max_wpa=0.0,
            biggest_play=None,
        )
        scored = _make_scored_game(feed=empty_feed)
        result = write_storyline(scored, client=client)

        assert "New York Yankees" in result
        assert "Los Angeles Angels" in result
        assert "3" in result and "4" in result
        client.messages.create.assert_not_called()

    def test_grounding_check_rejects_hallucinated_name(self):
        """LLM fabricates 'Aaron Judge' who isn't in the payload — falls back to template."""
        client = MagicMock(spec=anthropic.Anthropic)
        hallucinated = (
            "Aaron Judge crushed a three-run homer to seal the victory for "
            "the Yankees."
        )
        client.messages.create.return_value = _mock_anthropic_response(hallucinated)

        result = write_storyline(_make_scored_game(), client=client)

        # Should get the template fallback, not the hallucinated text
        assert "Aaron Judge" not in result
        assert "New York Yankees" in result or "Los Angeles Angels" in result

    def test_retry_success(self):
        """First call raises APIError, second succeeds."""
        client = MagicMock(spec=anthropic.Anthropic)
        expected = "Mike Trout delivered the walk-off. Tyler Anderson earned the win."

        client.messages.create.side_effect = [
            anthropic.APIError(
                message="Internal error",
                request=MagicMock(),
                body=None,
            ),
            _mock_anthropic_response(expected),
        ]

        with patch("mlbreview.llm.time.sleep"):
            result = write_storyline(_make_scored_game(), client=client)

        assert result == expected
        assert client.messages.create.call_count == 2

    def test_retry_exhausted_uses_fallback(self):
        """Both calls raise APIError — returns deterministic fallback."""
        client = MagicMock(spec=anthropic.Anthropic)
        error = anthropic.APIError(
            message="Service unavailable",
            request=MagicMock(),
            body=None,
        )
        client.messages.create.side_effect = [error, error]

        with patch("mlbreview.llm.time.sleep"):
            result = write_storyline(_make_scored_game(), client=client)

        assert "New York Yankees" in result or "Los Angeles Angels" in result
        assert client.messages.create.call_count == 2

    def test_season_stats_included_in_payload(self):
        """When season_stats are provided, the LLM payload includes batter_season_stats."""
        stats = {
            "Mike Trout": BatterSeasonStats(
                player_id=545361, full_name="Mike Trout",
                home_runs=15, doubles=20, triples=2,
                hits=85, rbi=42, stolen_bases=5, avg=".285",
            ),
        }
        scored = _make_scored_game()
        payload, _ = _build_storyline_payload(scored, stats)

        assert "batter_season_stats" in payload
        trout_stats = payload["batter_season_stats"]["Mike Trout"]
        assert trout_stats["home_runs"] == 15
        assert trout_stats["doubles"] == 20
        assert trout_stats["avg"] == ".285"

    def test_season_stats_omitted_when_none(self):
        """Without season_stats, no batter_season_stats key in the payload."""
        scored = _make_scored_game()
        payload, _ = _build_storyline_payload(scored)
        assert "batter_season_stats" not in payload

    def test_season_stats_passed_to_llm(self):
        """Season stats are forwarded to the LLM call via the payload JSON."""
        client = MagicMock(spec=anthropic.Anthropic)
        expected = "Mike Trout hit his 15th home run of the season in a walk-off."
        client.messages.create.return_value = _mock_anthropic_response(expected)

        stats = {
            "Mike Trout": BatterSeasonStats(
                player_id=545361, full_name="Mike Trout",
                home_runs=15, doubles=20, triples=2,
                hits=85, rbi=42, stolen_bases=5, avg=".285",
            ),
        }
        result = write_storyline(_make_scored_game(), client=client, season_stats=stats)

        assert result == expected
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "batter_season_stats" in user_msg
        assert "15" in user_msg


# ---------------------------------------------------------------------------
# Tests — write_preview
# ---------------------------------------------------------------------------


class TestWritePreview:
    def test_happy_path(self):
        """Mocked Anthropic returns preview prose."""
        client = MagicMock(spec=anthropic.Anthropic)
        expected = (
            "Gerrit Cole faces Shohei Ohtani in a nationally televised "
            "showdown on ESPN. The Dodgers lead the season series 4-2."
        )
        client.messages.create.return_value = _mock_anthropic_response(expected)

        result = write_preview(_make_scored_tonight_game(), client=client)

        assert result == expected
        client.messages.create.assert_called_once()

    def test_no_probable_pitchers(self):
        """Both pitchers are None — preview still generates with TBD."""
        client = MagicMock(spec=anthropic.Anthropic)
        expected = (
            "The Yankees visit the Dodgers tonight on ESPN with starting "
            "pitchers yet to be announced."
        )
        client.messages.create.return_value = _mock_anthropic_response(expected)

        scored = _make_scored_tonight_game(
            away_pitcher=ProbablePitcher(player_id=None, full_name=None),
            home_pitcher=ProbablePitcher(player_id=None, full_name=None),
        )
        result = write_preview(scored, client=client)

        assert result == expected
        client.messages.create.assert_called_once()

        # Verify the payload sent to the LLM includes "TBD"
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "TBD" in user_msg


# ---------------------------------------------------------------------------
# Tests — grounding check internals
# ---------------------------------------------------------------------------


class TestGroundingCheck:
    def test_no_names_passes(self):
        assert _grounding_check("A great game with lots of action.", set())

    def test_known_name_passes(self):
        assert _grounding_check(
            "Mike Trout delivered the walk-off.",
            {"Mike Trout"},
        )

    def test_unknown_name_fails(self):
        assert not _grounding_check(
            "Aaron Judge crushed a homer.",
            {"Mike Trout"},
        )

    def test_team_names_ignored(self):
        """Phrases like 'The Dodgers' and 'The World Series' don't trigger false positives."""
        # "The Dodgers" → strip article → "Dodgers" (single word, not a name)
        assert _grounding_check("The Dodgers won the game.", set())
        # "The World Series" → strip article → "World Series" (in non-player list)
        assert _grounding_check("The World Series begins tonight.", set())

    def test_extract_names(self):
        names = _extract_names("Mike Trout hit a homer. Shohei Ohtani struck out.")
        assert "Mike Trout" in names
        assert "Shohei Ohtani" in names


# ---------------------------------------------------------------------------
# Tests — fallback templates
# ---------------------------------------------------------------------------


class TestStorylineFallback:
    def test_includes_teams_and_score(self):
        result = _storyline_fallback(_make_scored_game())
        assert "New York Yankees 3" in result
        assert "Los Angeles Angels 4" in result

    def test_includes_winning_pitcher(self):
        result = _storyline_fallback(_make_scored_game())
        assert "Tyler Anderson earned the win" in result

    def test_includes_biggest_play(self):
        result = _storyline_fallback(_make_scored_game())
        assert "Walk-off homer by Mike Trout" in result
