"""Backfill immutable per-day ``data.json`` from existing daily snapshots.

The dashboard's day-over-day trends (rank deltas, OPS sparklines) live in
``dashboard.json``, which the bundle builder derives from a trailing window of
per-day ``data.json`` files. But the data layer only began emitting
``data.json`` on its launch day, so the bundle had a single day of history and
every trend rendered as NEW / "building up data".

The daily stat snapshots (``snapshots/<date>.json``) that feed the leaderboards
reach back further than the first ``data.json``. This one-time (and safely
repeatable) tool reconstructs each historical day's leaderboards from those
snapshots and writes a faithful ``data.json`` for it, then rebuilds
``index.json`` and ``dashboard.json`` so the next bundle carries a full window.

What is reconstructed vs. omitted
---------------------------------
- **leaderboards** — fully recomputed via the *same* code path as the live
  pipeline (``compute_rolling_stats`` → ``score_leaderboards``). Board
  membership and ranks are identical to what the live run would have produced:
  composite scoring and qualification never use Statcast — only the luck
  *label* does, and the bundle never reads that label for non-latest days. So
  Statcast is skipped entirely (offline, fast, deterministic) and reconstructed
  rows carry ``UNCONFIRMED`` luck status.
- **scores / storylines / tonight / transactions** — left empty. The bundle
  reads these only from the *latest* day (a real, live ``data.json``), never
  from the trailing history, so empty historical sections are invisible in the
  dashboard UI. Reconstructing them would require re-fetching game feeds and
  re-running the LLM per day for zero visible benefit.

Safety
------
``write_data_json`` is write-once: an existing ``data.json`` is never
overwritten, so the real live records (and any prior backfill) are untouched
and re-running is idempotent. The snapshot files are read-only inputs.

Usage
-----
    python scripts/backfill_data_json.py <base_dir> [--through DATE] [--limit-days N]

``base_dir`` is the published tree containing ``snapshots/<date>.json`` and
``digests/`` (i.e. a checkout of the gh-pages branch, or the local ``public/``).
``--through`` caps the newest date to backfill (default: all available).
``--limit-days`` backfills only the most recent N snapshot dates (default: all).
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from mlbreview.config import BREAKOUT_WINDOW_DAYS, ROLLING_WINDOW_DAYS
from mlbreview.data.digest_data import (
    data_json_path,
    write_data_json,
    write_index_json,
)
from mlbreview.data.snapshots import DailySnapshot, load_snapshot
from mlbreview.render.pages import Digest
from mlbreview.scoring.leaderboards import compute_rolling_stats, score_leaderboards

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUNDLE_SCRIPT = _REPO_ROOT / "scripts" / "build_dashboard_bundle.py"


def _snapshot_dates(base_dir: Path) -> list[date]:
    """All snapshot dates present under ``base_dir/snapshots/``, oldest-first."""
    snaps_dir = base_dir / "snapshots"
    if not snaps_dir.exists():
        return []
    dates: list[date] = []
    for path in snaps_dir.iterdir():
        if path.suffix != ".json":
            continue
        try:
            dates.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    dates.sort()
    return dates


def load_snapshots_asof(base_dir: Path, asof: date, n_days: int) -> list[DailySnapshot]:
    """Load the most recent *n_days* snapshots dated on or before *asof*.

    Returns them newest-first, matching what ``load_snapshots(n_days=...)`` would
    have returned on the run for *asof* — except anchored to a historical date
    instead of "now". Missing days (off-days) are simply absent; the window may
    therefore contain fewer than *n_days* snapshots, exactly as during normal
    early-season or post-off-day operation.
    """
    snaps_dir = base_dir / "snapshots"
    if not snaps_dir.exists():
        return []
    candidates: list[tuple[date, Path]] = []
    for path in snaps_dir.iterdir():
        if path.suffix != ".json":
            continue
        try:
            d = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if d <= asof:
            candidates.append((d, path))
    candidates.sort(key=lambda x: x[0], reverse=True)
    candidates = candidates[:n_days]

    snapshots: list[DailySnapshot] = []
    for _, path in candidates:
        try:
            snapshots.append(load_snapshot(path))
        except Exception:
            logger.warning("Skipping unreadable snapshot %s", path, exc_info=True)
    return snapshots


def _reconstruct_digest(base_dir: Path, target: date) -> Digest:
    """Recompute a historical day's leaderboards into a leaderboards-only Digest.

    Statcast is intentionally empty (see module docstring): it never affects
    board membership or rank, only an UNCONFIRMED-vs-other luck label the bundle
    ignores for non-latest days.
    """
    snaps_7d = load_snapshots_asof(base_dir, target, ROLLING_WINDOW_DAYS)
    snaps_15d = load_snapshots_asof(base_dir, target, BREAKOUT_WINDOW_DAYS)
    rolling_7d = compute_rolling_stats(snaps_7d)
    rolling_15d = compute_rolling_stats(snaps_15d)
    leaderboards = score_leaderboards(rolling_7d, rolling_15d, {}, {})
    return Digest(digest_date=target, is_off_day=False, leaderboards=leaderboards)


def _rebuild_bundle(base_dir: Path) -> None:
    """Rebuild ``dashboard.json`` by importing the standalone bundle builder."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_dashboard_bundle", _BUNDLE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.write_bundle(base_dir)


def backfill(
    base_dir: Path,
    *,
    through: date | None = None,
    limit_days: int | None = None,
    generated_at: str | None = None,
) -> list[date]:
    """Backfill ``data.json`` for every snapshot date lacking one.

    Returns the list of dates newly written (oldest-first). Rebuilds
    ``index.json`` and ``dashboard.json`` afterwards when anything was written.
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    dates = _snapshot_dates(base_dir)
    if through is not None:
        dates = [d for d in dates if d <= through]
    if limit_days is not None:
        dates = dates[-limit_days:]

    written: list[date] = []
    skipped_existing = 0
    for d in dates:
        if data_json_path(base_dir, d.isoformat()).exists():
            skipped_existing += 1
            continue
        digest = _reconstruct_digest(base_dir, d)
        path = write_data_json(digest, base_dir=base_dir, generated_at=generated_at)
        if path is not None:
            written.append(d)

    logger.info(
        "Backfill: %d snapshot dates scanned, %d written, %d already had data.json",
        len(dates), len(written), skipped_existing,
    )

    if written:
        write_index_json(base_dir, updated=generated_at)
        _rebuild_bundle(base_dir)
        logger.info("Rebuilt index.json and dashboard.json")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill data.json from snapshots.")
    parser.add_argument("base_dir", type=Path, help="Published tree (gh-pages checkout or public/)")
    parser.add_argument(
        "--through",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Only backfill dates on or before this ISO date.",
    )
    parser.add_argument(
        "--limit-days",
        type=int,
        default=None,
        help="Only backfill the most recent N snapshot dates.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    written = backfill(args.base_dir, through=args.through, limit_days=args.limit_days)
    if written:
        print(f"Wrote {len(written)} data.json files: {written[0]} … {written[-1]}")
    else:
        print("Nothing to backfill — every snapshot date already has data.json.")


if __name__ == "__main__":
    main()
