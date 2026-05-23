"""Rolling-stat aggregation, composite scoring, and luck filter.

**Rolling aggregation (U3):** Computes 7-day and 15-day rolling aggregates
for hitters, starters, and closers by summing counting stats across daily
snapshots.  Players below minimum-activity thresholds are filtered out.

**Leaderboard scoring (U4):** Ranks qualified players by composite score
(traditional stats), assigns a luck status by comparing against season-level
Statcast metrics, and identifies breakout players (7-day hot + 15-day
sustained).  Produces six leaderboards: hot/cold hitters, hot/cold
pitchers, breakout hitters, breakout pitchers.

The rolling window is defined in *calendar days* (number of most-recent
snapshots), not games played.  Off-days produce no snapshot, so a 7-day
window during a stretch with one off-day contains 6 days of data — this
is intentional and matches the product definition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from statistics import median

from mlbreview.config import (
    CLOSER_W_ERA,
    CLOSER_W_K9,
    CLOSER_W_SV_PCT,
    COLD_HITTER_COMPOSITE_MAX,
    COLD_PITCHER_COMPOSITE_MAX,
    HITTER_CEILING_AVG,
    HITTER_CEILING_HR,
    HITTER_CEILING_RBI,
    HITTER_W_AVG,
    HITTER_W_HR,
    HITTER_W_RBI,
    LEADERBOARD_SIZE,
    LUCK_FIP_THRESHOLD,
    LUCK_XWOBA_THRESHOLD,
    MIN_IP_PITCHER,
    MIN_PA_HITTER,
    MIN_SV_OPP_CLOSER,
    PITCHER_CEILING_ERA,
    PITCHER_CEILING_K9,
    PITCHER_CEILING_WHIP,
    STARTER_W_ERA,
    STARTER_W_K9,
    STARTER_W_WHIP,
)
from mlbreview.data.snapshots import (
    CloserDayStats,
    DailySnapshot,
    HitterDayStats,
    StarterGameStats,
)
from mlbreview.data.statcast import StatcastHitter, StatcastPitcher

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


# ===================================================================
# U4 — Leaderboard scoring, luck filter, breakout detection
# ===================================================================


# ---------------------------------------------------------------------------
# Luck status enum
# ---------------------------------------------------------------------------


class LuckStatus(Enum):
    """How well a player's rolling traditional stats align with Statcast metrics.

    The luck filter compares a short-window streak (hot or cold) against
    season-level quality-of-contact data:

    - ``CONFIRMED``: traditional and Statcast agree — streak is real.
    - ``LUCKY``: hot streak but underlying quality metrics are poor — may regress.
    - ``UNLUCKY``: cold streak but underlying quality metrics are strong — expect
      a bounce-back.
    - ``UNCONFIRMED``: no Statcast data available for this player.
    """

    CONFIRMED = "confirmed"
    LUCKY = "lucky"
    UNLUCKY = "unlucky"
    UNCONFIRMED = "unconfirmed"


# ---------------------------------------------------------------------------
# Leaderboard entry dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaderboardHitter:
    """One hitter's entry on a hot/cold/breakout leaderboard.

    Carries enough data for dashboard display: traditional rolling stats,
    the composite score used for ranking, luck status, and optional
    Statcast detail for the expanded row.
    """

    player_id: int
    full_name: str
    team_abbr: str
    # Traditional rolling stats
    games: int
    plate_appearances: int
    avg: float
    obp: float
    slg: float
    home_runs: int
    rbi: int
    stolen_bases: int
    # Ranking
    composite_score: float
    luck_status: LuckStatus
    # Statcast detail (None when unavailable)
    xwoba: float | None = None
    barrel_pct: float | None = None


@dataclass(frozen=True)
class LeaderboardPitcher:
    """One pitcher's entry on a hot/cold/breakout leaderboard.

    Accommodates both starters and closers via the ``role`` field.
    Starter-only fields (whip, k_per_9, starts) are None for closers;
    closer-only fields (saves, blown_saves, save_pct, holds, appearances)
    are None for starters.
    """

    player_id: int
    full_name: str
    team_abbr: str
    role: str  # "starter" or "closer"
    # Common stats
    era: float
    innings_pitched: float
    strikeouts: int
    # Ranking
    composite_score: float
    luck_status: LuckStatus
    # Starter-only (None for closers)
    whip: float | None = None
    k_per_9: float | None = None
    starts: int | None = None
    # Closer-only (None for starters)
    saves: int | None = None
    blown_saves: int | None = None
    save_pct: float | None = None
    holds: int | None = None
    appearances: int | None = None
    # Statcast detail (None when unavailable)
    fip: float | None = None
    xera: float | None = None


@dataclass(frozen=True)
class Leaderboards:
    """Complete set of six leaderboards for the V2 dashboard.

    ``snapshots_7d`` / ``snapshots_15d`` indicate how many daily snapshots
    were actually aggregated for each window.  The dashboard shows a
    "building up data" notice when ``snapshots_7d < 7``.
    """

    hot_hitters: list[LeaderboardHitter]
    cold_hitters: list[LeaderboardHitter]
    hot_pitchers: list[LeaderboardPitcher]
    cold_pitchers: list[LeaderboardPitcher]
    breakout_hitters: list[LeaderboardHitter]
    breakout_pitchers: list[LeaderboardPitcher]
    snapshots_7d: int
    snapshots_15d: int


# ---------------------------------------------------------------------------
# Composite scoring functions
# ---------------------------------------------------------------------------


def _hitter_composite(h: RollingHitterStats) -> float:
    """Composite score for ranking hitters.  Higher = hotter.

    Formula: 0.40 × norm_avg + 0.30 × norm_hr + 0.30 × norm_rbi

    AVG is the primary signal (are they getting hits?), HR and RBI capture
    power and run production over the window.  Counting stats (HR, RBI) are
    used directly rather than per-PA rates because fantasy managers think
    in counting stats for 7-day windows.
    """
    norm_avg = min(h.avg / HITTER_CEILING_AVG, 1.0)
    norm_hr = min(h.home_runs / HITTER_CEILING_HR, 1.0)
    norm_rbi = min(h.rbi / HITTER_CEILING_RBI, 1.0)
    return HITTER_W_AVG * norm_avg + HITTER_W_HR * norm_hr + HITTER_W_RBI * norm_rbi


def _starter_composite(s: RollingStarterStats) -> float:
    """Composite score for ranking starting pitchers.  Higher = hotter.

    Formula: 0.40 × norm_inv_era + 0.35 × norm_k9 + 0.25 × norm_inv_whip

    ERA and WHIP are inverted (lower is better → higher composite).
    Values worse than the ceiling score 0; values at or better than 0
    score the maximum for that component.
    """
    norm_era = max(1.0 - s.era / PITCHER_CEILING_ERA, 0.0)
    norm_k9 = min(s.k_per_9 / PITCHER_CEILING_K9, 1.0)
    norm_whip = max(1.0 - s.whip / PITCHER_CEILING_WHIP, 0.0)
    return STARTER_W_ERA * norm_era + STARTER_W_K9 * norm_k9 + STARTER_W_WHIP * norm_whip


def _closer_composite(c: RollingCloserStats) -> float:
    """Composite score for ranking closers/relievers.  Higher = hotter.

    Formula: 0.35 × norm_inv_era + 0.40 × sv_pct + 0.25 × norm_k9

    Save percentage is the primary signal for closers.  K/9 is computed
    inline from strikeouts and innings pitched (not a property on the
    dataclass since closers don't expose it separately).
    """
    norm_era = max(1.0 - c.era / PITCHER_CEILING_ERA, 0.0)
    ip = c.innings_pitched
    k9 = (c.strikeouts * 9) / ip if ip > 0 else 0.0
    norm_k9 = min(k9 / PITCHER_CEILING_K9, 1.0)
    return CLOSER_W_ERA * norm_era + CLOSER_W_SV_PCT * c.save_pct + CLOSER_W_K9 * norm_k9


# ---------------------------------------------------------------------------
# Luck filter
# ---------------------------------------------------------------------------


def _hitter_luck(
    full_name: str,
    statcast: dict[str, StatcastHitter],
    *,
    is_hot: bool,
) -> tuple[LuckStatus, float | None, float | None]:
    """Determine a hitter's luck status from Statcast data.

    Returns (status, xwoba, barrel_pct).  xwoba and barrel_pct are None
    when the player has no Statcast entry.
    """
    sc = statcast.get(full_name)
    if sc is None:
        return LuckStatus.UNCONFIRMED, None, None

    quality_contact = sc.xwoba >= LUCK_XWOBA_THRESHOLD
    if is_hot:
        status = LuckStatus.CONFIRMED if quality_contact else LuckStatus.LUCKY
    else:
        status = LuckStatus.UNLUCKY if quality_contact else LuckStatus.CONFIRMED
    return status, sc.xwoba, sc.barrel_pct


def _pitcher_luck(
    full_name: str,
    statcast: dict[str, StatcastPitcher],
    *,
    is_hot: bool,
) -> tuple[LuckStatus, float | None, float | None]:
    """Determine a pitcher's luck status from Statcast data.

    Returns (status, fip, xera).  Both are None when the player has no
    Statcast entry.

    For pitchers, "quality" means low FIP (pitcher controls outcomes well).
    A hot pitcher (low ERA) with quality pitching (low FIP) is CONFIRMED;
    a hot pitcher with poor FIP is LUCKY.
    """
    sc = statcast.get(full_name)
    if sc is None:
        return LuckStatus.UNCONFIRMED, None, None

    quality_pitching = sc.fip <= LUCK_FIP_THRESHOLD
    if is_hot:
        status = LuckStatus.CONFIRMED if quality_pitching else LuckStatus.LUCKY
    else:
        status = LuckStatus.UNLUCKY if quality_pitching else LuckStatus.CONFIRMED
    return status, sc.fip, sc.xera


# ---------------------------------------------------------------------------
# Leaderboard entry builders
# ---------------------------------------------------------------------------


def _build_hitter_entry(
    h: RollingHitterStats,
    composite: float,
    luck_status: LuckStatus,
    xwoba: float | None,
    barrel_pct: float | None,
) -> LeaderboardHitter:
    return LeaderboardHitter(
        player_id=h.player_id,
        full_name=h.full_name,
        team_abbr=h.team_abbr,
        games=h.games,
        plate_appearances=h.plate_appearances,
        avg=h.avg,
        obp=h.obp,
        slg=h.slg,
        home_runs=h.home_runs,
        rbi=h.rbi,
        stolen_bases=h.stolen_bases,
        composite_score=composite,
        luck_status=luck_status,
        xwoba=xwoba,
        barrel_pct=barrel_pct,
    )


def _build_starter_entry(
    s: RollingStarterStats,
    composite: float,
    luck_status: LuckStatus,
    fip: float | None,
    xera: float | None,
) -> LeaderboardPitcher:
    return LeaderboardPitcher(
        player_id=s.player_id,
        full_name=s.full_name,
        team_abbr=s.team_abbr,
        role="starter",
        era=s.era,
        innings_pitched=s.innings_pitched,
        strikeouts=s.strikeouts,
        composite_score=composite,
        luck_status=luck_status,
        whip=s.whip,
        k_per_9=s.k_per_9,
        starts=s.starts,
        fip=fip,
        xera=xera,
    )


def _build_closer_entry(
    c: RollingCloserStats,
    composite: float,
    luck_status: LuckStatus,
    fip: float | None,
    xera: float | None,
) -> LeaderboardPitcher:
    return LeaderboardPitcher(
        player_id=c.player_id,
        full_name=c.full_name,
        team_abbr=c.team_abbr,
        role="closer",
        era=c.era,
        innings_pitched=c.innings_pitched,
        strikeouts=c.strikeouts,
        composite_score=composite,
        luck_status=luck_status,
        saves=c.saves,
        blown_saves=c.blown_saves,
        save_pct=c.save_pct,
        holds=c.holds,
        appearances=c.appearances,
        fip=fip,
        xera=xera,
    )


# ---------------------------------------------------------------------------
# Internal leaderboard assembly
# ---------------------------------------------------------------------------


def _score_and_rank_hitters(
    hitters: dict[int, RollingHitterStats],
    statcast: dict[str, StatcastHitter],
    *,
    size: int,
    cold_max: float = COLD_HITTER_COMPOSITE_MAX,
) -> tuple[list[LeaderboardHitter], list[LeaderboardHitter]]:
    """Score all qualified hitters and return (hot, cold) lists.

    Hot = top *size* by composite (descending).
    Cold = worst *size* by composite (ascending), restricted to hitters NOT on
    the hot list whose composite is at or below ``cold_max``.  The absolute gate
    means "cold" reflects genuinely poor performance rather than the relative
    bottom of a thin pool; the cold list may be shorter than *size* or empty.
    """
    scored: list[tuple[float, RollingHitterStats]] = [
        (_hitter_composite(h), h) for h in hitters.values()
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    hot: list[LeaderboardHitter] = []
    for composite, h in scored[:size]:
        status, xwoba, barrel_pct = _hitter_luck(h.full_name, statcast, is_hot=True)
        hot.append(_build_hitter_entry(h, composite, status, xwoba, barrel_pct))

    hot_ids = {e.player_id for e in hot}

    # Cold list: worst performers not already on the hot list AND genuinely cold
    # (composite at or below the absolute gate).  No fallback to the full pool —
    # hot and cold are disjoint by construction, and a player can only be cold if
    # their line is actually poor.
    cold_candidates = [
        (c, h) for c, h in scored
        if h.player_id not in hot_ids and c <= cold_max
    ]
    cold_candidates.sort(key=lambda x: x[0])  # ascending = worst first
    cold: list[LeaderboardHitter] = []
    for composite, h in cold_candidates[:size]:
        status, xwoba, barrel_pct = _hitter_luck(h.full_name, statcast, is_hot=False)
        cold.append(_build_hitter_entry(h, composite, status, xwoba, barrel_pct))

    return hot, cold


def _score_and_rank_pitchers(
    starters: dict[int, RollingStarterStats],
    closers: dict[int, RollingCloserStats],
    statcast: dict[str, StatcastPitcher],
    *,
    size: int,
    cold_max: float = COLD_PITCHER_COMPOSITE_MAX,
) -> tuple[list[LeaderboardPitcher], list[LeaderboardPitcher]]:
    """Score all qualified pitchers (starters + closers) and return (hot, cold).

    Starters and closers use different composite formulas but the scores are on
    the same [0, 1] scale, allowing a merged ranking.

    A swingman can appear in both the starter and closer pools for the same
    rolling window (a start one day, a save/hold in relief another).  Those are
    collapsed to a single entry per ``player_id`` — the higher-scoring role,
    which best represents the pitcher's contribution — so nobody is listed
    twice.  Cold = worst *size* not on the hot list whose composite is at or
    below ``cold_max``; the gate keeps genuinely good pitchers off the cold
    board in thin pools.  The cold list may be shorter than *size* or empty.
    """
    # Build one (hot-luck, cold-luck) entry pair per player_id, keeping the
    # higher-scoring role for swingmen.  The composite is identical regardless
    # of is_hot (only the luck label differs), so the role choice is stable.
    best: dict[int, tuple[float, LeaderboardPitcher, LeaderboardPitcher]] = {}

    def _consider(
        player_id: int,
        composite: float,
        hot_entry: LeaderboardPitcher,
        cold_entry: LeaderboardPitcher,
    ) -> None:
        existing = best.get(player_id)
        if existing is None or composite > existing[0]:
            best[player_id] = (composite, hot_entry, cold_entry)

    for s in starters.values():
        composite = _starter_composite(s)
        hot_status, fip, xera = _pitcher_luck(s.full_name, statcast, is_hot=True)
        cold_status, _, _ = _pitcher_luck(s.full_name, statcast, is_hot=False)
        _consider(
            s.player_id, composite,
            _build_starter_entry(s, composite, hot_status, fip, xera),
            _build_starter_entry(s, composite, cold_status, fip, xera),
        )

    for c in closers.values():
        composite = _closer_composite(c)
        hot_status, fip, xera = _pitcher_luck(c.full_name, statcast, is_hot=True)
        cold_status, _, _ = _pitcher_luck(c.full_name, statcast, is_hot=False)
        _consider(
            c.player_id, composite,
            _build_closer_entry(c, composite, hot_status, fip, xera),
            _build_closer_entry(c, composite, cold_status, fip, xera),
        )

    scored = sorted(best.values(), key=lambda x: x[0], reverse=True)

    hot: list[LeaderboardPitcher] = [hot_entry for _, hot_entry, _ in scored[:size]]
    hot_ids = {e.player_id for e in hot}

    # Cold pitchers: worst not already on the hot list AND genuinely cold
    # (composite at or below the absolute gate).  No fallback to the full pool.
    cold_candidates = [
        (composite, cold_entry)
        for composite, _, cold_entry in scored
        if cold_entry.player_id not in hot_ids and composite <= cold_max
    ]
    cold_candidates.sort(key=lambda x: x[0])  # ascending = worst first
    cold: list[LeaderboardPitcher] = [entry for _, entry in cold_candidates[:size]]

    return hot, cold


# ---------------------------------------------------------------------------
# Breakout detection
# ---------------------------------------------------------------------------


def _detect_breakout_hitters(
    hot_7d: list[LeaderboardHitter],
    rolling_15d: RollingStats,
    statcast: dict[str, StatcastHitter],
) -> list[LeaderboardHitter]:
    """Identify breakout hitters: 7-day hot AND 15-day composite above median.

    A breakout player is sustaining elevated performance, not just having
    a single hot week.  The 15-day median serves as a self-calibrating
    threshold that adapts to overall league offense levels.
    """
    if not rolling_15d.hitters:
        return []

    # Compute 15-day composites for all qualified hitters
    composites_15d: dict[int, float] = {
        pid: _hitter_composite(h) for pid, h in rolling_15d.hitters.items()
    }
    all_composites = list(composites_15d.values())
    median_15d = median(all_composites) if all_composites else 0.0

    breakouts: list[LeaderboardHitter] = []
    for entry in hot_7d:
        c15 = composites_15d.get(entry.player_id)
        if c15 is not None and c15 >= median_15d:
            # Re-apply luck filter (same as hot — breakout is a confirmed hot streak)
            status, xwoba, barrel_pct = _hitter_luck(
                entry.full_name, statcast, is_hot=True,
            )
            breakouts.append(_build_hitter_entry(
                rolling_15d.hitters[entry.player_id],
                c15,
                status,
                xwoba,
                barrel_pct,
            ))
    return breakouts


def _detect_breakout_pitchers(
    hot_7d: list[LeaderboardPitcher],
    rolling_15d: RollingStats,
    statcast: dict[str, StatcastPitcher],
) -> list[LeaderboardPitcher]:
    """Identify breakout pitchers: 7-day hot AND 15-day composite above median.

    Works across both starters and closers.
    """
    # Compute 15-day composites for all qualified pitchers
    composites_15d: dict[int, float] = {}
    for pid, s in rolling_15d.starters.items():
        composites_15d[pid] = _starter_composite(s)
    for pid, c in rolling_15d.closers.items():
        composites_15d[pid] = _closer_composite(c)

    if not composites_15d:
        return []

    all_composites = list(composites_15d.values())
    median_15d = median(all_composites) if all_composites else 0.0

    breakouts: list[LeaderboardPitcher] = []
    for entry in hot_7d:
        c15 = composites_15d.get(entry.player_id)
        if c15 is not None and c15 >= median_15d:
            status, fip, xera = _pitcher_luck(
                entry.full_name, statcast, is_hot=True,
            )
            # Build entry from the 15-day rolling stats
            if entry.player_id in rolling_15d.starters:
                s = rolling_15d.starters[entry.player_id]
                breakouts.append(_build_starter_entry(s, c15, status, fip, xera))
            elif entry.player_id in rolling_15d.closers:
                c = rolling_15d.closers[entry.player_id]
                breakouts.append(_build_closer_entry(c, c15, status, fip, xera))
    return breakouts


# ---------------------------------------------------------------------------
# Public API — leaderboard scoring
# ---------------------------------------------------------------------------


def score_leaderboards(
    rolling_7d: RollingStats,
    rolling_15d: RollingStats,
    statcast_hitters: dict[str, StatcastHitter],
    statcast_pitchers: dict[str, StatcastPitcher],
    *,
    leaderboard_size: int = LEADERBOARD_SIZE,
) -> Leaderboards:
    """Score, rank, and annotate all six V2 leaderboards.

    Parameters
    ----------
    rolling_7d
        7-day rolling stats (qualified players only), from
        ``compute_rolling_stats``.
    rolling_15d
        15-day rolling stats, used for breakout detection.
    statcast_hitters, statcast_pitchers
        Season-level Statcast lookups keyed by player name.  Pass empty
        dicts when Statcast data is unavailable — all players receive
        ``UNCONFIRMED`` luck status.
    leaderboard_size
        Maximum entries per leaderboard list.

    Returns
    -------
    Leaderboards
        Six ranked lists plus snapshot counts for both windows.
    """
    hot_hitters, cold_hitters = _score_and_rank_hitters(
        rolling_7d.hitters, statcast_hitters, size=leaderboard_size,
    )
    hot_pitchers, cold_pitchers = _score_and_rank_pitchers(
        rolling_7d.starters, rolling_7d.closers, statcast_pitchers,
        size=leaderboard_size,
    )
    breakout_hitters = _detect_breakout_hitters(
        hot_hitters, rolling_15d, statcast_hitters,
    )
    breakout_pitchers = _detect_breakout_pitchers(
        hot_pitchers, rolling_15d, statcast_pitchers,
    )

    logger.info(
        "Leaderboards: %d hot / %d cold hitters, %d hot / %d cold pitchers, "
        "%d breakout hitters, %d breakout pitchers",
        len(hot_hitters), len(cold_hitters),
        len(hot_pitchers), len(cold_pitchers),
        len(breakout_hitters), len(breakout_pitchers),
    )

    return Leaderboards(
        hot_hitters=hot_hitters,
        cold_hitters=cold_hitters,
        hot_pitchers=hot_pitchers,
        cold_pitchers=cold_pitchers,
        breakout_hitters=breakout_hitters,
        breakout_pitchers=breakout_pitchers,
        snapshots_7d=rolling_7d.snapshots_used,
        snapshots_15d=rolling_15d.snapshots_used,
    )
