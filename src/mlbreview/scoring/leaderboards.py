"""Rolling-stat aggregation from daily snapshots.

Computes 7-day and 15-day rolling aggregates for hitters, starters, and
closers by summing counting stats across daily snapshots.  The results
feed into the leaderboard scoring (U4) and luck filter.

The rolling window is defined in *calendar days* (number of most-recent
snapshots), not games played.  Off-days produce no snapshot, so a 7-day
window during a stretch with one off-day contains 6 days of data — this
is intentional and matches the product definition.

Players below the minimum-activity thresholds (MIN_PA_HITTER, MIN_IP_PITCHER,
MIN_SV_OPP_CLOSER in config.py) are filtered out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mlbreview.config import MIN_IP_PITCHER, MIN_PA_HITTER, MIN_SV_OPP_CLOSER
from mlbreview.data.snapshots import (
    CloserDayStats,
    DailySnapshot,
    HitterDayStats,
    StarterGameStats,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rolling stat dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollingHitterStats:
    """Aggregated hitter stats over a rolling window."""

    player_id: int
    full_name: str
    team_abbr: str
    games: int  # number of game appearances (may exceed snapshot count for doubleheaders)
    plate_appearances: int
    at_bats: int
    hits: int
    doubles: int
    triples: int
    home_runs: int
    rbi: int
    stolen_bases: int
    walks: int
    strikeouts: int

    @property
    def avg(self) -> float:
        """Batting average (H / AB).  Returns 0.0 when AB == 0."""
        return self.hits / self.at_bats if self.at_bats > 0 else 0.0

    @property
    def obp(self) -> float:
        """Simplified on-base percentage ((H + BB) / PA).

        This omits HBP and SF from the standard formula because the
        snapshot dataclass does not track them.  Acceptable for V2
        leaderboard ranking; not intended for official record-keeping.
        """
        return (self.hits + self.walks) / self.plate_appearances if self.plate_appearances > 0 else 0.0

    @property
    def slg(self) -> float:
        """Slugging percentage (TB / AB).  Returns 0.0 when AB == 0."""
        if self.at_bats == 0:
            return 0.0
        singles = self.hits - self.doubles - self.triples - self.home_runs
        total_bases = singles + 2 * self.doubles + 3 * self.triples + 4 * self.home_runs
        return total_bases / self.at_bats


@dataclass(frozen=True)
class RollingStarterStats:
    """Aggregated starting pitcher stats over a rolling window."""

    player_id: int
    full_name: str
    team_abbr: str
    starts: int  # number of starts in the window
    outs_recorded: int
    hits_allowed: int
    earned_runs: int
    walks: int
    strikeouts: int
    home_runs_allowed: int
    pitches_thrown: int

    @property
    def innings_pitched(self) -> float:
        """Total innings pitched as a decimal."""
        return self.outs_recorded / 3

    @property
    def era(self) -> float:
        """ERA over the rolling window.  Returns 0.0 when IP == 0."""
        ip = self.innings_pitched
        return (self.earned_runs * 9) / ip if ip > 0 else 0.0

    @property
    def whip(self) -> float:
        """WHIP over the rolling window.  Returns 0.0 when IP == 0."""
        ip = self.innings_pitched
        return (self.walks + self.hits_allowed) / ip if ip > 0 else 0.0

    @property
    def k_per_9(self) -> float:
        """K/9 over the rolling window.  Returns 0.0 when IP == 0."""
        ip = self.innings_pitched
        return (self.strikeouts * 9) / ip if ip > 0 else 0.0


@dataclass(frozen=True)
class RollingCloserStats:
    """Aggregated closer/reliever stats over a rolling window."""

    player_id: int
    full_name: str
    team_abbr: str
    appearances: int  # number of snapshots the closer appeared in
    outs_recorded: int
    earned_runs: int
    saves: int
    blown_saves: int
    holds: int
    strikeouts: int
    walks: int

    @property
    def innings_pitched(self) -> float:
        return self.outs_recorded / 3

    @property
    def era(self) -> float:
        ip = self.innings_pitched
        return (self.earned_runs * 9) / ip if ip > 0 else 0.0

    @property
    def save_pct(self) -> float:
        """Save percentage.  Returns 0.0 when no save opportunities."""
        opps = self.saves + self.blown_saves
        return self.saves / opps if opps > 0 else 0.0


# ---------------------------------------------------------------------------
# Aggregation logic
# ---------------------------------------------------------------------------


def _aggregate_hitters(
    snapshots: list[DailySnapshot],
) -> dict[int, RollingHitterStats]:
    """Sum hitter counting stats across snapshots, keyed by player_id.

    Snapshots are expected newest-first (from ``load_snapshots``).
    Team affiliation is taken from the first (most recent) appearance.
    """
    accum: dict[int, dict] = {}

    for snap in snapshots:
        for h in snap.hitters:
            if h.player_id not in accum:
                accum[h.player_id] = {
                    "player_id": h.player_id,
                    "full_name": h.full_name,
                    "team_abbr": h.team_abbr,  # first seen = most recent
                    "games": 0,
                    "plate_appearances": 0,
                    "at_bats": 0,
                    "hits": 0,
                    "doubles": 0,
                    "triples": 0,
                    "home_runs": 0,
                    "rbi": 0,
                    "stolen_bases": 0,
                    "walks": 0,
                    "strikeouts": 0,
                }
            a = accum[h.player_id]
            a["games"] += 1
            a["plate_appearances"] += h.plate_appearances
            a["at_bats"] += h.at_bats
            a["hits"] += h.hits
            a["doubles"] += h.doubles
            a["triples"] += h.triples
            a["home_runs"] += h.home_runs
            a["rbi"] += h.rbi
            a["stolen_bases"] += h.stolen_bases
            a["walks"] += h.walks
            a["strikeouts"] += h.strikeouts

    return {
        pid: RollingHitterStats(**data)
        for pid, data in accum.items()
    }


def _aggregate_starters(
    snapshots: list[DailySnapshot],
) -> dict[int, RollingStarterStats]:
    """Sum starter counting stats across snapshots, keyed by player_id.

    Team affiliation is taken from the first (most recent) appearance.
    """
    accum: dict[int, dict] = {}

    for snap in snapshots:
        for s in snap.starters:
            if s.player_id not in accum:
                accum[s.player_id] = {
                    "player_id": s.player_id,
                    "full_name": s.full_name,
                    "team_abbr": s.team_abbr,  # first seen = most recent
                    "starts": 0,
                    "outs_recorded": 0,
                    "hits_allowed": 0,
                    "earned_runs": 0,
                    "walks": 0,
                    "strikeouts": 0,
                    "home_runs_allowed": 0,
                    "pitches_thrown": 0,
                }
            a = accum[s.player_id]
            a["starts"] += 1
            a["outs_recorded"] += s.outs_recorded
            a["hits_allowed"] += s.hits_allowed
            a["earned_runs"] += s.earned_runs
            a["walks"] += s.walks
            a["strikeouts"] += s.strikeouts
            a["home_runs_allowed"] += s.home_runs_allowed
            a["pitches_thrown"] += s.pitches_thrown

    return {
        pid: RollingStarterStats(**data)
        for pid, data in accum.items()
    }


def _aggregate_closers(
    snapshots: list[DailySnapshot],
) -> dict[int, RollingCloserStats]:
    """Sum closer counting stats across snapshots, keyed by player_id.

    Team affiliation is taken from the first (most recent) appearance.
    """
    accum: dict[int, dict] = {}

    for snap in snapshots:
        for c in snap.closers:
            if c.player_id not in accum:
                accum[c.player_id] = {
                    "player_id": c.player_id,
                    "full_name": c.full_name,
                    "team_abbr": c.team_abbr,  # first seen = most recent
                    "appearances": 0,
                    "outs_recorded": 0,
                    "earned_runs": 0,
                    "saves": 0,
                    "blown_saves": 0,
                    "holds": 0,
                    "strikeouts": 0,
                    "walks": 0,
                }
            a = accum[c.player_id]
            a["appearances"] += 1
            a["outs_recorded"] += c.outs_recorded
            a["earned_runs"] += c.earned_runs
            a["saves"] += c.saves
            a["blown_saves"] += c.blown_saves
            a["holds"] += c.holds
            a["strikeouts"] += c.strikeouts
            a["walks"] += c.walks

    return {
        pid: RollingCloserStats(**data)
        for pid, data in accum.items()
    }


# ---------------------------------------------------------------------------
# Qualification filters
# ---------------------------------------------------------------------------


def _filter_qualified_hitters(
    stats: dict[int, RollingHitterStats],
    *,
    min_pa: int = MIN_PA_HITTER,
) -> dict[int, RollingHitterStats]:
    """Keep only hitters with enough plate appearances."""
    return {
        pid: s for pid, s in stats.items()
        if s.plate_appearances >= min_pa
    }


def _filter_qualified_starters(
    stats: dict[int, RollingStarterStats],
    *,
    min_ip: float = MIN_IP_PITCHER,
) -> dict[int, RollingStarterStats]:
    """Keep only starters with enough innings pitched."""
    return {
        pid: s for pid, s in stats.items()
        if s.innings_pitched >= min_ip
    }


def _filter_qualified_closers(
    stats: dict[int, RollingCloserStats],
    *,
    min_sv_opp: int = MIN_SV_OPP_CLOSER,
) -> dict[int, RollingCloserStats]:
    """Keep only closers with enough save opportunities."""
    return {
        pid: s for pid, s in stats.items()
        if (s.saves + s.blown_saves) >= min_sv_opp
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollingStats:
    """Complete rolling-stat aggregation for one window size.

    Contains qualified player stats only; the leaderboard scoring (U4)
    uses these dicts directly.  ``snapshots_used`` is the number of
    daily snapshots that were actually aggregated (may be fewer than
    the requested window if data is missing for off-days or early season).
    """

    snapshots_used: int
    hitters: dict[int, RollingHitterStats]
    starters: dict[int, RollingStarterStats]
    closers: dict[int, RollingCloserStats]


def compute_rolling_stats(
    snapshots: list[DailySnapshot],
    *,
    min_pa: int = MIN_PA_HITTER,
    min_ip: float = MIN_IP_PITCHER,
    min_sv_opp: int = MIN_SV_OPP_CLOSER,
) -> RollingStats:
    """Aggregate counting stats across snapshots and apply qualification filters.

    Parameters
    ----------
    snapshots
        Daily snapshots to aggregate, typically the most recent N days
        loaded by ``load_snapshots(n_days=ROLLING_WINDOW_DAYS)``.
    min_pa, min_ip, min_sv_opp
        Minimum activity thresholds.  Players below these are excluded.

    Returns
    -------
    RollingStats
        Aggregated and filtered stats for all three player roles.
    """
    if not snapshots:
        return RollingStats(
            snapshots_used=0,
            hitters={},
            starters={},
            closers={},
        )

    all_hitters = _aggregate_hitters(snapshots)
    all_starters = _aggregate_starters(snapshots)
    all_closers = _aggregate_closers(snapshots)

    qualified_hitters = _filter_qualified_hitters(all_hitters, min_pa=min_pa)
    qualified_starters = _filter_qualified_starters(all_starters, min_ip=min_ip)
    qualified_closers = _filter_qualified_closers(all_closers, min_sv_opp=min_sv_opp)

    logger.info(
        "Rolling stats: %d snapshots → %d/%d hitters, %d/%d starters, %d/%d closers qualified",
        len(snapshots),
        len(qualified_hitters), len(all_hitters),
        len(qualified_starters), len(all_starters),
        len(qualified_closers), len(all_closers),
    )

    return RollingStats(
        snapshots_used=len(snapshots),
        hitters=qualified_hitters,
        starters=qualified_starters,
        closers=qualified_closers,
    )
