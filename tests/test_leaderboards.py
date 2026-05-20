"""Tests for rolling-stat aggregation from daily snapshots."""

from __future__ import annotations

import pytest

from mlbreview.data.snapshots import (
    CloserDayStats,
    DailySnapshot,
    HitterDayStats,
    StarterGameStats,
)
from mlbreview.scoring.leaderboards import (
    RollingCloserStats,
    RollingHitterStats,
    RollingStarterStats,
    RollingStats,
    compute_rolling_stats,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _hitter(
    player_id: int = 1,
    full_name: str = "Test Hitter",
    team_abbr: str = "TST",
    plate_appearances: int = 4,
    at_bats: int = 3,
    hits: int = 1,
    doubles: int = 0,
    triples: int = 0,
    home_runs: int = 0,
    rbi: int = 0,
    stolen_bases: int = 0,
    walks: int = 1,
    strikeouts: int = 1,
) -> HitterDayStats:
    return HitterDayStats(
        player_id=player_id, full_name=full_name, team_abbr=team_abbr,
        plate_appearances=plate_appearances, at_bats=at_bats, hits=hits,
        doubles=doubles, triples=triples, home_runs=home_runs,
        rbi=rbi, stolen_bases=stolen_bases, walks=walks, strikeouts=strikeouts,
    )


def _starter(
    player_id: int = 100,
    full_name: str = "Test Starter",
    team_abbr: str = "TST",
    game_date: str = "2026-05-18",
    opponent_abbr: str = "OPP",
    outs_recorded: int = 18,
    hits_allowed: int = 5,
    earned_runs: int = 2,
    walks: int = 2,
    strikeouts: int = 7,
    home_runs_allowed: int = 1,
    pitches_thrown: int = 95,
) -> StarterGameStats:
    return StarterGameStats(
        player_id=player_id, full_name=full_name, team_abbr=team_abbr,
        game_date=game_date, opponent_abbr=opponent_abbr,
        outs_recorded=outs_recorded, hits_allowed=hits_allowed,
        earned_runs=earned_runs, walks=walks, strikeouts=strikeouts,
        home_runs_allowed=home_runs_allowed, pitches_thrown=pitches_thrown,
    )


def _closer(
    player_id: int = 200,
    full_name: str = "Test Closer",
    team_abbr: str = "TST",
    outs_recorded: int = 3,
    earned_runs: int = 0,
    saves: int = 1,
    blown_saves: int = 0,
    holds: int = 0,
    strikeouts: int = 1,
    walks: int = 0,
) -> CloserDayStats:
    return CloserDayStats(
        player_id=player_id, full_name=full_name, team_abbr=team_abbr,
        outs_recorded=outs_recorded, earned_runs=earned_runs,
        saves=saves, blown_saves=blown_saves, holds=holds,
        strikeouts=strikeouts, walks=walks,
    )


def _snapshot(
    snapshot_date: str = "2026-05-18",
    hitters: tuple[HitterDayStats, ...] = (),
    starters: tuple[StarterGameStats, ...] = (),
    closers: tuple[CloserDayStats, ...] = (),
) -> DailySnapshot:
    return DailySnapshot(
        snapshot_date=snapshot_date,
        hitters=hitters,
        starters=starters,
        closers=closers,
    )


# ---------------------------------------------------------------------------
# Dataclass property tests
# ---------------------------------------------------------------------------


class TestRollingHitterProperties:
    def test_avg_normal(self):
        h = RollingHitterStats(
            player_id=1, full_name="Test", team_abbr="TST",
            games=3, plate_appearances=12, at_bats=10,
            hits=3, doubles=1, triples=0, home_runs=1,
            rbi=2, stolen_bases=0, walks=2, strikeouts=3,
        )
        assert h.avg == pytest.approx(0.300)

    def test_avg_zero_at_bats(self):
        h = RollingHitterStats(
            player_id=1, full_name="Test", team_abbr="TST",
            games=1, plate_appearances=2, at_bats=0,
            hits=0, doubles=0, triples=0, home_runs=0,
            rbi=0, stolen_bases=0, walks=2, strikeouts=0,
        )
        assert h.avg == 0.0

    def test_obp(self):
        h = RollingHitterStats(
            player_id=1, full_name="Test", team_abbr="TST",
            games=3, plate_appearances=20, at_bats=16,
            hits=5, doubles=1, triples=0, home_runs=1,
            rbi=3, stolen_bases=0, walks=4, strikeouts=3,
        )
        # OBP = (5 + 4) / 20 = 0.450
        assert h.obp == pytest.approx(0.450)

    def test_slg(self):
        h = RollingHitterStats(
            player_id=1, full_name="Test", team_abbr="TST",
            games=3, plate_appearances=12, at_bats=10,
            hits=4, doubles=1, triples=1, home_runs=1,
            rbi=3, stolen_bases=0, walks=2, strikeouts=2,
        )
        # singles = 4 - 1 - 1 - 1 = 1
        # TB = 1 + 2*1 + 3*1 + 4*1 = 10
        # SLG = 10/10 = 1.000
        assert h.slg == pytest.approx(1.000)

    def test_slg_zero_at_bats(self):
        h = RollingHitterStats(
            player_id=1, full_name="Test", team_abbr="TST",
            games=1, plate_appearances=1, at_bats=0,
            hits=0, doubles=0, triples=0, home_runs=0,
            rbi=0, stolen_bases=0, walks=1, strikeouts=0,
        )
        assert h.slg == 0.0


class TestRollingStarterProperties:
    def test_innings_pitched(self):
        s = RollingStarterStats(
            player_id=100, full_name="Test", team_abbr="TST",
            starts=2, outs_recorded=36, hits_allowed=10,
            earned_runs=4, walks=3, strikeouts=14,
            home_runs_allowed=2, pitches_thrown=190,
        )
        assert s.innings_pitched == pytest.approx(12.0)

    def test_era(self):
        s = RollingStarterStats(
            player_id=100, full_name="Test", team_abbr="TST",
            starts=2, outs_recorded=36, hits_allowed=10,
            earned_runs=4, walks=3, strikeouts=14,
            home_runs_allowed=2, pitches_thrown=190,
        )
        # ERA = (4 * 9) / 12.0 = 3.00
        assert s.era == pytest.approx(3.0)

    def test_era_zero_ip(self):
        s = RollingStarterStats(
            player_id=100, full_name="Test", team_abbr="TST",
            starts=1, outs_recorded=0, hits_allowed=3,
            earned_runs=5, walks=2, strikeouts=0,
            home_runs_allowed=1, pitches_thrown=20,
        )
        assert s.era == 0.0

    def test_whip(self):
        s = RollingStarterStats(
            player_id=100, full_name="Test", team_abbr="TST",
            starts=1, outs_recorded=18, hits_allowed=5,
            earned_runs=2, walks=2, strikeouts=7,
            home_runs_allowed=1, pitches_thrown=95,
        )
        # WHIP = (2 + 5) / 6.0 = 1.167
        assert s.whip == pytest.approx(7 / 6)

    def test_k_per_9(self):
        s = RollingStarterStats(
            player_id=100, full_name="Test", team_abbr="TST",
            starts=1, outs_recorded=18, hits_allowed=5,
            earned_runs=2, walks=2, strikeouts=9,
            home_runs_allowed=1, pitches_thrown=95,
        )
        # K/9 = (9 * 9) / 6.0 = 13.5
        assert s.k_per_9 == pytest.approx(13.5)


class TestRollingCloserProperties:
    def test_era(self):
        c = RollingCloserStats(
            player_id=200, full_name="Test", team_abbr="TST",
            appearances=5, outs_recorded=15, earned_runs=1,
            saves=4, blown_saves=1, holds=0,
            strikeouts=6, walks=2,
        )
        # ERA = (1 * 9) / 5.0 = 1.80
        assert c.era == pytest.approx(1.8)

    def test_save_pct(self):
        c = RollingCloserStats(
            player_id=200, full_name="Test", team_abbr="TST",
            appearances=5, outs_recorded=15, earned_runs=1,
            saves=4, blown_saves=1, holds=0,
            strikeouts=6, walks=2,
        )
        # SV% = 4 / 5 = 0.800
        assert c.save_pct == pytest.approx(0.8)

    def test_save_pct_no_opportunities(self):
        c = RollingCloserStats(
            player_id=200, full_name="Test", team_abbr="TST",
            appearances=2, outs_recorded=6, earned_runs=0,
            saves=0, blown_saves=0, holds=2,
            strikeouts=3, walks=0,
        )
        assert c.save_pct == 0.0


class TestDataclassesFrozen:
    def test_rolling_hitter_is_frozen(self):
        h = RollingHitterStats(
            player_id=1, full_name="Test", team_abbr="TST",
            games=1, plate_appearances=4, at_bats=3,
            hits=1, doubles=0, triples=0, home_runs=0,
            rbi=0, stolen_bases=0, walks=1, strikeouts=1,
        )
        with pytest.raises(AttributeError):
            h.hits = 99  # type: ignore[misc]

    def test_rolling_starter_is_frozen(self):
        s = RollingStarterStats(
            player_id=100, full_name="Test", team_abbr="TST",
            starts=1, outs_recorded=18, hits_allowed=5,
            earned_runs=2, walks=2, strikeouts=7,
            home_runs_allowed=1, pitches_thrown=95,
        )
        with pytest.raises(AttributeError):
            s.earned_runs = 99  # type: ignore[misc]

    def test_rolling_closer_is_frozen(self):
        c = RollingCloserStats(
            player_id=200, full_name="Test", team_abbr="TST",
            appearances=1, outs_recorded=3, earned_runs=0,
            saves=1, blown_saves=0, holds=0,
            strikeouts=1, walks=0,
        )
        with pytest.raises(AttributeError):
            c.saves = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute_rolling_stats — empty / missing data
# ---------------------------------------------------------------------------


class TestComputeRollingStatsEmpty:
    def test_empty_snapshot_list(self):
        result = compute_rolling_stats([])
        assert result.snapshots_used == 0
        assert result.snapshots_used == 0
        assert result.hitters == {}
        assert result.starters == {}
        assert result.closers == {}

    def test_snapshots_with_no_players(self):
        snaps = [
            _snapshot("2026-05-18"),
            _snapshot("2026-05-19"),
        ]
        result = compute_rolling_stats(snaps)
        assert result.snapshots_used == 2
        assert result.hitters == {}
        assert result.starters == {}
        assert result.closers == {}


# ---------------------------------------------------------------------------
# compute_rolling_stats — hitter aggregation
# ---------------------------------------------------------------------------


class TestHitterAggregation:
    def _multi_day_snapshots(self) -> list[DailySnapshot]:
        """Three days of data for two hitters."""
        return [
            _snapshot("2026-05-18", hitters=(
                _hitter(1, "Aaron Judge", "NYY", plate_appearances=5, at_bats=4,
                        hits=2, doubles=1, triples=0, home_runs=1, rbi=3,
                        stolen_bases=0, walks=1, strikeouts=1),
                _hitter(2, "Shohei Ohtani", "LAD", plate_appearances=4, at_bats=3,
                        hits=1, doubles=0, triples=0, home_runs=0, rbi=0,
                        stolen_bases=1, walks=1, strikeouts=1),
            )),
            _snapshot("2026-05-19", hitters=(
                _hitter(1, "Aaron Judge", "NYY", plate_appearances=4, at_bats=3,
                        hits=1, doubles=0, triples=0, home_runs=0, rbi=1,
                        stolen_bases=0, walks=1, strikeouts=0),
                _hitter(2, "Shohei Ohtani", "LAD", plate_appearances=5, at_bats=4,
                        hits=3, doubles=1, triples=0, home_runs=1, rbi=4,
                        stolen_bases=0, walks=1, strikeouts=0),
            )),
            _snapshot("2026-05-20", hitters=(
                _hitter(1, "Aaron Judge", "NYY", plate_appearances=4, at_bats=4,
                        hits=2, doubles=0, triples=0, home_runs=1, rbi=2,
                        stolen_bases=0, walks=0, strikeouts=1),
                _hitter(2, "Shohei Ohtani", "LAD", plate_appearances=4, at_bats=3,
                        hits=2, doubles=0, triples=1, home_runs=0, rbi=1,
                        stolen_bases=1, walks=1, strikeouts=0),
            )),
        ]

    def test_sums_counting_stats(self):
        result = compute_rolling_stats(self._multi_day_snapshots(), min_pa=0)
        judge = result.hitters[1]
        assert judge.full_name == "Aaron Judge"
        assert judge.games == 3
        assert judge.plate_appearances == 13
        assert judge.at_bats == 11
        assert judge.hits == 5
        assert judge.doubles == 1
        assert judge.home_runs == 2
        assert judge.rbi == 6
        assert judge.walks == 2
        assert judge.strikeouts == 2

    def test_computed_avg(self):
        result = compute_rolling_stats(self._multi_day_snapshots(), min_pa=0)
        judge = result.hitters[1]
        # AVG = 5/11 ≈ 0.4545
        assert judge.avg == pytest.approx(5 / 11)

    def test_computed_obp(self):
        result = compute_rolling_stats(self._multi_day_snapshots(), min_pa=0)
        ohtani = result.hitters[2]
        # H=6, BB=3, PA=13 → OBP = 9/13 ≈ 0.692
        assert ohtani.obp == pytest.approx(9 / 13)

    def test_computed_slg(self):
        result = compute_rolling_stats(self._multi_day_snapshots(), min_pa=0)
        ohtani = result.hitters[2]
        # hits=6, 2B=1, 3B=1, HR=1 → singles=3
        # TB = 3 + 2*1 + 3*1 + 4*1 = 12
        # AB = 10 → SLG = 12/10 = 1.200
        assert ohtani.slg == pytest.approx(12 / 10)

    def test_qualification_filter(self):
        result = compute_rolling_stats(self._multi_day_snapshots(), min_pa=14)
        # Judge has 13 PA, Ohtani has 13 PA — both below 14
        assert result.hitters == {}

    def test_qualification_passes(self):
        result = compute_rolling_stats(self._multi_day_snapshots(), min_pa=13)
        assert len(result.hitters) == 2

    def test_single_day_single_player(self):
        snaps = [_snapshot("2026-05-18", hitters=(
            _hitter(1, "Solo", "TST", plate_appearances=4, at_bats=3,
                    hits=2, doubles=1, triples=0, home_runs=0, rbi=1,
                    stolen_bases=0, walks=1, strikeouts=0),
        ))]
        result = compute_rolling_stats(snaps, min_pa=0)
        assert len(result.hitters) == 1
        assert result.hitters[1].games == 1
        assert result.hitters[1].hits == 2

    def test_player_not_in_every_snapshot(self):
        """Player who only appears in some snapshots."""
        snaps = [
            _snapshot("2026-05-18", hitters=(
                _hitter(1, "Everyday", "TST", plate_appearances=4, at_bats=3,
                        hits=1, doubles=0, triples=0, home_runs=0, rbi=0,
                        stolen_bases=0, walks=1, strikeouts=1),
                _hitter(2, "Occasional", "TST", plate_appearances=3, at_bats=3,
                        hits=2, doubles=0, triples=0, home_runs=1, rbi=2,
                        stolen_bases=0, walks=0, strikeouts=1),
            )),
            _snapshot("2026-05-19", hitters=(
                _hitter(1, "Everyday", "TST", plate_appearances=4, at_bats=3,
                        hits=2, doubles=0, triples=0, home_runs=0, rbi=0,
                        stolen_bases=0, walks=1, strikeouts=0),
                # Player 2 did not play this day
            )),
        ]
        result = compute_rolling_stats(snaps, min_pa=0)
        assert result.hitters[1].games == 2
        assert result.hitters[2].games == 1
        assert result.hitters[2].home_runs == 1

    def test_team_affiliation_uses_most_recent(self):
        """If a player gets traded mid-window, use the latest team.

        Snapshots are ordered newest-first (matching load_snapshots),
        so the first snapshot's team should win.
        """
        snaps = [
            # Newest first — player is now on LAD
            _snapshot("2026-05-19", hitters=(
                _hitter(1, "Traded", "LAD", plate_appearances=4, at_bats=3,
                        hits=2, doubles=0, triples=0, home_runs=0, rbi=0,
                        stolen_bases=0, walks=1, strikeouts=0),
            )),
            # Older — player was on NYY
            _snapshot("2026-05-18", hitters=(
                _hitter(1, "Traded", "NYY", plate_appearances=4, at_bats=3,
                        hits=1, doubles=0, triples=0, home_runs=0, rbi=0,
                        stolen_bases=0, walks=1, strikeouts=1),
            )),
        ]
        result = compute_rolling_stats(snaps, min_pa=0)
        assert result.hitters[1].team_abbr == "LAD"


# ---------------------------------------------------------------------------
# compute_rolling_stats — starter aggregation
# ---------------------------------------------------------------------------


class TestStarterAggregation:
    def _multi_start_snapshots(self) -> list[DailySnapshot]:
        return [
            _snapshot("2026-05-15", starters=(
                _starter(100, "Gerrit Cole", "NYY", game_date="2026-05-15",
                         outs_recorded=21, hits_allowed=4, earned_runs=1,
                         walks=1, strikeouts=9, home_runs_allowed=0, pitches_thrown=100),
            )),
            _snapshot("2026-05-20", starters=(
                _starter(100, "Gerrit Cole", "NYY", game_date="2026-05-20",
                         outs_recorded=18, hits_allowed=6, earned_runs=3,
                         walks=3, strikeouts=7, home_runs_allowed=1, pitches_thrown=105),
            )),
        ]

    def test_sums_counting_stats(self):
        result = compute_rolling_stats(self._multi_start_snapshots(), min_ip=0.0)
        cole = result.starters[100]
        assert cole.starts == 2
        assert cole.outs_recorded == 39
        assert cole.hits_allowed == 10
        assert cole.earned_runs == 4
        assert cole.walks == 4
        assert cole.strikeouts == 16
        assert cole.home_runs_allowed == 1
        assert cole.pitches_thrown == 205

    def test_computed_era(self):
        result = compute_rolling_stats(self._multi_start_snapshots(), min_ip=0.0)
        cole = result.starters[100]
        # ERA = (4 * 9) / (39/3) = 36 / 13 ≈ 2.769
        assert cole.era == pytest.approx(36 / 13)

    def test_computed_whip(self):
        result = compute_rolling_stats(self._multi_start_snapshots(), min_ip=0.0)
        cole = result.starters[100]
        # WHIP = (4 + 10) / (39/3) = 14 / 13 ≈ 1.077
        assert cole.whip == pytest.approx(14 / 13)

    def test_computed_k_per_9(self):
        result = compute_rolling_stats(self._multi_start_snapshots(), min_ip=0.0)
        cole = result.starters[100]
        # K/9 = (16 * 9) / (39/3) = 144 / 13 ≈ 11.077
        assert cole.k_per_9 == pytest.approx(144 / 13)

    def test_qualification_filter(self):
        result = compute_rolling_stats(self._multi_start_snapshots(), min_ip=14.0)
        # Cole has 13.0 IP → filtered out
        assert result.starters == {}

    def test_qualification_passes(self):
        result = compute_rolling_stats(self._multi_start_snapshots(), min_ip=13.0)
        assert 100 in result.starters


# ---------------------------------------------------------------------------
# compute_rolling_stats — closer aggregation
# ---------------------------------------------------------------------------


class TestCloserAggregation:
    def _multi_day_closer_snapshots(self) -> list[DailySnapshot]:
        return [
            _snapshot("2026-05-18", closers=(
                _closer(200, "Emmanuel Clase", "CLE",
                        outs_recorded=3, earned_runs=0, saves=1,
                        blown_saves=0, holds=0, strikeouts=2, walks=0),
            )),
            _snapshot("2026-05-19", closers=(
                _closer(200, "Emmanuel Clase", "CLE",
                        outs_recorded=3, earned_runs=1, saves=0,
                        blown_saves=1, holds=0, strikeouts=1, walks=1),
            )),
            _snapshot("2026-05-20", closers=(
                _closer(200, "Emmanuel Clase", "CLE",
                        outs_recorded=3, earned_runs=0, saves=1,
                        blown_saves=0, holds=0, strikeouts=1, walks=0),
            )),
        ]

    def test_sums_counting_stats(self):
        result = compute_rolling_stats(self._multi_day_closer_snapshots(), min_sv_opp=0)
        clase = result.closers[200]
        assert clase.appearances == 3
        assert clase.outs_recorded == 9
        assert clase.earned_runs == 1
        assert clase.saves == 2
        assert clase.blown_saves == 1
        assert clase.strikeouts == 4
        assert clase.walks == 1

    def test_computed_era(self):
        result = compute_rolling_stats(self._multi_day_closer_snapshots(), min_sv_opp=0)
        clase = result.closers[200]
        # ERA = (1 * 9) / (9/3) = 9 / 3 = 3.00
        assert clase.era == pytest.approx(3.0)

    def test_computed_save_pct(self):
        result = compute_rolling_stats(self._multi_day_closer_snapshots(), min_sv_opp=0)
        clase = result.closers[200]
        # SV% = 2 / (2+1) = 0.667
        assert clase.save_pct == pytest.approx(2 / 3)

    def test_qualification_filter(self):
        result = compute_rolling_stats(self._multi_day_closer_snapshots(), min_sv_opp=4)
        # Clase has 3 save opportunities → filtered out
        assert result.closers == {}

    def test_qualification_passes(self):
        result = compute_rolling_stats(self._multi_day_closer_snapshots(), min_sv_opp=3)
        assert 200 in result.closers


# ---------------------------------------------------------------------------
# Mixed snapshots — all three player types
# ---------------------------------------------------------------------------


class TestMixedAggregation:
    def test_all_three_types(self):
        snaps = [
            _snapshot("2026-05-18",
                      hitters=(_hitter(1, "Hitter A", "NYY", plate_appearances=4, at_bats=3, hits=1),),
                      starters=(_starter(100, "Starter A", "NYY"),),
                      closers=(_closer(200, "Closer A", "NYY"),)),
            _snapshot("2026-05-19",
                      hitters=(_hitter(1, "Hitter A", "NYY", plate_appearances=5, at_bats=4, hits=2),),
                      starters=(),
                      closers=(_closer(200, "Closer A", "NYY"),)),
        ]
        result = compute_rolling_stats(snaps, min_pa=0, min_ip=0.0, min_sv_opp=0)
        assert len(result.hitters) == 1
        assert result.hitters[1].plate_appearances == 9
        assert len(result.starters) == 1
        assert result.starters[100].starts == 1
        assert len(result.closers) == 1
        assert result.closers[200].appearances == 2

    def test_snapshots_used_count(self):
        snaps = [_snapshot(f"2026-05-{d:02d}") for d in range(15, 22)]
        result = compute_rolling_stats(snaps)
        assert result.snapshots_used == 7

    def test_default_qualification_thresholds(self):
        """Verify that default thresholds from config are applied."""
        # Create a player barely below the default MIN_PA_HITTER=15
        snaps = [
            _snapshot("2026-05-18", hitters=(
                _hitter(1, "Low PA", "TST", plate_appearances=7, at_bats=6, hits=2),
            )),
            _snapshot("2026-05-19", hitters=(
                _hitter(1, "Low PA", "TST", plate_appearances=7, at_bats=6, hits=3),
            )),
        ]
        # Total PA = 14, which is below the default MIN_PA_HITTER = 15
        result = compute_rolling_stats(snaps)
        assert 1 not in result.hitters

    def test_exactly_at_threshold(self):
        """Player exactly at the PA threshold should qualify."""
        snaps = [
            _snapshot("2026-05-18", hitters=(
                _hitter(1, "Exactly 15 PA", "TST", plate_appearances=8, at_bats=6, hits=2),
            )),
            _snapshot("2026-05-19", hitters=(
                _hitter(1, "Exactly 15 PA", "TST", plate_appearances=7, at_bats=6, hits=3),
            )),
        ]
        # Total PA = 15, exactly at MIN_PA_HITTER = 15
        result = compute_rolling_stats(snaps)
        assert 1 in result.hitters


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_single_snapshot(self):
        snaps = [_snapshot("2026-05-18", hitters=(
            _hitter(1, "Solo", "TST", plate_appearances=5, at_bats=4, hits=3),
        ))]
        result = compute_rolling_stats(snaps, min_pa=0)
        assert result.snapshots_used == 1
        assert result.hitters[1].avg == pytest.approx(0.75)

    def test_many_players_across_snapshots(self):
        """10 players across 7 days."""
        snaps = []
        for day in range(7):
            hitters = tuple(
                _hitter(
                    player_id=i,
                    full_name=f"Player {i}",
                    plate_appearances=4,
                    at_bats=3,
                    hits=1 if day % 2 == 0 else 2,
                )
                for i in range(10)
            )
            snaps.append(_snapshot(f"2026-05-{15 + day:02d}", hitters=hitters))

        result = compute_rolling_stats(snaps, min_pa=0)
        assert len(result.hitters) == 10
        # Each player: 7 games, 28 PA, 21 AB
        for pid in range(10):
            assert result.hitters[pid].games == 7
            assert result.hitters[pid].plate_appearances == 28

    def test_pitcher_zero_outs(self):
        """Pitcher with 0 outs recorded — rate stats should be 0.0, not crash."""
        snaps = [_snapshot("2026-05-18", starters=(
            _starter(100, "Bad Start", "TST", outs_recorded=0,
                     hits_allowed=5, earned_runs=5, walks=3,
                     strikeouts=0, home_runs_allowed=2, pitches_thrown=30),
        ))]
        result = compute_rolling_stats(snaps, min_ip=0.0)
        starter = result.starters[100]
        assert starter.era == 0.0
        assert starter.whip == 0.0
        assert starter.k_per_9 == 0.0

    def test_duplicate_player_same_snapshot(self):
        """If the same player appears twice in one snapshot (shouldn't happen,
        but defensive), both entries get summed."""
        snaps = [_snapshot("2026-05-18", hitters=(
            _hitter(1, "Double Entry", "TST", plate_appearances=3, at_bats=3, hits=1),
            _hitter(1, "Double Entry", "TST", plate_appearances=3, at_bats=3, hits=2),
        ))]
        result = compute_rolling_stats(snaps, min_pa=0)
        h = result.hitters[1]
        assert h.plate_appearances == 6
        assert h.hits == 3
        # games count increments per entry
        assert h.games == 2
