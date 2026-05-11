"""Tests for the pipeline orchestrator."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mlbreview.config import Config
from mlbreview.data.client import MlbApiError
from mlbreview.data.game import GameFeed, Play
from mlbreview.data.schedule import (
    Broadcast,
    Decisions,
    Game,
    InningLine,
    ProbablePitcher,
    TonightGame,
)
from mlbreview.data.transactions import Transaction, TransactionCategory
from mlbreview.pipeline import (
    _both_above_500,
    _build_index_entries,
    _is_active_season,
    run,
)
from mlbreview.render.pages import IndexEntry
from mlbreview.scoring.drama import ScoredGame
from mlbreview.scoring.hype import ScoredTonightGame


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _stub_config() -> Config:
    return Config(
        anthropic_api_key="test-key",
        resend_api_key="test-resend-key",
        digest_to_email="test@example.com",
        digest_from_email="MLB Digest <onboarding@resend.dev>",
    )


def _make_game(
    gamePk: int = 12345,
    away_name: str = "New York Yankees",
    away_abbr: str = "NYY",
    away_score: int = 3,
    home_name: str = "Los Angeles Angels",
    home_abbr: str = "LAA",
    home_score: int = 4,
) -> Game:
    return Game(
        gamePk=gamePk,
        game_type="R",
        status="Final",
        away_team_name=away_name,
        away_team_abbr=away_abbr,
        away_score=away_score,
        home_team_name=home_name,
        home_team_abbr=home_abbr,
        home_score=home_score,
        decisions=Decisions(winner="Tyler Anderson", loser="Gerrit Cole", save=None),
        line_score=(
            InningLine(1, 0, 1), InningLine(2, 1, 0), InningLine(3, 0, 0),
            InningLine(4, 0, 1), InningLine(5, 1, 0), InningLine(6, 0, 0),
            InningLine(7, 0, 1), InningLine(8, 1, 0), InningLine(9, 0, 1),
        ),
    )


def _make_play(
    description: str = "Single to left",
    inning: int = 5,
    wpa: float = 3.5,
    batter: str = "Mike Trout",
    pitcher: str = "Gerrit Cole",
) -> Play:
    return Play(
        description=description,
        event="Single",
        inning=inning,
        half_inning="top",
        wpa=wpa,
        home_win_probability=55.0,
        away_win_probability=45.0,
        batter=batter,
        pitcher=pitcher,
    )


def _make_feed(gamePk: int = 12345) -> GameFeed:
    plays = (
        _make_play(
            description="Walk-off homer by Mike Trout",
            inning=9, wpa=42.0,
            batter="Mike Trout", pitcher="Gerrit Cole",
        ),
        _make_play(
            description="Double by Shohei Ohtani",
            inning=7, wpa=-15.0,
            batter="Shohei Ohtani", pitcher="Tyler Glasnow",
        ),
    )
    return GameFeed(
        gamePk=gamePk,
        plays=plays,
        max_wpa_swing=42.0,
        late_inning_max_wpa=42.0,
        biggest_play=plays[0],
    )


def _make_tonight_game(gamePk: int = 67890) -> TonightGame:
    return TonightGame(
        gamePk=gamePk,
        game_type="R",
        game_date_utc="2025-08-16T23:10:00Z",
        away_team_name="New York Yankees",
        away_team_abbr="NYY",
        away_record="62-50",
        home_team_name="Los Angeles Dodgers",
        home_team_abbr="LAD",
        home_record="70-42",
        away_probable_pitcher=ProbablePitcher(player_id=543037, full_name="Gerrit Cole"),
        home_probable_pitcher=ProbablePitcher(player_id=660271, full_name="Shohei Ohtani"),
        broadcasts=(
            Broadcast(name="ESPN", type="TV", is_national=True),
        ),
    )


# ---------------------------------------------------------------------------
# Tests — season guard
# ---------------------------------------------------------------------------


class TestSeasonGuard:
    def test_mid_season_is_active(self):
        assert _is_active_season(date(2025, 7, 15))

    def test_opening_day_is_active(self):
        assert _is_active_season(date(2025, 3, 20))

    def test_january_is_inactive(self):
        assert not _is_active_season(date(2026, 1, 15))

    def test_december_is_inactive(self):
        assert not _is_active_season(date(2025, 12, 1))

    def test_february_is_inactive(self):
        assert not _is_active_season(date(2025, 2, 15))

    def test_november_10_is_active(self):
        assert _is_active_season(date(2025, 11, 10))

    def test_november_11_is_inactive(self):
        assert not _is_active_season(date(2025, 11, 11))


class TestSeasonPause:
    def test_january_date_exits_zero(self):
        result = run(
            date(2026, 1, 15),
            dry_run=True,
            out_dir="/tmp/test-pipeline-season",
            config=_stub_config(),
        )
        assert result == 0


# ---------------------------------------------------------------------------
# Tests — off-day branch
# ---------------------------------------------------------------------------


class TestOffDayBranch:
    def test_no_finals_renders_off_day(self, tmp_path):
        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[]) as mock_fetch_finals, \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[_make_tonight_game()]):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

            assert result == 0
            day_file = tmp_path / "digests" / "2025-08-15" / "index.html"
            assert day_file.exists()
            content = day_file.read_text()
            assert "No games last night" in content or "no games" in content.lower()


# ---------------------------------------------------------------------------
# Tests — idempotency guard
# ---------------------------------------------------------------------------


class TestIdempotencyGuard:
    def test_existing_dashboard_skips_run(self, tmp_path):
        day_dir = tmp_path / "digests" / "2025-08-15"
        day_dir.mkdir(parents=True)
        (day_dir / "index.html").write_text("<html>already done</html>")

        result = run(
            date(2025, 8, 15),
            dry_run=True,
            out_dir=str(tmp_path),
            config=_stub_config(),
        )

        assert result == 0


# ---------------------------------------------------------------------------
# Tests — happy path (fully mocked)
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_pipeline_dry_run(self, tmp_path):
        game = _make_game()
        feed = _make_feed()
        tonight = _make_tonight_game()
        transactions = [
            Transaction(
                transaction_id=1,
                date="2025-08-15",
                category=TransactionCategory.TRADE,
                player_name="Juan Soto",
                team_name="New York Mets",
                description="Traded to the Mets",
            ),
        ]

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[tonight]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=transactions), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="Mike Trout hit a walk-off homer."), \
             patch("mlbreview.pipeline.write_preview", return_value="Gerrit Cole faces Shohei Ohtani tonight."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod:

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0

        day_file = tmp_path / "digests" / "2025-08-15" / "index.html"
        assert day_file.exists()

        index_file = tmp_path / "index.html"
        assert index_file.exists()

    def test_production_sends_email(self, tmp_path):
        game = _make_game()
        feed = _make_feed()

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A great game."), \
             patch("mlbreview.pipeline.write_preview", return_value="Preview."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod, \
             patch("mlbreview.pipeline.resend") as mock_resend:

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            config = _stub_config()
            result = run(
                date(2025, 8, 15),
                dry_run=False,
                out_dir=str(tmp_path),
                config=config,
            )

        assert result == 0
        mock_resend.Emails.send.assert_called_once()
        send_args = mock_resend.Emails.send.call_args[0][0]
        assert send_args["to"] == ["test@example.com"]
        assert "MLB Digest" in send_args["subject"]


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_schedule_fetch_failure_returns_1(self, tmp_path):
        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", side_effect=MlbApiError("API down")):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 1

    def test_game_feed_failure_skips_game(self, tmp_path):
        game1 = _make_game(gamePk=111)
        game2 = _make_game(gamePk=222)
        feed2 = _make_feed(gamePk=222)

        def side_effect(gamePk, *, client):
            if gamePk == 111:
                raise MlbApiError("Feed fetch failed")
            return feed2

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game1, game2]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", side_effect=side_effect), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A game."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod:

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0
        assert (tmp_path / "digests" / "2025-08-15" / "index.html").exists()

    def test_transactions_failure_continues(self, tmp_path):
        game = _make_game()
        feed = _make_feed()

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", side_effect=MlbApiError("tx fail")), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A game."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod:

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0


# ---------------------------------------------------------------------------
# Tests — dry run without API key uses fallbacks
# ---------------------------------------------------------------------------


class TestDryRunNoApiKey:
    def test_no_anthropic_key_uses_fallbacks(self, tmp_path):
        game = _make_game()
        feed = _make_feed()

        config = Config(
            anthropic_api_key=None,
            resend_api_key=None,
            digest_to_email=None,
            digest_from_email="MLB Digest <onboarding@resend.dev>",
        )

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=config,
            )

        assert result == 0
        day_file = tmp_path / "digests" / "2025-08-15" / "index.html"
        assert day_file.exists()
        content = day_file.read_text()
        assert "New York Yankees" in content or "Los Angeles Angels" in content


# ---------------------------------------------------------------------------
# Tests — helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_both_above_500_true(self):
        game = _make_tonight_game()
        assert _both_above_500(game) is True

    def test_both_above_500_false_when_below(self):
        game = TonightGame(
            gamePk=67890,
            game_type="R",
            game_date_utc="2025-08-16T23:10:00Z",
            away_team_name="Colorado Rockies",
            away_team_abbr="COL",
            away_record="30-80",
            home_team_name="Oakland Athletics",
            home_team_abbr="OAK",
            home_record="35-75",
            away_probable_pitcher=None,
            home_probable_pitcher=None,
            broadcasts=(),
        )
        assert _both_above_500(game) is False

    def test_both_above_500_none_record(self):
        game = TonightGame(
            gamePk=67890,
            game_type="R",
            game_date_utc="2025-08-16T23:10:00Z",
            away_team_name="Team A",
            away_team_abbr="TMA",
            away_record=None,
            home_team_name="Team B",
            home_team_abbr="TMB",
            home_record=None,
            away_probable_pitcher=None,
            home_probable_pitcher=None,
            broadcasts=(),
        )
        assert _both_above_500(game) is False

    def test_build_index_entries_from_directories(self, tmp_path):
        digests_dir = tmp_path / "digests"
        (digests_dir / "2025-08-14").mkdir(parents=True)
        (digests_dir / "2025-08-15").mkdir(parents=True)
        (digests_dir / "not-a-date").mkdir(parents=True)

        entries = _build_index_entries(tmp_path)

        assert len(entries) == 2
        assert entries[0].date == date(2025, 8, 15)
        assert entries[1].date == date(2025, 8, 14)

    def test_build_index_entries_empty(self, tmp_path):
        entries = _build_index_entries(tmp_path)
        assert entries == []


# ---------------------------------------------------------------------------
# Tests — integration: call order
# ---------------------------------------------------------------------------


class TestCallOrder:
    def test_pipeline_calls_modules_in_order(self, tmp_path):
        game = _make_game()
        feed = _make_feed()
        tonight = _make_tonight_game()

        call_log: list[str] = []

        def log_fetch_finals(*a, **kw):
            call_log.append("fetch_finals")
            return [game]

        def log_fetch_tonight(*a, **kw):
            call_log.append("fetch_tonight")
            return [tonight]

        def log_fetch_feed(*a, **kw):
            call_log.append("fetch_game_feed")
            return feed

        def log_fetch_tx(*a, **kw):
            call_log.append("fetch_transactions")
            return []

        def log_write_storyline(*a, **kw):
            call_log.append("write_storyline")
            return "Storyline prose."

        def log_write_preview(*a, **kw):
            call_log.append("write_preview")
            return "Preview prose."

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", side_effect=log_fetch_finals), \
             patch("mlbreview.pipeline.fetch_tonight", side_effect=log_fetch_tonight), \
             patch("mlbreview.pipeline.fetch_game_feed", side_effect=log_fetch_feed), \
             patch("mlbreview.pipeline.fetch_transactions", side_effect=log_fetch_tx), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", side_effect=log_write_storyline), \
             patch("mlbreview.pipeline.write_preview", side_effect=log_write_preview), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod:

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                date(2025, 8, 15),
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0
        assert call_log.index("fetch_finals") < call_log.index("fetch_game_feed")
        assert call_log.index("fetch_game_feed") < call_log.index("write_storyline")
        assert "fetch_tonight" in call_log
        assert "write_preview" in call_log
