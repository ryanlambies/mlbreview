"""Tests for leaderboard scoring, luck filter, and breakout detection (U4)."""

from __future__ import annotations

import pytest

from mlbreview.config import (
    COLD_HITTER_COMPOSITE_MAX,
    COLD_PITCHER_COMPOSITE_MAX,
)
from mlbreview.data.snapshots import (
    CloserDayStats,
    DailySnapshot,
    HitterDayStats,
    StarterGameStats,
)
from mlbreview.data.statcast import StatcastHitter, StatcastPitcher
from mlbreview.scoring.leaderboards import (
    LeaderboardHitter,
    LeaderboardPitcher,
    Leaderboards,
    LuckStatus,
    RollingCloserStats,
    RollingHitterStats,
    RollingStarterStats,
    RollingStats,
    _closer_composite,
    _hitter_composite,
    _hitter_luck,
    _pitcher_luck,
    _starter_composite,
    compute_rolling_stats,
    score_leaderboards,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _rolling_hitter(
    player_id: int = 1,
    full_name: str = "Test Hitter",
    team_abbr: str = "TST",
    games: int = 5,
    plate_appearances: int = 20,
    at_bats: int = 18,
    hits: int = 6,
    doubles: int = 1,
    triples: int = 0,
    home_runs: int = 2,
    rbi: int = 5,
    stolen_bases: int = 1,
    walks: int = 2,
    strikeouts: int = 4,
) -> RollingHitterStats:
    return RollingHitterStats(
        player_id=player_id, full_name=full_name, team_abbr=team_abbr,
        games=games, plate_appearances=plate_appearances, at_bats=at_bats,
        hits=hits, doubles=doubles, triples=triples, home_runs=home_runs,
        rbi=rbi, stolen_bases=stolen_bases, walks=walks, strikeouts=strikeouts,
    )


def _rolling_starter(
    player_id: int = 100,
    full_name: str = "Test Starter",
    team_abbr: str = "TST",
    starts: int = 2,
    outs_recorded: int = 36,
    hits_allowed: int = 10,
    earned_runs: int = 4,
    walks: int = 3,
    strikeouts: int = 14,
    home_runs_allowed: int = 2,
    pitches_thrown: int = 190,
) -> RollingStarterStats:
    return RollingStarterStats(
        player_id=player_id, full_name=full_name, team_abbr=team_abbr,
        starts=starts, outs_recorded=outs_recorded, hits_allowed=hits_allowed,
        earned_runs=earned_runs, walks=walks, strikeouts=strikeouts,
        home_runs_allowed=home_runs_allowed, pitches_thrown=pitches_thrown,
    )


def _rolling_closer(
    player_id: int = 200,
    full_name: str = "Test Closer",
    team_abbr: str = "TST",
    appearances: int = 5,
    outs_recorded: int = 15,
    earned_runs: int = 1,
    saves: int = 4,
    blown_saves: int = 1,
    holds: int = 0,
    strikeouts: int = 6,
    walks: int = 2,
) -> RollingCloserStats:
    return RollingCloserStats(
        player_id=player_id, full_name=full_name, team_abbr=team_abbr,
        appearances=appearances, outs_recorded=outs_recorded,
        earned_runs=earned_runs, saves=saves, blown_saves=blown_saves,
        holds=holds, strikeouts=strikeouts, walks=walks,
    )


def _rolling_stats(
    snapshots_used: int = 7,
    hitters: dict[int, RollingHitterStats] | None = None,
    starters: dict[int, RollingStarterStats] | None = None,
    closers: dict[int, RollingCloserStats] | None = None,
) -> RollingStats:
    return RollingStats(
        snapshots_used=snapshots_used,
        hitters=hitters or {},
        starters=starters or {},
        closers=closers or {},
    )


def _statcast_hitter(
    name: str = "Test Hitter",
    team: str = "TST",
    xwoba: float = 0.340,
    barrel_pct: float = 8.5,
    hard_hit_pct: float = 42.0,
) -> StatcastHitter:
    return StatcastHitter(
        name=name, team=team, xwoba=xwoba,
        barrel_pct=barrel_pct, hard_hit_pct=hard_hit_pct,
    )


def _statcast_pitcher(
    name: str = "Test Starter",
    team: str = "TST",
    fip: float = 3.50,
    xfip: float = 3.60,
    xera: float = 3.40,
    barrel_pct: float = 6.0,
    hard_hit_pct: float = 35.0,
) -> StatcastPitcher:
    return StatcastPitcher(
        name=name, team=team, fip=fip, xfip=xfip, xera=xera,
        barrel_pct=barrel_pct, hard_hit_pct=hard_hit_pct,
    )


# ===========================================================================
# Composite scoring
# ===========================================================================


class TestHitterComposite:
    def test_zero_stats(self):
        h = _rolling_hitter(at_bats=0, hits=0, home_runs=0, rbi=0)
        assert _hitter_composite(h) == 0.0

    def test_ceiling_values(self):
        """A hitter at all ceilings scores 1.0."""
        h = _rolling_hitter(at_bats=10, hits=5, home_runs=5, rbi=12)
        # AVG = 0.500 (ceiling), HR = 5 (ceiling), RBI = 12 (ceiling)
        assert _hitter_composite(h) == pytest.approx(1.0)

    def test_above_ceiling_clamped(self):
        """Stats above ceiling are clamped to 1.0 per component."""
        h = _rolling_hitter(at_bats=10, hits=8, home_runs=10, rbi=20)
        # AVG=0.800 > ceiling, HR=10 > ceiling, RBI=20 > ceiling
        assert _hitter_composite(h) == pytest.approx(1.0)

    def test_moderate_stats(self):
        h = _rolling_hitter(at_bats=20, hits=6, home_runs=2, rbi=5)
        # AVG = 0.300 → 0.300/0.500 = 0.600
        # HR = 2 → 2/5 = 0.400
        # RBI = 5 → 5/12 = 0.4167
        expected = 0.40 * 0.600 + 0.30 * 0.400 + 0.30 * (5 / 12)
        assert _hitter_composite(h) == pytest.approx(expected)

    def test_ordering_reflects_performance(self):
        hot = _rolling_hitter(player_id=1, at_bats=20, hits=8, home_runs=3, rbi=8)
        cold = _rolling_hitter(player_id=2, at_bats=20, hits=3, home_runs=0, rbi=1)
        assert _hitter_composite(hot) > _hitter_composite(cold)


class TestStarterComposite:
    def test_perfect_starter(self):
        """ERA=0, K/9=15+, WHIP=0 → score = 1.0."""
        s = _rolling_starter(outs_recorded=27, earned_runs=0, strikeouts=15,
                             hits_allowed=0, walks=0)
        # ERA = 0 → 1 - 0/6 = 1.0
        # K/9 = (15*9)/9 = 15.0 → 15/15 = 1.0
        # WHIP = 0/9 = 0 → 1 - 0/2 = 1.0
        assert _starter_composite(s) == pytest.approx(1.0)

    def test_terrible_starter(self):
        """ERA >= ceiling, K/9 = 0, WHIP >= ceiling → score = 0."""
        s = _rolling_starter(outs_recorded=9, earned_runs=6, strikeouts=0,
                             hits_allowed=5, walks=1)
        # ERA = (6*9)/3 = 18.0 → 1 - 18/6 = clamped to 0
        # K/9 = 0
        # WHIP = (1+5)/3 = 2.0 → 1 - 2/2 = 0
        assert _starter_composite(s) == pytest.approx(0.0)

    def test_moderate_starter(self):
        s = _rolling_starter(outs_recorded=36, earned_runs=4, strikeouts=14,
                             hits_allowed=10, walks=3)
        ip = 12.0
        era = (4 * 9) / ip  # 3.0
        k9 = (14 * 9) / ip  # 10.5
        whip = (3 + 10) / ip  # 1.083
        norm_era = 1.0 - era / 6.0
        norm_k9 = k9 / 15.0
        norm_whip = 1.0 - whip / 2.0
        expected = 0.40 * norm_era + 0.35 * norm_k9 + 0.25 * norm_whip
        assert _starter_composite(s) == pytest.approx(expected)

    def test_zero_ip_does_not_crash(self):
        """Zero IP produces a non-zero composite due to ERA/WHIP division guards.

        ERA and WHIP return 0.0 when IP=0, which normalizes to maximum
        "good" values (norm_era=1.0, norm_whip=1.0).  In practice the
        qualification filter (MIN_IP_PITCHER=7.0) prevents this case
        from reaching leaderboards.
        """
        s = _rolling_starter(outs_recorded=0, earned_runs=5, strikeouts=0,
                             hits_allowed=3, walks=2)
        composite = _starter_composite(s)
        # 0.40 * 1.0 + 0.35 * 0.0 + 0.25 * 1.0 = 0.65 (quirk, see docstring)
        assert composite == pytest.approx(0.65)


class TestCloserComposite:
    def test_perfect_closer(self):
        """ERA=0, SV%=100%, high K/9 → score ≈ 1.0."""
        c = _rolling_closer(outs_recorded=15, earned_runs=0, saves=5,
                            blown_saves=0, strikeouts=10)
        # ERA = 0 → 1 - 0/6 = 1.0
        # SV% = 5/5 = 1.0
        # IP = 5, K/9 = (10*9)/5 = 18.0 → 18/15 clamped to 1.0
        assert _closer_composite(c) == pytest.approx(1.0)

    def test_terrible_closer(self):
        """ERA >= ceiling, SV%=0, K/9=0 → score = 0."""
        c = _rolling_closer(outs_recorded=3, earned_runs=3, saves=0,
                            blown_saves=3, strikeouts=0)
        # ERA = (3*9)/1 = 27.0 → clamped to 0
        # SV% = 0/3 = 0
        # K/9 = 0
        assert _closer_composite(c) == pytest.approx(0.0)

    def test_moderate_closer(self):
        c = _rolling_closer(outs_recorded=15, earned_runs=1, saves=4,
                            blown_saves=1, strikeouts=6)
        ip = 5.0
        era = (1 * 9) / ip  # 1.8
        sv_pct = 4 / 5  # 0.8
        k9 = (6 * 9) / ip  # 10.8
        norm_era = 1.0 - era / 6.0
        norm_k9 = min(k9 / 15.0, 1.0)
        expected = 0.35 * norm_era + 0.40 * sv_pct + 0.25 * norm_k9
        assert _closer_composite(c) == pytest.approx(expected)

    def test_zero_ip_closer(self):
        c = _rolling_closer(outs_recorded=0, earned_runs=0, saves=1,
                            blown_saves=0, strikeouts=0)
        composite = _closer_composite(c)
        assert composite >= 0.0  # doesn't crash


class TestCompositeOrdering:
    """Verify that composites rank players in the expected order."""

    def test_hot_hitter_beats_cold(self):
        hot = _rolling_hitter(at_bats=20, hits=9, home_runs=3, rbi=8)
        cold = _rolling_hitter(at_bats=20, hits=2, home_runs=0, rbi=1)
        assert _hitter_composite(hot) > _hitter_composite(cold)

    def test_ace_beats_journeyman(self):
        ace = _rolling_starter(outs_recorded=42, earned_runs=2, strikeouts=20,
                               hits_allowed=7, walks=2)
        jrn = _rolling_starter(outs_recorded=30, earned_runs=8, strikeouts=6,
                               hits_allowed=14, walks=5)
        assert _starter_composite(ace) > _starter_composite(jrn)

    def test_dominant_closer_beats_shaky(self):
        dom = _rolling_closer(outs_recorded=15, earned_runs=0, saves=5,
                              blown_saves=0, strikeouts=8)
        shk = _rolling_closer(outs_recorded=12, earned_runs=4, saves=1,
                              blown_saves=3, strikeouts=2)
        assert _closer_composite(dom) > _closer_composite(shk)


# ===========================================================================
# Luck filter
# ===========================================================================


class TestHitterLuck:
    def test_hot_confirmed(self):
        """Hot hitter with high xwOBA → CONFIRMED."""
        sc = {"Aaron Judge": _statcast_hitter("Aaron Judge", xwoba=0.400)}
        status, xwoba, barrel = _hitter_luck("Aaron Judge", sc, is_hot=True)
        assert status == LuckStatus.CONFIRMED
        assert xwoba == 0.400

    def test_hot_lucky(self):
        """Hot hitter with low xwOBA → LUCKY."""
        sc = {"Lucky Lou": _statcast_hitter("Lucky Lou", xwoba=0.280)}
        status, xwoba, _ = _hitter_luck("Lucky Lou", sc, is_hot=True)
        assert status == LuckStatus.LUCKY
        assert xwoba == 0.280

    def test_cold_unlucky(self):
        """Cold hitter with high xwOBA → UNLUCKY."""
        sc = {"Snakebit Sam": _statcast_hitter("Snakebit Sam", xwoba=0.360)}
        status, _, _ = _hitter_luck("Snakebit Sam", sc, is_hot=False)
        assert status == LuckStatus.UNLUCKY

    def test_cold_confirmed(self):
        """Cold hitter with low xwOBA → CONFIRMED cold."""
        sc = {"Bad Bob": _statcast_hitter("Bad Bob", xwoba=0.250)}
        status, _, _ = _hitter_luck("Bad Bob", sc, is_hot=False)
        assert status == LuckStatus.CONFIRMED

    def test_no_statcast_data(self):
        """Missing Statcast data → UNCONFIRMED."""
        status, xwoba, barrel = _hitter_luck("Unknown", {}, is_hot=True)
        assert status == LuckStatus.UNCONFIRMED
        assert xwoba is None
        assert barrel is None

    def test_exactly_at_threshold(self):
        """xwOBA exactly at threshold counts as quality contact."""
        sc = {"Edge Case": _statcast_hitter("Edge Case", xwoba=0.320)}
        status, _, _ = _hitter_luck("Edge Case", sc, is_hot=True)
        assert status == LuckStatus.CONFIRMED

    def test_just_below_threshold(self):
        sc = {"Below": _statcast_hitter("Below", xwoba=0.319)}
        status, _, _ = _hitter_luck("Below", sc, is_hot=True)
        assert status == LuckStatus.LUCKY


class TestPitcherLuck:
    def test_hot_confirmed(self):
        """Hot pitcher with low FIP → CONFIRMED."""
        sc = {"Ace": _statcast_pitcher("Ace", fip=2.80)}
        status, fip, xera = _pitcher_luck("Ace", sc, is_hot=True)
        assert status == LuckStatus.CONFIRMED
        assert fip == 2.80

    def test_hot_lucky(self):
        """Hot pitcher with high FIP → LUCKY."""
        sc = {"Smoke Mirror": _statcast_pitcher("Smoke Mirror", fip=4.50)}
        status, fip, _ = _pitcher_luck("Smoke Mirror", sc, is_hot=True)
        assert status == LuckStatus.LUCKY

    def test_cold_unlucky(self):
        """Cold pitcher with low FIP → UNLUCKY."""
        sc = {"Unlucky Pete": _statcast_pitcher("Unlucky Pete", fip=3.20)}
        status, _, _ = _pitcher_luck("Unlucky Pete", sc, is_hot=False)
        assert status == LuckStatus.UNLUCKY

    def test_cold_confirmed(self):
        """Cold pitcher with high FIP → CONFIRMED cold."""
        sc = {"Bad Bill": _statcast_pitcher("Bad Bill", fip=5.10)}
        status, _, _ = _pitcher_luck("Bad Bill", sc, is_hot=False)
        assert status == LuckStatus.CONFIRMED

    def test_no_statcast_data(self):
        status, fip, xera = _pitcher_luck("Unknown", {}, is_hot=True)
        assert status == LuckStatus.UNCONFIRMED
        assert fip is None
        assert xera is None

    def test_at_threshold_is_quality(self):
        """FIP exactly at threshold counts as quality pitching."""
        sc = {"Edge": _statcast_pitcher("Edge", fip=4.00)}
        status, _, _ = _pitcher_luck("Edge", sc, is_hot=True)
        assert status == LuckStatus.CONFIRMED

    def test_just_above_threshold(self):
        sc = {"Above": _statcast_pitcher("Above", fip=4.01)}
        status, _, _ = _pitcher_luck("Above", sc, is_hot=True)
        assert status == LuckStatus.LUCKY


# ===========================================================================
# score_leaderboards — full integration
# ===========================================================================


def _build_hitter_pool(n: int) -> dict[int, RollingHitterStats]:
    """Build a pool of N hitters with descending quality."""
    pool = {}
    for i in range(n):
        # Hitter i has (n-i) hits out of 20 AB, (n-i)//2 HR, (n-i) RBI
        hits = max(n - i, 0)
        hr = max((n - i) // 2, 0)
        rbi = max(n - i, 0)
        pool[i] = _rolling_hitter(
            player_id=i, full_name=f"Hitter {i}", team_abbr="TST",
            at_bats=20, hits=min(hits, 20), home_runs=hr, rbi=rbi,
        )
    return pool


def _build_starter_pool(n: int) -> dict[int, RollingStarterStats]:
    """Build a pool of N starters with descending quality."""
    pool = {}
    for i in range(n):
        # Starter i has fewer earned runs and more strikeouts as i decreases
        er = i + 1
        k = max(15 - i, 1)
        pool[100 + i] = _rolling_starter(
            player_id=100 + i, full_name=f"Starter {i}", team_abbr="TST",
            outs_recorded=36, earned_runs=er, strikeouts=k,
            hits_allowed=8, walks=3,
        )
    return pool


def _build_closer_pool(n: int) -> dict[int, RollingCloserStats]:
    """Build a pool of N closers with descending quality."""
    pool = {}
    for i in range(n):
        saves = max(5 - i, 0)
        blown = i
        pool[200 + i] = _rolling_closer(
            player_id=200 + i, full_name=f"Closer {i}", team_abbr="TST",
            outs_recorded=15, earned_runs=i, saves=saves,
            blown_saves=blown, strikeouts=max(8 - i, 1),
        )
    return pool


class TestScoreLeaderboards:
    def test_empty_data(self):
        r7 = _rolling_stats(snapshots_used=7)
        r15 = _rolling_stats(snapshots_used=15)
        lb = score_leaderboards(r7, r15, {}, {})
        assert lb.hot_hitters == []
        assert lb.cold_hitters == []
        assert lb.hot_pitchers == []
        assert lb.cold_pitchers == []
        assert lb.breakout_hitters == []
        assert lb.breakout_pitchers == []
        assert lb.snapshots_7d == 7
        assert lb.snapshots_15d == 15

    def test_hitter_hot_cold_ordering(self):
        """Hot hitters descend by composite; cold hitters ascend and are gated."""
        hitters = _build_hitter_pool(15)
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=5)

        assert len(lb.hot_hitters) == 5

        # Hot list is sorted descending by composite
        hot_scores = [h.composite_score for h in lb.hot_hitters]
        assert hot_scores == sorted(hot_scores, reverse=True)

        # Cold list ascends and every entry clears the absolute cold gate.
        cold_scores = [h.composite_score for h in lb.cold_hitters]
        assert len(lb.cold_hitters) > 0
        assert cold_scores == sorted(cold_scores)
        assert all(s <= COLD_HITTER_COMPOSITE_MAX for s in cold_scores)

        # Hot and cold never share a player.
        hot_ids = {h.player_id for h in lb.hot_hitters}
        cold_ids = {h.player_id for h in lb.cold_hitters}
        assert hot_ids.isdisjoint(cold_ids)

    def test_pitcher_hot_cold_ordering(self):
        """Pitchers (starters + closers merged) are ranked; cold is gated."""
        starters = _build_starter_pool(8)
        closers = _build_closer_pool(4)
        r7 = _rolling_stats(starters=starters, closers=closers)
        r15 = _rolling_stats(snapshots_used=15, starters=starters, closers=closers)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=5)

        assert len(lb.hot_pitchers) == 5
        hot_scores = [p.composite_score for p in lb.hot_pitchers]
        assert hot_scores == sorted(hot_scores, reverse=True)

        cold_scores = [p.composite_score for p in lb.cold_pitchers]
        assert len(lb.cold_pitchers) > 0
        assert cold_scores == sorted(cold_scores)
        assert all(s <= COLD_PITCHER_COMPOSITE_MAX for s in cold_scores)

        hot_ids = {p.player_id for p in lb.hot_pitchers}
        cold_ids = {p.player_id for p in lb.cold_pitchers}
        assert hot_ids.isdisjoint(cold_ids)

    def test_leaderboard_size_limits(self):
        """Leaderboard never returns more than leaderboard_size entries."""
        hitters = _build_hitter_pool(20)
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=3)
        assert len(lb.hot_hitters) == 3
        assert len(lb.cold_hitters) == 3

    def test_fewer_than_leaderboard_size(self):
        """Fewer players than size: all are hot, so none remain for cold."""
        hitters = _build_hitter_pool(3)
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=10)
        assert len(lb.hot_hitters) == 3
        # Every hitter is on the hot list, so the cold list is empty (disjoint).
        assert len(lb.cold_hitters) == 0

    def test_starters_and_closers_mixed_on_same_leaderboard(self):
        """Both starters and closers appear on the pitcher leaderboard."""
        starters = _build_starter_pool(3)
        closers = _build_closer_pool(3)
        r7 = _rolling_stats(starters=starters, closers=closers)
        r15 = _rolling_stats(snapshots_used=15, starters=starters, closers=closers)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=6)
        roles = {p.role for p in lb.hot_pitchers}
        assert "starter" in roles
        assert "closer" in roles

    def test_starter_entry_has_starter_fields(self):
        starters = {100: _rolling_starter(player_id=100, full_name="Ace")}
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)
        entry = lb.hot_pitchers[0]
        assert entry.role == "starter"
        assert entry.whip is not None
        assert entry.k_per_9 is not None
        assert entry.starts is not None
        assert entry.saves is None
        assert entry.save_pct is None

    def test_closer_entry_has_closer_fields(self):
        closers = {200: _rolling_closer(player_id=200, full_name="Lockdown")}
        r7 = _rolling_stats(closers=closers)
        r15 = _rolling_stats(snapshots_used=15, closers=closers)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)
        entry = lb.hot_pitchers[0]
        assert entry.role == "closer"
        assert entry.saves is not None
        assert entry.save_pct is not None
        assert entry.appearances is not None
        assert entry.whip is None
        assert entry.starts is None


class TestScoreLeaderboardsLuck:
    """Verify luck status is set correctly in full leaderboard flow."""

    def test_hot_hitter_with_statcast_confirmed(self):
        hitters = {1: _rolling_hitter(player_id=1, full_name="Star",
                                      at_bats=20, hits=10, home_runs=4, rbi=10)}
        sc = {"Star": _statcast_hitter("Star", xwoba=0.380)}
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, sc, {})
        assert lb.hot_hitters[0].luck_status == LuckStatus.CONFIRMED
        assert lb.hot_hitters[0].xwoba == 0.380

    def test_cold_hitter_with_statcast_unlucky(self):
        # A masher takes the single hot slot; the snakebit hitter (poor results
        # but good xwOBA) is the cold one and reads UNLUCKY.
        hitters = {
            1: _rolling_hitter(player_id=1, full_name="Snakebit",
                               at_bats=20, hits=2, home_runs=0, rbi=0),
            2: _rolling_hitter(player_id=2, full_name="Masher",
                               at_bats=20, hits=10, home_runs=4, rbi=10),
        }
        sc = {"Snakebit": _statcast_hitter("Snakebit", xwoba=0.370)}
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, sc, {}, leaderboard_size=1)
        assert lb.cold_hitters[0].full_name == "Snakebit"
        assert lb.cold_hitters[0].luck_status == LuckStatus.UNLUCKY

    def test_no_statcast_unconfirmed(self):
        hitters = {1: _rolling_hitter(player_id=1, full_name="Unknown")}
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {})
        assert lb.hot_hitters[0].luck_status == LuckStatus.UNCONFIRMED
        assert lb.hot_hitters[0].xwoba is None

    def test_hot_pitcher_confirmed(self):
        starters = {100: _rolling_starter(player_id=100, full_name="Ace",
                                          outs_recorded=36, earned_runs=1,
                                          strikeouts=18, hits_allowed=5, walks=1)}
        sc = {"Ace": _statcast_pitcher("Ace", fip=2.50)}
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, sc)
        assert lb.hot_pitchers[0].luck_status == LuckStatus.CONFIRMED
        assert lb.hot_pitchers[0].fip == 2.50

    def test_cold_pitcher_unlucky(self):
        # An ace takes the single hot slot; the struggling pitcher (poor results
        # but good FIP) is the cold one and reads UNLUCKY.
        starters = {
            100: _rolling_starter(player_id=100, full_name="Unlucky",
                                  outs_recorded=18, earned_runs=8,
                                  strikeouts=3, hits_allowed=14, walks=5),
            101: _rolling_starter(player_id=101, full_name="Ace",
                                  outs_recorded=36, earned_runs=1,
                                  strikeouts=18, hits_allowed=5, walks=1),
        }
        sc = {"Unlucky": _statcast_pitcher("Unlucky", fip=3.20)}
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, sc, leaderboard_size=1)
        assert lb.cold_pitchers[0].full_name == "Unlucky"
        assert lb.cold_pitchers[0].luck_status == LuckStatus.UNLUCKY

    def test_hot_pitcher_lucky(self):
        starters = {100: _rolling_starter(player_id=100, full_name="Lucky",
                                          outs_recorded=36, earned_runs=1,
                                          strikeouts=18, hits_allowed=5, walks=1)}
        sc = {"Lucky": _statcast_pitcher("Lucky", fip=4.80)}
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, sc)
        assert lb.hot_pitchers[0].luck_status == LuckStatus.LUCKY


# ===========================================================================
# Breakout detection
# ===========================================================================


class TestBreakoutHitters:
    def test_breakout_when_7d_hot_and_15d_above_median(self):
        """A 7-day hot hitter whose 15-day composite is above the 15-day median."""
        # Hitter 0 is best in both windows
        hitters_7d = _build_hitter_pool(10)
        hitters_15d = _build_hitter_pool(10)
        r7 = _rolling_stats(hitters=hitters_7d)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters_15d)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=3)

        # Top 3 hitters from the 7-day window who also have above-median 15-day
        assert len(lb.breakout_hitters) > 0
        # All breakout hitters should be from the hot list
        hot_ids = {h.player_id for h in lb.hot_hitters}
        for b in lb.breakout_hitters:
            assert b.player_id in hot_ids

    def test_no_breakout_when_15d_below_median(self):
        """A 7-day hot hitter who is poor in the 15-day window → no breakout."""
        # 7-day: hitter 1 is hot (great stats)
        hot_hitter = _rolling_hitter(player_id=1, full_name="Flash",
                                     at_bats=20, hits=10, home_runs=4, rbi=10)
        hitters_7d = {1: hot_hitter}

        # 15-day: Flash is terrible; two other hitters are much better.
        # With 3 players, median is the middle value.  Flash's composite
        # must be strictly below it.
        flash_15d = _rolling_hitter(player_id=1, full_name="Flash",
                                    at_bats=50, hits=5, home_runs=0, rbi=1)
        star_15d = _rolling_hitter(player_id=2, full_name="Star",
                                   at_bats=50, hits=20, home_runs=5, rbi=12)
        other_15d = _rolling_hitter(player_id=3, full_name="Other",
                                    at_bats=50, hits=18, home_runs=4, rbi=10)
        hitters_15d = {1: flash_15d, 2: star_15d, 3: other_15d}

        # Sanity: Flash's 15-day composite is below the others
        flash_c = _hitter_composite(flash_15d)
        star_c = _hitter_composite(star_15d)
        other_c = _hitter_composite(other_15d)
        assert flash_c < min(star_c, other_c), "fixture should make Flash worst"

        r7 = _rolling_stats(hitters=hitters_7d)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters_15d)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)

        # Flash is 7-day hot but 15-day below median → not a breakout
        assert len(lb.breakout_hitters) == 0

    def test_no_breakout_when_15d_data_empty(self):
        """No 15-day data → no breakouts."""
        hitters_7d = _build_hitter_pool(5)
        r7 = _rolling_stats(hitters=hitters_7d)
        r15 = _rolling_stats(snapshots_used=15)  # no hitters
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=3)
        assert lb.breakout_hitters == []

    def test_breakout_uses_15d_stats(self):
        """Breakout entries carry 15-day rolling stats, not 7-day."""
        h7 = _rolling_hitter(player_id=1, full_name="Surge",
                             at_bats=20, hits=10, home_runs=4, rbi=10,
                             plate_appearances=22)
        h15 = _rolling_hitter(player_id=1, full_name="Surge",
                              at_bats=50, hits=18, home_runs=6, rbi=15,
                              plate_appearances=55)
        r7 = _rolling_stats(hitters={1: h7})
        r15 = _rolling_stats(snapshots_used=15, hitters={1: h15})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)

        # Single player: always >= median of [self], so always a breakout
        assert len(lb.breakout_hitters) == 1
        # Should use 15-day PA, not 7-day
        assert lb.breakout_hitters[0].plate_appearances == 55

    def test_breakout_hitter_gets_luck_status(self):
        """Breakout hitters get the hot-side luck filter applied."""
        h7 = _rolling_hitter(player_id=1, full_name="Lucky Break",
                             at_bats=20, hits=10, home_runs=4, rbi=10)
        h15 = _rolling_hitter(player_id=1, full_name="Lucky Break",
                              at_bats=50, hits=20, home_runs=6, rbi=15)
        sc = {"Lucky Break": _statcast_hitter("Lucky Break", xwoba=0.280)}
        r7 = _rolling_stats(hitters={1: h7})
        r15 = _rolling_stats(snapshots_used=15, hitters={1: h15})
        lb = score_leaderboards(r7, r15, sc, {}, leaderboard_size=1)

        assert len(lb.breakout_hitters) == 1
        assert lb.breakout_hitters[0].luck_status == LuckStatus.LUCKY

    def test_no_breakout_when_7d_hot_absent_from_15d(self):
        """A 7-day hot player not in the 15-day pool is skipped for breakout."""
        # Player 1 is hot in 7d but not in 15d at all (e.g., recent callup)
        h7 = _rolling_hitter(player_id=1, full_name="Callup",
                             at_bats=20, hits=10, home_runs=4, rbi=10)
        # Player 2 is only in the 15d pool (provides a non-empty pool)
        h15_other = _rolling_hitter(player_id=2, full_name="Veteran",
                                    at_bats=50, hits=15, home_runs=3, rbi=8)
        r7 = _rolling_stats(hitters={1: h7})
        r15 = _rolling_stats(snapshots_used=15, hitters={2: h15_other})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)

        # Callup is 7d hot but absent from 15d → no breakout
        assert len(lb.breakout_hitters) == 0


class TestBreakoutPitchers:
    def test_breakout_starter(self):
        """A 7-day hot starter with sustained 15-day performance."""
        s7 = _rolling_starter(player_id=100, full_name="Ace",
                              outs_recorded=36, earned_runs=1, strikeouts=18,
                              hits_allowed=5, walks=1)
        s15 = _rolling_starter(player_id=100, full_name="Ace",
                               outs_recorded=72, earned_runs=5, strikeouts=30,
                               hits_allowed=15, walks=5)
        r7 = _rolling_stats(starters={100: s7})
        r15 = _rolling_stats(snapshots_used=15, starters={100: s15})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)

        # Single pitcher: 15-day composite equals the median, so >= median
        assert len(lb.breakout_pitchers) == 1
        assert lb.breakout_pitchers[0].role == "starter"

    def test_breakout_closer(self):
        """A 7-day hot closer with sustained 15-day performance."""
        c7 = _rolling_closer(player_id=200, full_name="Lockdown",
                             outs_recorded=15, earned_runs=0, saves=5,
                             blown_saves=0, strikeouts=8)
        c15 = _rolling_closer(player_id=200, full_name="Lockdown",
                              outs_recorded=30, earned_runs=1, saves=9,
                              blown_saves=1, strikeouts=15)
        r7 = _rolling_stats(closers={200: c7})
        r15 = _rolling_stats(snapshots_used=15, closers={200: c15})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)

        assert len(lb.breakout_pitchers) == 1
        assert lb.breakout_pitchers[0].role == "closer"

    def test_no_breakout_pitcher_when_15d_empty(self):
        starters = {100: _rolling_starter(player_id=100)}
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15)
        lb = score_leaderboards(r7, r15, {}, {})
        assert lb.breakout_pitchers == []

    def test_breakout_pitcher_uses_15d_stats(self):
        """Breakout pitcher entries use 15-day rolling stats."""
        s7 = _rolling_starter(player_id=100, full_name="Ace", starts=1,
                              outs_recorded=21, earned_runs=0)
        s15 = _rolling_starter(player_id=100, full_name="Ace", starts=3,
                               outs_recorded=63, earned_runs=4)
        r7 = _rolling_stats(starters={100: s7})
        r15 = _rolling_stats(snapshots_used=15, starters={100: s15})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)

        assert len(lb.breakout_pitchers) == 1
        assert lb.breakout_pitchers[0].starts == 3


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_single_hitter(self):
        """A lone qualified hitter is hot; cold is empty (hot/cold are disjoint)."""
        hitters = {1: _rolling_hitter(player_id=1, full_name="Solo")}
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {})
        assert len(lb.hot_hitters) == 1
        assert len(lb.cold_hitters) == 0

    def test_all_same_composite(self):
        """Identical good composites: top N are hot; leftovers fail the cold gate."""
        hitters = {
            i: _rolling_hitter(player_id=i, full_name=f"Clone {i}",
                               at_bats=20, hits=6, home_runs=2, rbi=5)
            for i in range(5)
        }
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=3)
        assert len(lb.hot_hitters) == 3
        # The 2 leftover clones score above the cold gate, so cold is empty.
        assert len(lb.cold_hitters) == 0

    def test_hitters_only_no_pitchers(self):
        hitters = _build_hitter_pool(5)
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {})
        assert len(lb.hot_hitters) > 0
        assert lb.hot_pitchers == []
        assert lb.cold_pitchers == []

    def test_pitchers_only_no_hitters(self):
        starters = _build_starter_pool(5)
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, {})
        assert lb.hot_hitters == []
        assert len(lb.hot_pitchers) > 0

    def test_snapshot_counts_propagated(self):
        r7 = _rolling_stats(snapshots_used=5)
        r15 = _rolling_stats(snapshots_used=12)
        lb = score_leaderboards(r7, r15, {}, {})
        assert lb.snapshots_7d == 5
        assert lb.snapshots_15d == 12

    def test_leaderboard_dataclass_frozen(self):
        r7 = _rolling_stats()
        r15 = _rolling_stats(snapshots_used=15)
        lb = score_leaderboards(r7, r15, {}, {})
        with pytest.raises(AttributeError):
            lb.snapshots_7d = 99  # type: ignore[misc]

    def test_leaderboard_hitter_dataclass_frozen(self):
        hitters = {1: _rolling_hitter(player_id=1)}
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {})
        with pytest.raises(AttributeError):
            lb.hot_hitters[0].composite_score = 99  # type: ignore[misc]

    def test_cold_hitters_different_luck_from_hot(self):
        """Same hitter on hot vs cold list gets opposite luck interpretation."""
        h = _rolling_hitter(player_id=1, full_name="Borderline",
                            at_bats=20, hits=6, home_runs=2, rbi=5)
        sc = {"Borderline": _statcast_hitter("Borderline", xwoba=0.350)}

        # Manually check hot and cold luck
        hot_status, _, _ = _hitter_luck("Borderline", sc, is_hot=True)
        cold_status, _, _ = _hitter_luck("Borderline", sc, is_hot=False)
        assert hot_status == LuckStatus.CONFIRMED
        assert cold_status == LuckStatus.UNLUCKY


    def test_hot_cold_disjoint_when_pool_barely_exceeds_size(self):
        """11 hitters with size=10: hot and cold should NOT share players."""
        hitters = _build_hitter_pool(11)
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=10)

        hot_ids = {h.player_id for h in lb.hot_hitters}
        cold_ids = {h.player_id for h in lb.cold_hitters}
        assert len(lb.hot_hitters) == 10
        # Cold list should only have the 1 player not on the hot list
        assert len(lb.cold_hitters) == 1
        assert hot_ids.isdisjoint(cold_ids)

    def test_cold_pitcher_confirmed_via_full_pipeline(self):
        """Cold pitcher with high FIP gets CONFIRMED status through score_leaderboards."""
        bad = _rolling_starter(player_id=100, full_name="Bad Pitcher",
                               outs_recorded=18, earned_runs=8,
                               strikeouts=3, hits_allowed=14, walks=5)
        ace = _rolling_starter(player_id=101, full_name="Ace",
                               outs_recorded=36, earned_runs=1,
                               strikeouts=18, hits_allowed=5, walks=1)
        sc = {"Bad Pitcher": _statcast_pitcher("Bad Pitcher", fip=5.50)}
        r7 = _rolling_stats(starters={100: bad, 101: ace})
        r15 = _rolling_stats(snapshots_used=15, starters={100: bad, 101: ace})
        lb = score_leaderboards(r7, r15, {}, sc, leaderboard_size=1)
        assert lb.cold_pitchers[0].full_name == "Bad Pitcher"
        assert lb.cold_pitchers[0].luck_status == LuckStatus.CONFIRMED

    def test_breakout_two_players_one_below_median(self):
        """With 2 hitters in 15-day pool, only the above-median one is a breakout."""
        # Both are hot in 7-day
        h1_7d = _rolling_hitter(player_id=1, full_name="Star",
                                at_bats=20, hits=10, home_runs=4, rbi=10)
        h2_7d = _rolling_hitter(player_id=2, full_name="Flash",
                                at_bats=20, hits=8, home_runs=3, rbi=7)

        # 15-day: Star sustained, Flash fell off
        h1_15d = _rolling_hitter(player_id=1, full_name="Star",
                                 at_bats=50, hits=20, home_runs=6, rbi=15)
        h2_15d = _rolling_hitter(player_id=2, full_name="Flash",
                                 at_bats=50, hits=6, home_runs=0, rbi=2)

        r7 = _rolling_stats(hitters={1: h1_7d, 2: h2_7d})
        r15 = _rolling_stats(snapshots_used=15, hitters={1: h1_15d, 2: h2_15d})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=2)

        # Star should be a breakout (above median); Flash should not
        breakout_ids = {b.player_id for b in lb.breakout_hitters}
        assert 1 in breakout_ids
        assert 2 not in breakout_ids

    def test_hot_cold_sets_distinct_when_large_pool(self):
        """With 20 hitters and leaderboard_size=5, hot and cold sets don't overlap."""
        hitters = _build_hitter_pool(20)
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=5)

        hot_ids = {h.player_id for h in lb.hot_hitters}
        cold_ids = {h.player_id for h in lb.cold_hitters}
        assert hot_ids.isdisjoint(cold_ids)


class TestColdSelectionGate:
    """Cold lists must be disjoint from hot and gated by an absolute floor.

    These cover the fix for the bug where a genuinely hot pitcher (e.g. a
    1.89-ERA ace) appeared on both the hot and cold pitcher boards because a
    thin qualified pool triggered a full-pool fallback.
    """

    def test_hot_pitcher_not_cold_in_thin_pool(self):
        """A hot pitcher never lands on cold, even when the pool is smaller
        than the leaderboard size (the old fallback used to dump him there)."""
        starters = {300: _rolling_starter(player_id=300, full_name="Chris Sale",
                                          outs_recorded=21, earned_runs=1,
                                          strikeouts=8, hits_allowed=4, walks=0)}
        for i in range(5):
            starters[100 + i] = _rolling_starter(
                player_id=100 + i, full_name=f"Starter {i}",
                outs_recorded=21, earned_runs=i + 1,
                strikeouts=max(8 - i, 2), hits_allowed=5, walks=1,
            )
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        # Pool (6) < leaderboard_size (10): used to trigger the full-pool fallback.
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=10)

        hot_names = {p.full_name for p in lb.hot_pitchers}
        cold_names = {p.full_name for p in lb.cold_pitchers}
        assert "Chris Sale" in hot_names
        assert "Chris Sale" not in cold_names
        assert hot_names.isdisjoint(cold_names)

    def test_cold_empty_when_no_one_is_actually_cold(self):
        """A thin pool of good pitchers yields an empty cold list, not false colds."""
        starters = {
            100 + i: _rolling_starter(player_id=100 + i, full_name=f"Good {i}",
                                      outs_recorded=21, earned_runs=1,
                                      strikeouts=8, hits_allowed=4, walks=0)
            for i in range(3)
        }
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=10)
        assert len(lb.hot_pitchers) == 3
        assert lb.cold_pitchers == []

    def test_swingman_listed_once(self):
        """A pitcher who both starts and relieves in the window appears once,
        not twice, on the merged pitcher leaderboard (kept on his better role)."""
        starter = _rolling_starter(player_id=300, full_name="Swingman",
                                   outs_recorded=21, earned_runs=1, strikeouts=8,
                                   hits_allowed=4, walks=0)
        closer = _rolling_closer(player_id=300, full_name="Swingman",
                                 outs_recorded=3, earned_runs=4, saves=0,
                                 blown_saves=2, holds=0, strikeouts=1, walks=1)
        r7 = _rolling_stats(starters={300: starter}, closers={300: closer})
        r15 = _rolling_stats(snapshots_used=15, starters={300: starter},
                             closers={300: closer})
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=10)

        all_pitchers = lb.hot_pitchers + lb.cold_pitchers
        swingman_entries = [p for p in all_pitchers if p.full_name == "Swingman"]
        assert len(swingman_entries) == 1
        # Collapsed to the higher-scoring (starter) role.
        assert swingman_entries[0].role == "starter"

    def test_cold_hitter_gate_excludes_mediocre_in_thin_pool(self):
        """Only genuinely cold hitters make the cold list; mediocre ones don't."""
        hitters = {
            1: _rolling_hitter(player_id=1, full_name="Masher",
                               at_bats=20, hits=10, home_runs=4, rbi=10),
            2: _rolling_hitter(player_id=2, full_name="Average",
                               at_bats=20, hits=6, home_runs=2, rbi=5),
            3: _rolling_hitter(player_id=3, full_name="Frozen",
                               at_bats=20, hits=1, home_runs=0, rbi=0),
        }
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, {}, {}, leaderboard_size=1)
        # Masher is hot; only Frozen clears the cold gate (Average is excluded).
        cold_names = {h.full_name for h in lb.cold_hitters}
        assert cold_names == {"Frozen"}


class TestLuckStatusEnum:
    def test_all_values(self):
        assert LuckStatus.CONFIRMED.value == "confirmed"
        assert LuckStatus.LUCKY.value == "lucky"
        assert LuckStatus.UNLUCKY.value == "unlucky"
        assert LuckStatus.UNCONFIRMED.value == "unconfirmed"

    def test_enum_members(self):
        assert len(LuckStatus) == 4


class TestLeaderboardEntryFields:
    """Verify that leaderboard entries carry the expected data."""

    def test_hitter_entry_fields(self):
        h = _rolling_hitter(player_id=1, full_name="Judge", team_abbr="NYY",
                            games=6, plate_appearances=25, at_bats=22,
                            hits=9, doubles=2, triples=0, home_runs=3,
                            rbi=7, stolen_bases=1, walks=3, strikeouts=5)
        sc = {"Judge": _statcast_hitter("Judge", xwoba=0.410, barrel_pct=12.5)}
        hitters = {1: h}
        r7 = _rolling_stats(hitters=hitters)
        r15 = _rolling_stats(snapshots_used=15, hitters=hitters)
        lb = score_leaderboards(r7, r15, sc, {})
        entry = lb.hot_hitters[0]

        assert entry.player_id == 1
        assert entry.full_name == "Judge"
        assert entry.team_abbr == "NYY"
        assert entry.games == 6
        assert entry.plate_appearances == 25
        assert entry.avg == pytest.approx(9 / 22)
        assert entry.obp == pytest.approx(12 / 25)
        assert entry.home_runs == 3
        assert entry.rbi == 7
        assert entry.stolen_bases == 1
        assert entry.composite_score > 0
        assert entry.luck_status == LuckStatus.CONFIRMED
        assert entry.xwoba == 0.410
        assert entry.barrel_pct == 12.5

    def test_starter_entry_fields(self):
        s = _rolling_starter(player_id=100, full_name="Cole", team_abbr="NYY",
                             starts=2, outs_recorded=39, hits_allowed=10,
                             earned_runs=3, walks=4, strikeouts=16,
                             home_runs_allowed=1, pitches_thrown=200)
        sc_p = {"Cole": _statcast_pitcher("Cole", fip=2.80, xera=2.90)}
        starters = {100: s}
        r7 = _rolling_stats(starters=starters)
        r15 = _rolling_stats(snapshots_used=15, starters=starters)
        lb = score_leaderboards(r7, r15, {}, sc_p)
        entry = lb.hot_pitchers[0]

        assert entry.role == "starter"
        assert entry.era == pytest.approx((3 * 9) / (39 / 3))
        assert entry.whip == pytest.approx((4 + 10) / (39 / 3))
        assert entry.k_per_9 == pytest.approx((16 * 9) / (39 / 3))
        assert entry.starts == 2
        assert entry.fip == 2.80
        assert entry.xera == 2.90
        assert entry.saves is None

    def test_closer_entry_fields(self):
        c = _rolling_closer(player_id=200, full_name="Clase", team_abbr="CLE",
                            appearances=5, outs_recorded=15, earned_runs=1,
                            saves=4, blown_saves=1, holds=0,
                            strikeouts=6, walks=2)
        closers = {200: c}
        r7 = _rolling_stats(closers=closers)
        r15 = _rolling_stats(snapshots_used=15, closers=closers)
        lb = score_leaderboards(r7, r15, {}, {})
        entry = lb.hot_pitchers[0]

        assert entry.role == "closer"
        assert entry.saves == 4
        assert entry.blown_saves == 1
        assert entry.save_pct == pytest.approx(0.8)
        assert entry.holds == 0
        assert entry.appearances == 5
        assert entry.whip is None
        assert entry.starts is None
