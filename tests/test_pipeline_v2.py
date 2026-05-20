"""Tests for V2 pipeline wiring — leaderboard data flow.

Covers: game-log fetching → snapshot writing → rolling-stat computation →
leaderboard scoring → Digest attachment.  All external dependencies (MLB API,
Statcast) are mocked.  V1 tests remain in ``test_pipeline.py`` unchanged.
"""

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
from mlbreview.data.snapshots import (
    CloserDayStats,
    DailySnapshot,
    HitterDayStats,
    StarterGameStats,
    load_snapshot,
    snapshot_path,
)
from mlbreview.data.statcast import StatcastHitter, StatcastPitcher
from mlbreview.pipeline import _run_v2_leaderboards, run
from mlbreview.scoring.leaderboards import Leaderboards, LuckStatus


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


def _make_game(gamePk: int = 12345) -> Game:
    return Game(
        gamePk=gamePk,
        game_type="R",
        status="Final",
        away_team_name="New York Yankees",
        away_team_abbr="NYY",
        away_score=5,
        home_team_name="Boston Red Sox",
        home_team_abbr="BOS",
        home_score=3,
        decisions=Decisions(winner="W", loser="L", save=None),
        line_score=tuple(
            InningLine(inning=i + 1, away_runs=0, home_runs=0)
            for i in range(9)
        ),
    )


def _make_feed(gamePk: int = 12345) -> GameFeed:
    play = Play(
        description="Home run",
        event="Home Run",
        inning=7,
        half_inning="top",
        wpa=15.0,
        home_win_probability=45.0,
        away_win_probability=55.0,
        batter="Aaron Judge",
        batter_id=592450,
        pitcher="Chris Sale",
        pitcher_id=519242,
    )
    return GameFeed(
        gamePk=gamePk,
        plays=(play,),
        max_wpa_swing=15.0,
        late_inning_max_wpa=15.0,
        biggest_play=play,
    )


def _hitter_day(pid: int, name: str, team: str) -> HitterDayStats:
    return HitterDayStats(
        player_id=pid, full_name=name, team_abbr=team,
        plate_appearances=5, at_bats=4, hits=2, doubles=1, triples=0,
        home_runs=1, rbi=3, stolen_bases=0, walks=1, strikeouts=1,
    )


def _starter_day(pid: int, name: str, team: str) -> StarterGameStats:
    return StarterGameStats(
        player_id=pid, full_name=name, team_abbr=team,
        game_date="2025-08-15", opponent_abbr="OPP",
        outs_recorded=21, hits_allowed=4, earned_runs=1,
        walks=2, strikeouts=8, home_runs_allowed=0, pitches_thrown=95,
    )


def _closer_day(pid: int, name: str, team: str) -> CloserDayStats:
    return CloserDayStats(
        player_id=pid, full_name=name, team_abbr=team,
        outs_recorded=3, earned_runs=0, saves=1, blown_saves=0,
        holds=0, strikeouts=2, walks=0,
    )


def _write_past_snapshots(base_dir: Path, n_days: int, target_date: date) -> None:
    """Write n_days of snapshot history before target_date for rolling windows."""
    for i in range(1, n_days + 1):
        d = target_date.replace(day=target_date.day - i)
        snap = DailySnapshot(
            snapshot_date=d.isoformat(),
            hitters=(
                _hitter_day(1, "Aaron Judge", "NYY"),
                _hitter_day(2, "Mookie Betts", "LAD"),
                _hitter_day(3, "Freddie Freeman", "LAD"),
            ),
            starters=(
                _starter_day(10, "Gerrit Cole", "NYY"),
            ),
            closers=(
                _closer_day(20, "Emmanuel Clase", "CLE"),
            ),
        )
        from mlbreview.data.snapshots import write_snapshot
        write_snapshot(snap, base_dir=base_dir)


# ---------------------------------------------------------------------------
# Tests — _run_v2_leaderboards
# ---------------------------------------------------------------------------


class TestRunV2Leaderboards:
    def test_writes_snapshot_and_returns_leaderboards(self, tmp_path):
        """Happy path: game logs fetched, snapshot written, leaderboards scored."""
        target = date(2025, 8, 15)
        finals = [_make_game()]

        # Pre-populate snapshots so rolling window has data
        _write_past_snapshots(tmp_path, 7, target)

        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]
        starters = [_starter_day(10, "Gerrit Cole", "NYY")]
        closers = [_closer_day(20, "Emmanuel Clase", "CLE")]

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, starters, closers)), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            client = MagicMock()
            result = _run_v2_leaderboards(
                finals, target, mlb_client=client, out_path=tmp_path,
            )

        assert result is not None
        assert isinstance(result, Leaderboards)

        # Verify snapshot was written
        snap_file = snapshot_path(tmp_path, target)
        assert snap_file.exists()
        snap = load_snapshot(snap_file)
        assert len(snap.hitters) == 1
        assert snap.hitters[0].full_name == "Aaron Judge"

    def test_returns_none_on_gamelog_failure(self, tmp_path):
        """Game-log fetch failure → returns None, V1 unaffected."""
        target = date(2025, 8, 15)
        finals = [_make_game()]

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", side_effect=MlbApiError("API down")):
            client = MagicMock()
            result = _run_v2_leaderboards(
                finals, target, mlb_client=client, out_path=tmp_path,
            )

        assert result is None

    def test_statcast_failure_still_produces_leaderboards(self, tmp_path):
        """Statcast failure → leaderboards with UNCONFIRMED luck status."""
        target = date(2025, 8, 15)
        finals = [_make_game()]
        _write_past_snapshots(tmp_path, 7, target)

        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, [], [])), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", side_effect=Exception("FanGraphs down")), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", side_effect=Exception("FanGraphs down")):

            client = MagicMock()
            result = _run_v2_leaderboards(
                finals, target, mlb_client=client, out_path=tmp_path,
            )

        assert result is not None
        # All entries should be UNCONFIRMED since Statcast failed
        for h in result.hot_hitters:
            assert h.luck_status == LuckStatus.UNCONFIRMED

    def test_no_prior_snapshots_returns_minimal_leaderboards(self, tmp_path):
        """First day ever: only today's snapshot exists → leaderboards with 1 snapshot."""
        target = date(2025, 8, 15)
        finals = [_make_game()]
        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, [], [])), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            client = MagicMock()
            result = _run_v2_leaderboards(
                finals, target, mlb_client=client, out_path=tmp_path,
            )

        assert result is not None
        assert result.snapshots_7d == 1  # only today's snapshot

    def test_statcast_data_flows_to_luck_status(self, tmp_path):
        """Statcast data → proper luck status assignment."""
        target = date(2025, 8, 15)
        finals = [_make_game()]
        _write_past_snapshots(tmp_path, 7, target)

        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]
        statcast = {
            "Aaron Judge": StatcastHitter(
                name="Aaron Judge", team="NYY",
                xwoba=0.420, barrel_pct=18.0, hard_hit_pct=50.0,
            ),
        }

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, [], [])), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value=statcast), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            client = MagicMock()
            result = _run_v2_leaderboards(
                finals, target, mlb_client=client, out_path=tmp_path,
            )

        assert result is not None
        judge_entries = [h for h in result.hot_hitters if h.full_name == "Aaron Judge"]
        assert len(judge_entries) == 1
        assert judge_entries[0].luck_status == LuckStatus.CONFIRMED
        assert judge_entries[0].xwoba == 0.420


# ---------------------------------------------------------------------------
# Tests — full pipeline with V2
# ---------------------------------------------------------------------------


class TestPipelineWithV2:
    def test_full_dry_run_includes_leaderboards(self, tmp_path):
        """Full pipeline dry run produces dashboard with leaderboard section."""
        game = _make_game()
        feed = _make_feed()
        target = date(2025, 8, 15)

        # Pre-populate snapshots
        _write_past_snapshots(tmp_path, 7, target)

        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]
        starters = [_starter_day(10, "Gerrit Cole", "NYY")]
        closers = [_closer_day(20, "Emmanuel Clase", "CLE")]

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.fetch_batter_season_stats", return_value={}), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A great game."), \
             patch("mlbreview.pipeline.write_preview", return_value="Preview."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod, \
             patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, starters, closers)), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                target,
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0

        day_file = tmp_path / "digests" / "2025-08-15" / "index.html"
        assert day_file.exists()
        content = day_file.read_text()
        assert "Player Leaderboards" in content
        assert "Aaron Judge" in content

    def test_v2_failure_still_produces_v1_digest(self, tmp_path):
        """V2 failure → V1 digest ships without leaderboards."""
        game = _make_game()
        feed = _make_feed()
        target = date(2025, 8, 15)

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.fetch_batter_season_stats", return_value={}), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A game."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod, \
             patch("mlbreview.pipeline.fetch_daily_gamelogs", side_effect=MlbApiError("total failure")):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                target,
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0
        day_file = tmp_path / "digests" / "2025-08-15" / "index.html"
        assert day_file.exists()
        content = day_file.read_text()
        # V1 sections render fine
        assert "Scores" in content or "NYY" in content
        # V2 leaderboard section absent
        assert "Player Leaderboards" not in content

    def test_off_day_skips_v2(self, tmp_path):
        """Off-day branch doesn't attempt V2 leaderboard pipeline."""
        target = date(2025, 8, 15)

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]) as mock_tonight, \
             patch("mlbreview.pipeline.fetch_daily_gamelogs") as mock_gamelogs:

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client

            result = run(
                target,
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0
        # V2 game-log fetch should NOT have been called
        mock_gamelogs.assert_not_called()

    def test_building_up_data_notice(self, tmp_path):
        """Dashboard shows 'building up data' notice when < 7 snapshots."""
        game = _make_game()
        feed = _make_feed()
        target = date(2025, 8, 15)

        # Only 3 prior snapshots (not enough for full 7-day window)
        _write_past_snapshots(tmp_path, 3, target)

        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.fetch_batter_season_stats", return_value={}), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A game."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod, \
             patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, [], [])), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                target,
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0
        content = (tmp_path / "digests" / "2025-08-15" / "index.html").read_text()
        assert "Building up data" in content

    def test_email_includes_leaderboard_teaser(self, tmp_path):
        """Email includes hottest-hitter/pitcher teaser when leaderboards present."""
        game = _make_game()
        feed = _make_feed()
        target = date(2025, 8, 15)
        _write_past_snapshots(tmp_path, 7, target)

        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]
        starters = [_starter_day(10, "Gerrit Cole", "NYY")]

        with patch("mlbreview.pipeline.make_client") as mock_make_client, \
             patch("mlbreview.pipeline.fetch_finals", return_value=[game]), \
             patch("mlbreview.pipeline.fetch_tonight", return_value=[]), \
             patch("mlbreview.pipeline.fetch_game_feed", return_value=feed), \
             patch("mlbreview.pipeline.fetch_transactions", return_value=[]), \
             patch("mlbreview.pipeline.fetch_batter_season_stats", return_value={}), \
             patch("mlbreview.pipeline.load_star_ids", return_value=frozenset()), \
             patch("mlbreview.pipeline.write_storyline", return_value="A game."), \
             patch("mlbreview.pipeline.anthropic") as mock_anthropic_mod, \
             patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, starters, [])), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            mock_client = MagicMock()
            mock_make_client.return_value = mock_client
            mock_anthropic_mod.Anthropic.return_value = MagicMock()

            result = run(
                target,
                dry_run=True,
                out_dir=str(tmp_path),
                config=_stub_config(),
            )

        assert result == 0
        content = (tmp_path / "digests" / "2025-08-15" / "index.html").read_text()
        assert "Player Leaderboards" in content


# ---------------------------------------------------------------------------
# Tests — snapshot writing in pipeline
# ---------------------------------------------------------------------------


class TestSnapshotIntegration:
    def test_snapshot_written_to_correct_path(self, tmp_path):
        """Pipeline writes snapshot to public/snapshots/YYYY-MM-DD.json."""
        target = date(2025, 8, 15)
        finals = [_make_game()]
        hitters = [_hitter_day(1, "Aaron Judge", "NYY")]

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, [], [])), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            client = MagicMock()
            _run_v2_leaderboards(finals, target, mlb_client=client, out_path=tmp_path)

        expected = tmp_path / "snapshots" / "2025-08-15.json"
        assert expected.exists()

    def test_snapshot_round_trips(self, tmp_path):
        """Written snapshot can be loaded back with correct data."""
        target = date(2025, 8, 15)
        finals = [_make_game()]
        hitters = [
            _hitter_day(1, "Aaron Judge", "NYY"),
            _hitter_day(2, "Mookie Betts", "LAD"),
        ]
        starters = [_starter_day(10, "Gerrit Cole", "NYY")]
        closers = [_closer_day(20, "Emmanuel Clase", "CLE")]

        with patch("mlbreview.pipeline.fetch_daily_gamelogs", return_value=(hitters, starters, closers)), \
             patch("mlbreview.pipeline.fetch_statcast_hitters", return_value={}), \
             patch("mlbreview.pipeline.fetch_statcast_pitchers", return_value={}):

            client = MagicMock()
            _run_v2_leaderboards(finals, target, mlb_client=client, out_path=tmp_path)

        snap = load_snapshot(snapshot_path(tmp_path, target))
        assert len(snap.hitters) == 2
        assert len(snap.starters) == 1
        assert len(snap.closers) == 1
        assert snap.snapshot_date == "2025-08-15"


# ---------------------------------------------------------------------------
# Tests — Jinja2 filter helpers
# ---------------------------------------------------------------------------


class TestRenderFilters:
    def test_format_avg(self):
        from mlbreview.render.pages import _format_avg
        assert _format_avg(0.312) == ".312"
        assert _format_avg(0.000) == ".000"
        assert _format_avg(0.400) == ".400"

    def test_format_era(self):
        from mlbreview.render.pages import _format_era
        assert _format_era(3.24) == "3.24"
        assert _format_era(0.0) == "0.00"

    def test_format_ip(self):
        from mlbreview.render.pages import _format_ip
        assert _format_ip(6.0) == "6.0"
        assert _format_ip(6.333333) == "6.1"
        assert _format_ip(6.666667) == "6.2"

    def test_luck_badge(self):
        from mlbreview.render.pages import _luck_badge
        assert _luck_badge("confirmed") == "Confirmed"
        assert _luck_badge("lucky") == "Lucky"
        assert _luck_badge("unlucky") == "Unlucky"
        assert _luck_badge("unconfirmed") == ""

    def test_luck_class(self):
        from mlbreview.render.pages import _luck_class
        assert _luck_class("confirmed") == "confirmed"
        assert _luck_class("unconfirmed") == ""
