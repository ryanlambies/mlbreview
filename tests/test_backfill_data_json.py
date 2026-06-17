"""Tests for the data.json backfill tool (scripts/backfill_data_json.py).

The backfill reconstructs historical leaderboards from existing daily snapshots
and writes a faithful, immutable data.json per day, then rebuilds index.json and
dashboard.json so the bundle carries a full trailing window. These tests build a
small synthetic snapshot corpus in a tmp tree and assert the contract:
reconstructed leaderboards, write-once immutability, and a non-null bundle
``prev`` (which is what clears the SPA's "building up data" notice).
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

from mlbreview.data.snapshots import (
    DailySnapshot,
    HitterDayStats,
    write_snapshot,
)

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO / "scripts" / "backfill_data_json.py"
_spec = importlib.util.spec_from_file_location("backfill_data_json", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
backfill_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_mod)

backfill = backfill_mod.backfill
load_snapshots_asof = backfill_mod.load_snapshots_asof


def _hitter(player_id: int, name: str) -> HitterDayStats:
    """A productive day's line — comfortably clears MIN_PA_HITTER over a window."""
    return HitterDayStats(
        player_id=player_id,
        full_name=name,
        team_abbr="NYY",
        plate_appearances=4,
        at_bats=4,
        hits=2,
        doubles=1,
        triples=0,
        home_runs=1,
        rbi=2,
        stolen_bases=0,
        walks=0,
        strikeouts=1,
    )


def _seed_snapshots(base_dir: Path, start: date, days: int) -> list[date]:
    """Write `days` consecutive daily snapshots, each with two qualifying hitters."""
    dates: list[date] = []
    for i in range(days):
        d = start + timedelta(days=i)
        snap = DailySnapshot(
            snapshot_date=d.isoformat(),
            hitters=(_hitter(1, "Aaron Judge"), _hitter(2, "Juan Soto")),
            starters=(),
            closers=(),
        )
        write_snapshot(snap, base_dir=base_dir)
        dates.append(d)
    return dates


def test_load_snapshots_asof_anchors_to_date(tmp_path: Path) -> None:
    start = date(2026, 6, 1)
    _seed_snapshots(tmp_path, start, 10)

    # As-of the 5th day, a 7-day window sees only days 1..5 (5 snapshots).
    asof = start + timedelta(days=4)
    window = load_snapshots_asof(tmp_path, asof, n_days=7)
    assert len(window) == 5
    assert window[0].snapshot_date == asof.isoformat()  # newest-first
    assert all(date.fromisoformat(s.snapshot_date) <= asof for s in window)


def test_backfill_writes_data_json_per_snapshot(tmp_path: Path) -> None:
    start = date(2026, 6, 1)
    dates = _seed_snapshots(tmp_path, start, 5)

    written = backfill(tmp_path, generated_at="2026-06-16T00:00:00+00:00")

    assert written == dates
    for d in dates:
        payload = json.loads(
            (tmp_path / "digests" / d.isoformat() / "data.json").read_text()
        )
        # Content sections empty; leaderboards present (the bundle's trend source).
        assert payload["scores"] == []
        assert payload["storylines"] == []
        assert "hot_hitters" in payload["leaderboards"]

    # By the last day the rolling window has accumulated enough PA (4/day) to
    # clear MIN_PA_HITTER, so the board is actually populated.
    last = json.loads(
        (tmp_path / "digests" / dates[-1].isoformat() / "data.json").read_text()
    )
    assert len(last["leaderboards"]["hot_hitters"]) >= 1


def test_backfill_is_write_once_immutable(tmp_path: Path) -> None:
    start = date(2026, 6, 1)
    _seed_snapshots(tmp_path, start, 3)

    first = date(2026, 6, 1)
    data_path = tmp_path / "digests" / first.isoformat() / "data.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    # A valid pre-existing record carrying a recognizable marker. The backfill
    # must leave it byte-for-byte intact (write-once immutability).
    empty_boards = {
        k: []
        for k in (
            "hot_hitters", "cold_hitters", "hot_pitchers",
            "cold_pitchers", "breakout_hitters", "breakout_pitchers",
        )
    }
    sentinel = json.dumps({
        "meta": {"date": first.isoformat(), "generated_at": "SENTINEL", "season": "2026"},
        "scores": [], "storylines": [], "tonight": [], "transactions": [],
        "leaderboards": empty_boards,
    })
    data_path.write_text(sentinel, encoding="utf-8")

    written = backfill(tmp_path, generated_at="2026-06-16T00:00:00+00:00")

    # The pre-existing record is left untouched; only the other days are written.
    assert first not in written
    assert data_path.read_text() == sentinel


def test_backfill_rebuilds_bundle_with_non_null_prev(tmp_path: Path) -> None:
    start = date(2026, 6, 1)
    _seed_snapshots(tmp_path, start, 4)

    backfill(tmp_path, generated_at="2026-06-16T00:00:00+00:00")

    bundle = json.loads(
        (tmp_path / "dashboard" / "data" / "dashboard.json").read_text()
    )
    # >= 2 days of history => prev is set => SPA drops the "building up data" notice.
    assert bundle["as_of"] == "2026-06-04"
    assert bundle["prev"] == "2026-06-03"
