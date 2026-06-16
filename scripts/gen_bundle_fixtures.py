"""Generate the real multi-day data.json corpus used by the bundle-builder tests.

Run ONCE (with network) to produce committed fixtures; CI then replays the
committed data.json files offline. The corpus is REAL: daily snapshots are
captured from the public MLB Stats API (no auth, no LLM, no Statcast), scored
through the same leaderboard path the pipeline uses, and serialized with the
U2 data.json serializer. Rank movement across days reflects real performance —
the bundle-builder tests pin specific deltas inspected from this output.

Usage:
    python scripts/gen_bundle_fixtures.py

Idempotent-ish: re-capturing a snapshot that already exists in the fixtures dir
is skipped; data.json files are regenerated.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from mlbreview.config import BREAKOUT_WINDOW_DAYS, ROLLING_WINDOW_DAYS
from mlbreview.data.client import make_client
from mlbreview.data.gamelogs import fetch_daily_gamelogs
from mlbreview.data.schedule import fetch_finals
from mlbreview.data.snapshots import (
    DailySnapshot,
    load_snapshot,
    snapshot_path,
    write_snapshot,
)
from mlbreview.data.digest_data import write_data_json, write_index_json
from mlbreview.render.pages import Digest
from mlbreview.scoring.leaderboards import compute_rolling_stats, score_leaderboards

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "bundle"

# Dates to capture fresh from the API.
CAPTURE_DATES = ["2026-06-11", "2026-06-12", "2026-06-13"]
# An existing real snapshot to fold in (captured by a prior production run).
SEED_SNAPSHOT = REPO_ROOT / "public" / "snapshots" / "2026-06-14.json"
# "As-of" days to emit data.json for (each scored from its trailing window).
AS_OF_DATES = ["2026-06-12", "2026-06-13", "2026-06-14"]

GENERATED_AT = "2026-06-15T16:05:00Z"


def _capture_snapshot(d: str) -> None:
    dest = snapshot_path(FIXTURE_DIR, date.fromisoformat(d))
    if dest.exists():
        print(f"  snapshot {d} already present — skip")
        return
    with make_client() as client:
        finals = fetch_finals(date.fromisoformat(d), client=client)
        game_pks = [g.gamePk for g in finals]
        hitters, starters, closers = fetch_daily_gamelogs(
            game_pks, client=client, game_date=d
        )
    snap = DailySnapshot(
        snapshot_date=d,
        hitters=tuple(hitters),
        starters=tuple(starters),
        closers=tuple(closers),
    )
    write_snapshot(snap, base_dir=FIXTURE_DIR)
    print(f"  captured {d}: {len(finals)} games, {len(hitters)} hitters")


def _load_all_snapshots() -> dict[str, DailySnapshot]:
    snaps: dict[str, DailySnapshot] = {}
    for path in (FIXTURE_DIR / "snapshots").glob("*.json"):
        snaps[path.stem] = load_snapshot(path)
    return snaps


def _emit_data_json(as_of: str, snaps: dict[str, DailySnapshot]) -> None:
    keys = sorted((d for d in snaps if d <= as_of), reverse=True)
    window_7d = [snaps[d] for d in keys[:ROLLING_WINDOW_DAYS]]
    window_15d = [snaps[d] for d in keys[:BREAKOUT_WINDOW_DAYS]]
    rolling_7d = compute_rolling_stats(window_7d)
    rolling_15d = compute_rolling_stats(window_15d)
    boards = score_leaderboards(rolling_7d, rolling_15d, {}, {})
    digest = Digest(digest_date=date.fromisoformat(as_of), leaderboards=boards)
    # Remove any prior data.json so the write-once writer regenerates it.
    dest = FIXTURE_DIR / "digests" / as_of / "data.json"
    if dest.exists():
        dest.unlink()
    write_data_json(digest, base_dir=FIXTURE_DIR, generated_at=GENERATED_AT)
    print(
        f"  emitted {as_of}: "
        f"{len(boards.hot_hitters)} hot H, {len(boards.hot_pitchers)} hot P, "
        f"{len(boards.breakout_hitters)} breakout H"
    )


def main() -> None:
    (FIXTURE_DIR / "snapshots").mkdir(parents=True, exist_ok=True)

    if SEED_SNAPSHOT.exists():
        seed_dest = snapshot_path(FIXTURE_DIR, date.fromisoformat(SEED_SNAPSHOT.stem))
        if not seed_dest.exists():
            shutil.copy(SEED_SNAPSHOT, seed_dest)
            print(f"  seeded snapshot {SEED_SNAPSHOT.stem}")

    print("Capturing snapshots...")
    for d in CAPTURE_DATES:
        _capture_snapshot(d)

    print("Emitting data.json corpus...")
    snaps = _load_all_snapshots()
    for as_of in AS_OF_DATES:
        _emit_data_json(as_of, snaps)

    write_index_json(FIXTURE_DIR, updated=GENERATED_AT)
    print(f"Done. Corpus at {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
