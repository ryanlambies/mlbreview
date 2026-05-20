"""Daily stat snapshot persistence — write/read player stats as JSON files.

V2 rolling-window leaderboards (hot/cold/breakout) need historical data.
The pipeline is stateless, so each run writes a JSON snapshot to
``public/snapshots/YYYY-MM-DD.json`` on the gh-pages branch.  The
leaderboard code loads the last N snapshots and computes rolling aggregates.

Snapshot files are never pruned — ~80KB/day, ~14MB/season, well within
GitHub Pages' 1GB limit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses — one day's worth of player stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HitterDayStats:
    """One hitter's counting stats for a single day."""

    player_id: int
    full_name: str
    team_abbr: str
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


@dataclass(frozen=True)
class StarterGameStats:
    """One starting pitcher's stats for a single start.

    Starters are tracked per-start (not per-day) because their evaluation
    window is "last 2-3 starts," not a rolling day count.  ``outs_recorded``
    stores total outs instead of the MLB ``6.1`` notation (which means
    6 and 1/3 innings, not 6.1).  Convert to innings with ``outs / 3``.
    """

    player_id: int
    full_name: str
    team_abbr: str
    game_date: str  # YYYY-MM-DD
    opponent_abbr: str
    outs_recorded: int  # 18 = 6.0 IP, 19 = 6.1 IP, etc.
    hits_allowed: int
    earned_runs: int
    walks: int
    strikeouts: int
    home_runs_allowed: int
    pitches_thrown: int

    @property
    def innings_pitched(self) -> float:
        """Innings pitched as a decimal (e.g. 19 outs → 6.333...)."""
        return self.outs_recorded / 3


@dataclass(frozen=True)
class CloserDayStats:
    """One closer/reliever's stats for a single day."""

    player_id: int
    full_name: str
    team_abbr: str
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


@dataclass(frozen=True)
class DailySnapshot:
    """One day's complete snapshot of player stats across the league."""

    snapshot_date: str  # YYYY-MM-DD
    hitters: tuple[HitterDayStats, ...]
    starters: tuple[StarterGameStats, ...]
    closers: tuple[CloserDayStats, ...]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _snapshot_to_dict(snapshot: DailySnapshot) -> dict[str, Any]:
    """Convert a snapshot to a JSON-serializable dict."""
    return {
        "snapshot_date": snapshot.snapshot_date,
        "hitters": [asdict(h) for h in snapshot.hitters],
        "starters": [asdict(s) for s in snapshot.starters],
        "closers": [asdict(c) for c in snapshot.closers],
    }


def _snapshot_from_dict(data: dict[str, Any]) -> DailySnapshot:
    """Reconstruct a snapshot from a parsed JSON dict."""
    return DailySnapshot(
        snapshot_date=data["snapshot_date"],
        hitters=tuple(HitterDayStats(**h) for h in data.get("hitters", [])),
        starters=tuple(StarterGameStats(**s) for s in data.get("starters", [])),
        closers=tuple(CloserDayStats(**c) for c in data.get("closers", [])),
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def snapshot_path(base_dir: Path, snapshot_date: date) -> Path:
    """Return the canonical path for a snapshot file."""
    return base_dir / "snapshots" / f"{snapshot_date.isoformat()}.json"


def write_snapshot(snapshot: DailySnapshot, *, base_dir: Path) -> Path:
    """Write a snapshot to disk as JSON.

    Creates the ``snapshots/`` directory if it doesn't exist.
    Returns the path of the written file.
    """
    d = date.fromisoformat(snapshot.snapshot_date)
    path = snapshot_path(base_dir, d)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = _snapshot_to_dict(snapshot)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    logger.info("Wrote snapshot to %s (%d hitters, %d starters, %d closers)",
                path, len(snapshot.hitters), len(snapshot.starters), len(snapshot.closers))
    return path


def load_snapshot(path: Path) -> DailySnapshot:
    """Load a single snapshot from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return _snapshot_from_dict(data)


def load_snapshots(*, base_dir: Path, n_days: int) -> list[DailySnapshot]:
    """Load the most recent *n_days* snapshots, sorted newest-first.

    Scans ``base_dir/snapshots/`` for date-named JSON files, picks the
    most recent *n_days*, and returns them in reverse-chronological order.
    Missing days (off-days, early season) are simply absent — callers
    must tolerate gaps.
    """
    snapshots_dir = base_dir / "snapshots"
    if not snapshots_dir.exists():
        return []

    # Collect all valid snapshot files
    files: list[tuple[date, Path]] = []
    for path in snapshots_dir.iterdir():
        if not path.suffix == ".json":
            continue
        try:
            d = date.fromisoformat(path.stem)
        except ValueError:
            continue
        files.append((d, path))

    # Sort by date descending and take the most recent n_days
    files.sort(key=lambda x: x[0], reverse=True)
    files = files[:n_days]

    snapshots: list[DailySnapshot] = []
    for _, path in files:
        try:
            snapshots.append(load_snapshot(path))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Skipping corrupt snapshot %s: %s", path, exc)

    return snapshots
