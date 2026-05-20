"""Statcast advanced-stats fetcher — wraps pybaseball for the V2 luck filter.

The luck filter compares traditional rolling stats (from daily snapshots)
against Statcast quality-of-contact metrics to classify streaks as
``CONFIRMED``, ``LUCKY``, ``UNLUCKY``, or ``UNCONFIRMED``.  This module
fetches season-level Statcast leaderboards from FanGraphs via ``pybaseball``.

**Graceful degradation:** if pybaseball fails (scraping errors, rate
limits, missing data), both fetch functions return empty dicts and the
pipeline continues with ``UNCONFIRMED`` luck status for all players.
pybaseball is lazy-imported so the pipeline survives even if the library
is missing or broken at import time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses — season-level Statcast metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatcastHitter:
    """Season-level Statcast quality-of-contact metrics for one hitter.

    Used by the luck filter to confirm or flag traditional-stat streaks.
    """

    name: str
    team: str
    xwoba: float  # Expected weighted on-base average
    barrel_pct: float  # Barrel rate (%)
    hard_hit_pct: float  # Hard-hit rate (%)


@dataclass(frozen=True)
class StatcastPitcher:
    """Season-level Statcast metrics for one pitcher.

    FIP / xFIP / xERA assess a pitcher's "true" performance quality,
    independent of defense and sequencing.  Used by the luck filter to
    confirm or flag traditional-stat streaks for starters and closers.
    """

    name: str
    team: str
    fip: float  # Fielding Independent Pitching
    xfip: float  # Expected FIP
    xera: float  # Expected ERA
    barrel_pct: float  # Barrel rate allowed (%)
    hard_hit_pct: float  # Hard-hit rate allowed (%)


# ---------------------------------------------------------------------------
# DataFrame → dataclass parsing helpers
# ---------------------------------------------------------------------------

# Column names as they appear in pybaseball / FanGraphs DataFrames.
_HITTER_REQUIRED_COLS = {"Name", "Team", "xwOBA", "Barrel%", "HardHit%"}
_PITCHER_REQUIRED_COLS = {"Name", "Team", "FIP", "xFIP", "xERA", "Barrel%", "HardHit%"}


def _safe_float(value: object) -> float | None:
    """Convert a value to float, returning None for NaN or unparseable values."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if f != f:  # NaN check (math.isnan without importing math)
        return None
    return f


def _parse_hitter_row(row: dict) -> StatcastHitter | None:
    """Parse one row of FanGraphs batting data into a StatcastHitter.

    Returns None if any required numeric field is missing, unparseable,
    or NaN.  Players with incomplete Statcast data are excluded from
    the lookup; the luck filter will assign ``UNCONFIRMED`` for them.
    """
    try:
        name = str(row["Name"])
        team = str(row["Team"])
    except (KeyError, TypeError):
        return None

    xwoba = _safe_float(row.get("xwOBA"))
    barrel_pct = _safe_float(row.get("Barrel%"))
    hard_hit_pct = _safe_float(row.get("HardHit%"))

    if xwoba is None or barrel_pct is None or hard_hit_pct is None:
        return None

    return StatcastHitter(
        name=name, team=team,
        xwoba=xwoba, barrel_pct=barrel_pct, hard_hit_pct=hard_hit_pct,
    )


def _parse_pitcher_row(row: dict) -> StatcastPitcher | None:
    """Parse one row of FanGraphs pitching data into a StatcastPitcher.

    Same contract as :func:`_parse_hitter_row` — NaN fields cause the
    row to be skipped.
    """
    try:
        name = str(row["Name"])
        team = str(row["Team"])
    except (KeyError, TypeError):
        return None

    fip = _safe_float(row.get("FIP"))
    xfip = _safe_float(row.get("xFIP"))
    xera = _safe_float(row.get("xERA"))
    barrel_pct = _safe_float(row.get("Barrel%"))
    hard_hit_pct = _safe_float(row.get("HardHit%"))

    if any(v is None for v in (fip, xfip, xera, barrel_pct, hard_hit_pct)):
        return None

    return StatcastPitcher(
        name=name, team=team,
        fip=fip, xfip=xfip, xera=xera,  # type: ignore[arg-type]
        barrel_pct=barrel_pct, hard_hit_pct=hard_hit_pct,  # type: ignore[arg-type]
    )


def parse_hitter_dataframe(df: object) -> dict[str, StatcastHitter]:
    """Convert a FanGraphs batting DataFrame into a name-keyed lookup.

    Accepts ``object`` so callers don't need pandas imported to type-check.
    Returns an empty dict if the DataFrame is empty or missing required
    columns.  Rows with unparseable values are silently skipped.
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}

    if not _HITTER_REQUIRED_COLS.issubset(df.columns):
        missing = _HITTER_REQUIRED_COLS - set(df.columns)
        logger.warning("Missing columns in FanGraphs batting data: %s", missing)
        return {}

    result: dict[str, StatcastHitter] = {}
    for _, row in df.iterrows():
        parsed = _parse_hitter_row(row)
        if parsed is not None:
            result[parsed.name] = parsed

    return result


def parse_pitcher_dataframe(df: object) -> dict[str, StatcastPitcher]:
    """Convert a FanGraphs pitching DataFrame into a name-keyed lookup.

    Same contract as :func:`parse_hitter_dataframe`.
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}

    if not _PITCHER_REQUIRED_COLS.issubset(df.columns):
        missing = _PITCHER_REQUIRED_COLS - set(df.columns)
        logger.warning("Missing columns in FanGraphs pitching data: %s", missing)
        return {}

    result: dict[str, StatcastPitcher] = {}
    for _, row in df.iterrows():
        parsed = _parse_pitcher_row(row)
        if parsed is not None:
            result[parsed.name] = parsed

    return result


# ---------------------------------------------------------------------------
# Public fetch API
# ---------------------------------------------------------------------------


def fetch_statcast_hitters(season: int) -> dict[str, StatcastHitter]:
    """Fetch Statcast hitter metrics for *season* from FanGraphs.

    Returns a dict keyed by player name (matching the names used in
    snapshot dataclasses).  Returns an empty dict on any failure —
    import errors, network issues, scraping changes, or empty data.

    Uses ``qual=0`` (no minimum plate appearances) so players early in
    the season or recently called up are still available for the luck
    filter.  Players without Statcast data simply won't appear in the
    dict, and the luck filter will assign ``UNCONFIRMED``.
    """
    try:
        from pybaseball import fg_batting_data
    except ImportError:
        logger.warning("pybaseball not installed; skipping Statcast hitter fetch")
        return {}

    try:
        df = fg_batting_data(start_season=season, end_season=season, qual=0)
    except Exception:
        logger.warning(
            "Failed to fetch FanGraphs batting data for %d", season, exc_info=True
        )
        return {}

    result = parse_hitter_dataframe(df)
    if result:
        logger.info("Fetched Statcast data for %d hitters (season %d)", len(result), season)
    return result


def fetch_statcast_pitchers(season: int) -> dict[str, StatcastPitcher]:
    """Fetch Statcast pitcher metrics for *season* from FanGraphs.

    Same contract as :func:`fetch_statcast_hitters` — returns an empty
    dict on any failure, keyed by player name.
    """
    try:
        from pybaseball import fg_pitching_data
    except ImportError:
        logger.warning("pybaseball not installed; skipping Statcast pitcher fetch")
        return {}

    try:
        df = fg_pitching_data(start_season=season, end_season=season, qual=0)
    except Exception:
        logger.warning(
            "Failed to fetch FanGraphs pitching data for %d", season, exc_info=True
        )
        return {}

    result = parse_pitcher_dataframe(df)
    if result:
        logger.info("Fetched Statcast data for %d pitchers (season %d)", len(result), season)
    return result
