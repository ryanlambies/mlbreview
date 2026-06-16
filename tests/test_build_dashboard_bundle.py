"""Tests for the dashboard.json bundle builder (U4).

Two layers of coverage:

1. **Real-corpus assertions** against tests/fixtures/bundle/ — a multi-day
   data.json corpus generated from REAL captured MLB snapshots (see
   scripts/gen_bundle_fixtures.py). The pinned deltas/intensity were inspected
   once from that real output; they are not hand-fabricated to match the plan's
   illustrative numbers.
2. **Synthetic-corpus unit tests** for branches the early-season real data
   doesn't exercise (multi-point OPS series, one-day gap fallback) — these test
   the pure trend logic, not the headline numbers.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft7Validator

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO / "scripts" / "build_dashboard_bundle.py"
_spec = importlib.util.spec_from_file_location("build_dashboard_bundle", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
bundle_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle_mod)

build_bundle = bundle_mod.build_bundle
write_bundle = bundle_mod.write_bundle

_FIXTURE = _REPO / "tests" / "fixtures" / "bundle"
_DASHBOARD_SCHEMA = json.loads(
    (_REPO / "schemas" / "dashboard.schema.json").read_text()
)


def _validate(payload: dict) -> None:
    errors = sorted(Draft7Validator(_DASHBOARD_SCHEMA).iter_errors(payload), key=str)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


def _row_by_id(rows: list[dict], player_id: int) -> dict:
    return next(r for r in rows if r["player_id"] == player_id)


# ---------------------------------------------------------------------------
# Real-corpus assertions (as_of 2026-06-14, prev 2026-06-13)
# ---------------------------------------------------------------------------


def test_bundle_validates_against_schema() -> None:
    _validate(build_bundle(_FIXTURE))


def test_bundle_window_and_as_of() -> None:
    bundle = build_bundle(_FIXTURE)
    assert bundle["as_of"] == "2026-06-14"
    assert bundle["prev"] == "2026-06-13"
    assert bundle["window_days"] == 15


def test_hot_pitcher_real_deltas() -> None:
    """Real rank movement on the hot-pitchers board, 06-13 -> 06-14."""
    rows = build_bundle(_FIXTURE)["boards"]["hot_pitchers"]["rows"]

    williams = _row_by_id(rows, 642207)  # Devin Williams — held #1
    assert williams["rank"] == 1 and williams["prev_rank"] == 1
    assert williams["delta"] == 0 and williams["is_new"] is False

    smith = _row_by_id(rows, 671922)  # Cade Smith — 3 -> 4, fell one
    assert smith["rank"] == 4 and smith["prev_rank"] == 3
    assert smith["delta"] == -1

    yamamoto = _row_by_id(rows, 808967)  # Yoshinobu Yamamoto — 4 -> 6, fell two
    assert yamamoto["rank"] == 6 and yamamoto["prev_rank"] == 4
    assert yamamoto["delta"] == -2

    latz = _row_by_id(rows, 656641)  # Jacob Latz — new to the board
    assert latz["is_new"] is True and latz["prev_rank"] is None and latz["delta"] == 0


def test_heat_intensity_is_rank_driven() -> None:
    rows = build_bundle(_FIXTURE)["boards"]["hot_pitchers"]["rows"]
    by_rank = {r["rank"]: r["intensity"] for r in rows}
    assert by_rank[1] == 1.0   # (11 - 1) / 10
    assert by_rank[2] == 0.9
    assert by_rank[6] == 0.5
    # Series fields are null on heat boards.
    assert all(r["series"] is None for r in rows)


def test_freeze_board_has_intensity_not_series() -> None:
    cold = build_bundle(_FIXTURE)["boards"]["cold_pitchers"]
    assert cold["viz"] == "freeze"
    for r in cold["rows"]:
        assert r["series"] is None
        assert r["intensity"] == round((11 - r["rank"]) / 10, 3)


def test_series_board_rows_have_series_not_intensity() -> None:
    hot_h = build_bundle(_FIXTURE)["boards"]["hot_hitters"]
    assert hot_h["viz"] == "series" and hot_h["metric"] == "ops"
    for r in hot_h["rows"]:
        assert r["intensity"] is None
        assert isinstance(r["series"], list) and len(r["series"]) >= 1
        # Early-season: these hitters are new to the hitter boards on 06-14.
        assert r["is_new"] is True


def test_secondary_lines_formatted_per_role() -> None:
    bundle = build_bundle(_FIXTURE)
    # Closer reliever line.
    williams = _row_by_id(bundle["boards"]["hot_pitchers"]["rows"], 642207)
    assert williams["secondary"] == "2 APP · 2 SV · SV% 1.00"
    # Hitter line (Andy Pages on the cold board).
    pages = _row_by_id(bundle["boards"]["cold_hitters"]["rows"], 681624)
    assert pages["secondary"] == "4 G · 20 PA · OBP .200 · SLG .158"


def test_board_config_carried_through() -> None:
    boards = build_bundle(_FIXTURE)["boards"]
    assert boards["hot_pitchers"]["columns"] == ["ERA", "IP", "K", "Role"]
    assert boards["breakout_hitters"]["viz"] == "series"
    assert boards["breakout_pitchers"]["viz"] == "heat"
    assert "metric" not in boards["hot_pitchers"]  # only series boards carry metric


def test_write_bundle_round_trips_and_validates(tmp_path: Path) -> None:
    out = tmp_path / "dashboard.json"
    path = write_bundle(_FIXTURE, out_path=out)
    assert path == out
    _validate(json.loads(out.read_text()))


# ---------------------------------------------------------------------------
# Synthetic-corpus unit tests for branches real early-season data lacks
# ---------------------------------------------------------------------------


def _hrow(rank: int, pid: int, ops: float, *, name: str = "P") -> dict:
    return {
        "rank": rank, "player_id": pid, "player": name, "team": "MIL",
        "avg": 0.300, "hr": 1, "rbi": 2, "ops": ops, "obp": 0.4, "slg": ops - 0.4,
        "games": 3, "pa": 12, "sb": 0, "window": "7-day",
    }


def _prow(rank: int, pid: int, *, name: str = "P") -> dict:
    return {
        "rank": rank, "player_id": pid, "player": name, "team": "ATH",
        "era": 1.0, "ip": 5.0, "k": 6, "role": "starter",
        "appearances": None, "gs": 1, "sv": None, "bs": None, "sv_pct": None,
        "whip": 1.0, "k9": 9.0, "window": "7-day",
    }


def _empty_boards(**overrides) -> dict:
    boards = {
        "hot_hitters": [], "cold_hitters": [], "hot_pitchers": [],
        "cold_pitchers": [], "breakout_hitters": [], "breakout_pitchers": [],
    }
    boards.update(overrides)
    return boards


def _write_corpus(base: Path, days: dict[str, dict]) -> None:
    """days: {date: leaderboards-dict}. Writes data.json + index.json."""
    for d, boards in days.items():
        day_dir = base / "digests" / d
        day_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {"date": d, "generated_at": "x", "season": "2026"},
            "scores": [], "storylines": [], "tonight": [], "transactions": [],
            "leaderboards": boards,
        }
        (day_dir / "data.json").write_text(json.dumps(payload))
    (base / "digests" / "index.json").write_text(
        json.dumps({"updated": "x", "latest": max(days), "dates": sorted(days, reverse=True)})
    )


def test_multi_point_series_in_date_order(tmp_path: Path) -> None:
    # Same hitter (id 7) on the hot-hitter board all three days.
    _write_corpus(tmp_path, {
        "2026-06-12": _empty_boards(hot_hitters=[_hrow(1, 7, 1.100)]),
        "2026-06-13": _empty_boards(hot_hitters=[_hrow(1, 7, 1.050)]),
        "2026-06-14": _empty_boards(hot_hitters=[_hrow(1, 7, 1.200)]),
    })
    row = build_bundle(tmp_path)["boards"]["hot_hitters"]["rows"][0]
    # Oldest -> newest OPS.
    assert row["series"] == [1.100, 1.050, 1.200]
    assert row["prev_rank"] == 1 and row["delta"] == 0


def test_prev_rank_falls_back_across_a_one_day_gap(tmp_path: Path) -> None:
    # Pitcher id 9 on the board on 06-12 and 06-14 but NOT 06-13.
    _write_corpus(tmp_path, {
        "2026-06-12": _empty_boards(hot_pitchers=[_prow(2, 9)]),
        "2026-06-13": _empty_boards(hot_pitchers=[_prow(1, 100)]),
        "2026-06-14": _empty_boards(hot_pitchers=[_prow(1, 9), _prow(2, 100)]),
    })
    row = _row_by_id(build_bundle(tmp_path)["boards"]["hot_pitchers"]["rows"], 9)
    # Falls back to the 06-12 rank (2), not treated as NEW.
    assert row["is_new"] is False
    assert row["prev_rank"] == 2
    assert row["delta"] == 1  # 2 -> 1, climbed one


def test_climber_gets_positive_delta(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {
        "2026-06-13": _empty_boards(hot_pitchers=[_prow(1, 1), _prow(2, 2), _prow(3, 3)]),
        "2026-06-14": _empty_boards(hot_pitchers=[_prow(1, 3), _prow(2, 1), _prow(3, 2)]),
    })
    rows = build_bundle(tmp_path)["boards"]["hot_pitchers"]["rows"]
    assert _row_by_id(rows, 3)["delta"] == 2   # 3 -> 1
    assert _row_by_id(rows, 1)["delta"] == -1  # 1 -> 2


def test_dedup_collapses_repeated_player_id(tmp_path: Path) -> None:
    _write_corpus(tmp_path, {
        "2026-06-14": _empty_boards(
            hot_pitchers=[_prow(1, 5), _prow(2, 5), _prow(3, 6)]
        ),
    })
    rows = build_bundle(tmp_path)["boards"]["hot_pitchers"]["rows"]
    ids = [r["player_id"] for r in rows]
    assert ids == [5, 6]  # first (best-rank) instance of 5 kept, dup dropped


def test_bundle_copies_latest_day_sections(tmp_path: Path) -> None:
    # Two days; the latest day's scores/storylines/tonight/transactions are the
    # ones the bundle surfaces (it's a single fetch for the front end).
    older = tmp_path / "digests" / "2026-06-13"
    older.mkdir(parents=True, exist_ok=True)
    (older / "data.json").write_text(json.dumps({
        "meta": {"date": "2026-06-13", "generated_at": "x", "season": "2026"},
        "scores": [{"away": "OLD", "away_score": 1, "home": "DAY", "home_score": 0, "final": True, "tag": None}],
        "storylines": [], "tonight": [], "transactions": [],
        "leaderboards": _empty_boards(),
    }))
    latest = tmp_path / "digests" / "2026-06-14"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "data.json").write_text(json.dumps({
        "meta": {"date": "2026-06-14", "generated_at": "x", "season": "2026"},
        "scores": [{"away": "COL", "away_score": 12, "home": "ATH", "home_score": 9, "final": True, "tag": "Slugfest"}],
        "storylines": [{"matchup": "CHC @ STL", "score": "7-8", "tag": "Walk-off", "body": "walk-off"}],
        "tonight": [{"matchup": "TB @ LAD", "broadcast": "ESPN", "note": "marquee"}],
        "transactions": [{"team": "KC", "type": "IL", "player_id": 1, "player": "X", "pos": None, "detail": "IL"}],
        "leaderboards": _empty_boards(),
    }))
    (tmp_path / "digests" / "index.json").write_text(
        json.dumps({"updated": "x", "latest": "2026-06-14", "dates": ["2026-06-14", "2026-06-13"]})
    )

    bundle = build_bundle(tmp_path)
    _validate(bundle)
    assert bundle["scores"][0]["tag"] == "Slugfest"   # latest day's, not the older day's
    assert bundle["storylines"][0]["tag"] == "Walk-off"
    assert bundle["tonight"][0]["broadcast"] == "ESPN"
    assert bundle["transactions"][0]["type"] == "IL"


def test_empty_corpus_yields_valid_empty_bundle(tmp_path: Path) -> None:
    (tmp_path / "digests").mkdir(parents=True, exist_ok=True)
    bundle = build_bundle(tmp_path)
    _validate(bundle)
    assert bundle["as_of"] is None and bundle["prev"] is None
    for board in bundle["boards"].values():
        assert board["rows"] == []
