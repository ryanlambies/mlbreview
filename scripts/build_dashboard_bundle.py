"""Build the rolling, derived ``dashboard.json`` bundle from per-day data.json.

This is the ONLY place day-over-day trend logic lives (the two-layer principle:
data.json is a dumb immutable record; the bundle owns every derived value).
Given the trailing window of immutable ``data.json`` files, for each board it
joins rows by ``player_id`` across days and precomputes everything the front
end renders: rank deltas, OPS sparkline series, heat/freeze intensity, and the
secondary stat line. The front end stays dumb — it only switches on ``viz``.

Standalone by design: runnable and testable without the digest generator.

Usage:
    python scripts/build_dashboard_bundle.py <base_dir> [--window-days 15] [--out PATH]

``base_dir`` is the published tree containing ``digests/index.json`` and
``digests/<date>/data.json``. Default output is
``<base_dir>/dashboard/data/dashboard.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 15

# Per-board presentation contract. ``kind`` drives stat/secondary formatting;
# ``viz`` drives the front-end switch (series -> sparkline, heat/freeze -> meter).
BOARDS: dict[str, dict[str, Any]] = {
    "hot_hitters": {
        "label": "Hot Hitters", "viz": "series", "metric": "ops",
        "columns": ["AVG", "HR", "RBI", "OPS"], "kind": "hitter",
    },
    "cold_hitters": {
        "label": "Cold Hitters", "viz": "freeze",
        "columns": ["AVG", "HR", "RBI", "OPS"], "kind": "hitter",
    },
    "hot_pitchers": {
        "label": "Hot Pitchers", "viz": "heat",
        "columns": ["ERA", "IP", "K", "Role"], "kind": "pitcher",
    },
    "cold_pitchers": {
        "label": "Cold Pitchers", "viz": "freeze",
        "columns": ["ERA", "IP", "K", "Role"], "kind": "pitcher",
    },
    "breakout_hitters": {
        "label": "Breakout Hitters", "viz": "series", "metric": "ops",
        "columns": ["AVG", "HR", "RBI", "OPS"], "kind": "hitter",
    },
    "breakout_pitchers": {
        "label": "Breakout Pitchers", "viz": "heat",
        "columns": ["ERA", "IP", "K", "Role"], "kind": "pitcher",
    },
}

# Boards in the same family share a player's OPS series. Prefer the 7-day
# (hot/cold) value over the 15-day (breakout) value for the same date.
_HITTER_BOARDS_BY_PRIORITY = ("hot_hitters", "cold_hitters", "breakout_hitters")

_ROW_META_KEYS = frozenset({"rank", "player_id", "player", "team", "window"})


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt3(value: float) -> str:
    """Format a rate stat as .XXX with no leading zero (e.g. .286, 1.000)."""
    return f"{value:.3f}".lstrip("0") or ".000"


def _hitter_secondary(row: dict[str, Any]) -> str:
    base = (
        f"{row['games']} G · {row['pa']} PA · "
        f"OBP {_fmt3(row['obp'])} · SLG {_fmt3(row['slg'])}"
    )
    if row.get("sb"):
        base += f" · {row['sb']} SB"
    return base


def _pitcher_secondary(row: dict[str, Any]) -> str:
    if row.get("role") == "closer":
        sv_pct = row.get("sv_pct")
        pct = f"{sv_pct:.2f}" if sv_pct is not None else "—"
        return f"{row.get('appearances') or 0} APP · {row.get('sv') or 0} SV · SV% {pct}"
    whip = row.get("whip")
    k9 = row.get("k9")
    whip_s = f"{whip:.2f}" if whip is not None else "—"
    k9_s = f"{k9:.1f}" if k9 is not None else "—"
    return f"{row.get('gs') or 0} GS · WHIP {whip_s} · K/9 {k9_s}"


def _secondary(row: dict[str, Any], kind: str) -> str:
    return _hitter_secondary(row) if kind == "hitter" else _pitcher_secondary(row)


# ---------------------------------------------------------------------------
# Trend computation
# ---------------------------------------------------------------------------


def _dedup_by_player(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per player_id (first wins == best rank)."""
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        pid = row["player_id"]
        if pid in seen:
            continue
        seen.add(pid)
        out.append(row)
    return out


def _prev_rank(
    player_id: int, board: str, older_dates: list[str], boards_by_date: dict[str, dict]
) -> int | None:
    """Rank on the same board on the most-recent prior day the player appears.

    Walks older days newest-first and returns the first hit, so a one-day gap
    (player absent yesterday but present two days ago) falls back correctly
    rather than reading as NEW. Returns None when absent across the window.
    """
    for d in older_dates:
        for row in boards_by_date[d].get(board, []):
            if row["player_id"] == player_id:
                return row["rank"]
    return None


def _ops_series(
    player_id: int, ordered_dates: list[str], boards_by_date: dict[str, dict]
) -> list[float]:
    """Player's OPS per date they appear on any hitter board, oldest->newest.

    One value per date (prefer the shorter-window board value). Days where the
    player is on no hitter board are simply absent from the series.
    """
    series: list[float] = []
    for d in ordered_dates:
        boards = boards_by_date[d]
        for board in _HITTER_BOARDS_BY_PRIORITY:
            hit = next(
                (r for r in boards.get(board, []) if r["player_id"] == player_id),
                None,
            )
            if hit is not None:
                series.append(hit["ops"])
                break
    return series


def _bundle_row(
    row: dict[str, Any],
    *,
    board: str,
    viz: str,
    kind: str,
    older_dates: list[str],
    ascending_dates: list[str],
    boards_by_date: dict[str, dict],
) -> dict[str, Any]:
    pid = row["player_id"]
    rank = row["rank"]
    prev_rank = _prev_rank(pid, board, older_dates, boards_by_date)
    is_new = prev_rank is None

    series: list[float] | None = None
    intensity: float | None = None
    if viz == "series":
        series = _ops_series(pid, ascending_dates, boards_by_date)
    else:  # heat / freeze
        intensity = round((11 - rank) / 10, 3)

    return {
        "rank": rank,
        "player_id": pid,
        "player": row["player"],
        "team": row["team"],
        "stats": {k: v for k, v in row.items() if k not in _ROW_META_KEYS},
        "secondary": _secondary(row, kind),
        "prev_rank": prev_rank,
        "delta": 0 if is_new else prev_rank - rank,
        "is_new": is_new,
        "series": series,
        "intensity": intensity,
    }


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------


def _read_dates(base_dir: Path) -> list[str]:
    """Available digest dates newest-first, from index.json (fallback: scan)."""
    index_path = base_dir / "digests" / "index.json"
    if index_path.exists():
        return list(json.loads(index_path.read_text()).get("dates", []))
    digests = base_dir / "digests"
    if not digests.exists():
        return []
    dates = [
        p.name for p in digests.iterdir()
        if p.is_dir() and (p / "data.json").exists()
    ]
    dates.sort(reverse=True)
    return dates


def build_bundle(base_dir: Path, *, window_days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """Build the dashboard.json bundle from the trailing window of data.json."""
    dates = _read_dates(base_dir)[:window_days]
    if not dates:
        return _empty_bundle(window_days)

    data_by_date: dict[str, dict] = {}
    for d in dates:
        path = base_dir / "digests" / d / "data.json"
        data_by_date[d] = json.loads(path.read_text())

    latest = dates[0]
    prev = dates[1] if len(dates) > 1 else None
    latest_data = data_by_date[latest]
    boards_by_date = {d: data_by_date[d]["leaderboards"] for d in dates}

    ascending_dates = list(reversed(dates))  # oldest -> newest (for series)
    older_dates = dates[1:]  # newest-first, excluding latest (for prev_rank)

    boards_out: dict[str, Any] = {}
    for board, cfg in BOARDS.items():
        rows = _dedup_by_player(boards_by_date[latest].get(board, []))
        bundle_rows = [
            _bundle_row(
                row,
                board=board,
                viz=cfg["viz"],
                kind=cfg["kind"],
                older_dates=older_dates,
                ascending_dates=ascending_dates,
                boards_by_date=boards_by_date,
            )
            for row in rows
        ]
        entry = {"label": cfg["label"], "viz": cfg["viz"], "columns": cfg["columns"], "rows": bundle_rows}
        if "metric" in cfg:
            entry["metric"] = cfg["metric"]
        boards_out[board] = entry

    return {
        "as_of": latest,
        "prev": prev,
        "window_days": window_days,
        "scores": latest_data.get("scores", []),
        "storylines": latest_data.get("storylines", []),
        "tonight": latest_data.get("tonight", []),
        "transactions": latest_data.get("transactions", []),
        "boards": boards_out,
    }


def _empty_bundle(window_days: int) -> dict[str, Any]:
    boards_out: dict[str, Any] = {}
    for board, cfg in BOARDS.items():
        entry = {"label": cfg["label"], "viz": cfg["viz"], "columns": cfg["columns"], "rows": []}
        if "metric" in cfg:
            entry["metric"] = cfg["metric"]
        boards_out[board] = entry
    return {
        "as_of": None, "prev": None, "window_days": window_days,
        "scores": [], "storylines": [], "tonight": [], "transactions": [],
        "boards": boards_out,
    }


def write_bundle(
    base_dir: Path, *, window_days: int = DEFAULT_WINDOW_DAYS, out_path: Path | None = None
) -> Path:
    """Build and write dashboard.json. Regenerated every run (overwrites)."""
    bundle = build_bundle(base_dir, window_days=window_days)
    dest = out_path or (base_dir / "dashboard" / "data" / "dashboard.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    logger.info("Wrote dashboard.json bundle (as_of=%s) to %s", bundle["as_of"], dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the dashboard.json bundle.")
    parser.add_argument("base_dir", type=Path, help="Published tree with digests/")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--out", type=Path, default=None, help="Output path override")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = write_bundle(args.base_dir, window_days=args.window_days, out_path=args.out)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
