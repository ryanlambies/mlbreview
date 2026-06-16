"""Tests for the per-day data.json serializer (U2).

Validates the serialized shape against schemas/data.schema.json and pins the
mapping decisions: player_id on every row, OPS = OBP + SLG, real window labels
(7-day hot/cold, 15-day breakout), scoreboard tag derivation, transaction-type
mapping, write-once immutability, and the empty/off-day contract.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from jsonschema import Draft7Validator

from mlbreview.data.digest_data import (
    build_data_json,
    build_index_json,
    data_json_path,
    index_json_path,
    write_data_json,
    write_index_json,
)
from mlbreview.data.game import GameFeed
from mlbreview.data.schedule import (
    Broadcast,
    Decisions,
    Game,
    InningLine,
    TonightGame,
)
from mlbreview.data.transactions import Transaction, TransactionCategory
from mlbreview.render.pages import Digest, Storyline, TonightPreview
from mlbreview.scoring.drama import ScoredGame
from mlbreview.scoring.hype import ScoredTonightGame
from mlbreview.scoring.leaderboards import (
    LeaderboardHitter,
    LeaderboardPitcher,
    Leaderboards,
    LuckStatus,
)

GENERATED_AT = "2026-06-15T16:05:00Z"

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
_SCHEMA = json.loads((_SCHEMA_DIR / "data.schema.json").read_text())
_INDEX_SCHEMA = json.loads((_SCHEMA_DIR / "index.schema.json").read_text())


def _validate(payload: dict, schema: dict = _SCHEMA) -> None:
    """Raise if payload violates the schema (collects all errors)."""
    errors = sorted(Draft7Validator(schema).iter_errors(payload), key=str)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _game(
    *,
    gamePk: int,
    away_abbr: str,
    home_abbr: str,
    away_score: int,
    home_score: int,
    innings: int = 9,
) -> Game:
    return Game(
        gamePk=gamePk,
        game_type="R",
        status="Final",
        away_team_name=away_abbr,
        away_team_abbr=away_abbr,
        away_score=away_score,
        home_team_name=home_abbr,
        home_team_abbr=home_abbr,
        home_score=home_score,
        decisions=Decisions(winner="W", loser="L", save=None),
        line_score=tuple(
            InningLine(inning=i + 1, away_runs=0, home_runs=0) for i in range(innings)
        ),
    )


def _feed(gamePk: int) -> GameFeed:
    return GameFeed(
        gamePk=gamePk, plays=(), max_wpa_swing=0.0,
        late_inning_max_wpa=0.0, biggest_play=None,
    )


def _storyline(game: Game, category: str, prose: str) -> Storyline:
    return Storyline(
        scored=ScoredGame(game=game, feed=_feed(game.gamePk), score=0.8, category=category),
        prose=prose,
    )


def _tonight_preview() -> TonightPreview:
    game = TonightGame(
        gamePk=99, game_type="R", game_date_utc="2026-06-15T23:10:00Z",
        away_team_name="Tampa Bay Rays", away_team_abbr="TB", away_record="41-27",
        home_team_name="Los Angeles Dodgers", home_team_abbr="LAD", home_record="45-27",
        away_probable_pitcher=None, home_probable_pitcher=None,
        broadcasts=(
            Broadcast(name="MLB.tv", type="TV", is_national=False),
            Broadcast(name="ESPN", type="TV", is_national=True),
        ),
    )
    return TonightPreview(
        scored=ScoredTonightGame(game=game, score=0.9),
        prose="Rays at Dodgers in a marquee interleague matchup.",
    )


def _hitter(player_id: int, name: str, obp: float, slg: float) -> LeaderboardHitter:
    return LeaderboardHitter(
        player_id=player_id, full_name=name, team_abbr="MIL",
        games=6, plate_appearances=31, avg=0.448, obp=obp, slg=slg,
        home_runs=5, rbi=10, stolen_bases=0,
        composite_score=0.9, luck_status=LuckStatus.CONFIRMED,
    )


def _closer(player_id: int, name: str) -> LeaderboardPitcher:
    return LeaderboardPitcher(
        player_id=player_id, full_name=name, team_abbr="ATH",
        role="closer", era=0.0, innings_pitched=3.1, strikeouts=8,
        composite_score=0.95, luck_status=LuckStatus.UNCONFIRMED,
        saves=2, blown_saves=0, save_pct=1.0, holds=0, appearances=3,
    )


def _leaderboards() -> Leaderboards:
    return Leaderboards(
        hot_hitters=[_hitter(694192, "Jackson Chourio", 0.452, 0.966)],
        cold_hitters=[_hitter(700001, "Cold Bat", 0.200, 0.250)],
        hot_pitchers=[_closer(681911, "Elvis Alvarado")],
        cold_pitchers=[],
        breakout_hitters=[_hitter(694193, "Breakout Bat", 0.400, 0.700)],
        breakout_pitchers=[_closer(681912, "Breakout Arm")],
        snapshots_7d=7, snapshots_15d=15,
    )


def _full_digest() -> Digest:
    games = [
        _game(gamePk=1, away_abbr="NYY", home_abbr="BOS", away_score=5, home_score=3),
        _game(gamePk=2, away_abbr="COL", home_abbr="ATH", away_score=12, home_score=9),
        _game(gamePk=3, away_abbr="STL", home_abbr="MIN", away_score=0, home_score=4),
        _game(gamePk=4, away_abbr="CHC", home_abbr="STL", away_score=7, home_score=8, innings=11),
        _game(gamePk=5, away_abbr="LAD", home_abbr="SF", away_score=2, home_score=1),
    ]
    storylines = [
        _storyline(games[3], "walkoff", "The Cards walked it off in the 11th."),
        _storyline(games[4], "pitchers_duel", "A taut 2-1 pitchers' duel."),
    ]
    transactions = [
        Transaction(
            transaction_id=1, date="2026-06-14",
            category=TransactionCategory.INJURED_LIST,
            player_name="Vinnie Pasquantino", player_id=663728,
            team_name="Kansas City Royals",
            description="placed 1B Vinnie Pasquantino on the 10-day IL.",
        ),
        Transaction(
            transaction_id=2, date="2026-06-14",
            category=TransactionCategory.CALL_UP,
            player_name="Rookie Caller", player_id=700777,
            team_name="Seattle Mariners",
            description="recalled RHP Rookie Caller from Triple-A.",
        ),
        Transaction(
            transaction_id=3, date="2026-06-14",
            category=TransactionCategory.TRADE,
            player_name="No Id Guy", player_id=None,
            team_name="Some Team",
            description="acquired No Id Guy.",
        ),
    ]
    return Digest(
        digest_date=date(2026, 6, 14),
        games=games,
        storylines=storylines,
        tonight=_tonight_preview(),
        transactions=transactions,
        leaderboards=_leaderboards(),
    )


# ---------------------------------------------------------------------------
# Schema + identity
# ---------------------------------------------------------------------------


def test_build_data_json_validates_against_schema() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    _validate(payload)


def test_every_leaderboard_row_carries_int_player_id() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    for board in payload["leaderboards"].values():
        for row in board:
            assert isinstance(row["player_id"], int)


def test_transactions_carry_player_id_or_none() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    by_player = {t["player"]: t for t in payload["transactions"]}
    assert by_player["Vinnie Pasquantino"]["player_id"] == 663728
    assert by_player["No Id Guy"]["player_id"] is None


def test_transaction_type_mapping() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    by_player = {t["player"]: t["type"] for t in payload["transactions"]}
    assert by_player["Vinnie Pasquantino"] == "IL"
    assert by_player["Rookie Caller"] == "REC"
    assert by_player["No Id Guy"] == "OTHER"


# ---------------------------------------------------------------------------
# Coercion / meta
# ---------------------------------------------------------------------------


def test_meta_date_is_iso_and_payload_is_json_native() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    assert payload["meta"]["date"] == "2026-06-14"
    assert payload["meta"]["season"] == "2026"
    assert payload["meta"]["generated_at"] == GENERATED_AT
    # No enum/date object leaks — round-trips cleanly through json.
    assert json.loads(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# Scoreboard / storyline tags
# ---------------------------------------------------------------------------


def test_score_tags_derived_correctly() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    tags = {(s["away"], s["home"]): s["tag"] for s in payload["scores"]}
    assert tags[("NYY", "BOS")] is None          # 5-3, ordinary
    assert tags[("COL", "ATH")] == "Slugfest"     # 21 combined runs
    assert tags[("STL", "MIN")] == "Shutout"      # away held to 0
    assert tags[("CHC", "STL")] == "Walk-off"     # storyline category wins
    assert tags[("LAD", "SF")] == "Pitchers' Duel"


def test_storyline_entries_carry_mapped_tag_and_body() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    by_matchup = {s["matchup"]: s for s in payload["storylines"]}
    walkoff = by_matchup["CHC @ STL"]
    assert walkoff["tag"] == "Walk-off"
    assert walkoff["score"] == "7-8"
    assert "walked it off" in walkoff["body"]


def test_tonight_prefers_national_broadcast() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    assert len(payload["tonight"]) == 1
    assert payload["tonight"][0]["matchup"] == "TB @ LAD"
    assert payload["tonight"][0]["broadcast"] == "ESPN"


# ---------------------------------------------------------------------------
# Row mapping: OPS, windows, ranks, closer fields
# ---------------------------------------------------------------------------


def test_hitter_ops_is_obp_plus_slg_and_rank_is_position() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    row = payload["leaderboards"]["hot_hitters"][0]
    assert row["rank"] == 1
    assert row["ops"] == round(0.452 + 0.966, 3)
    assert row["obp"] == 0.452 and row["slg"] == 0.966


def test_window_labels_match_real_leaderboard_windows() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    assert payload["leaderboards"]["hot_hitters"][0]["window"] == "7-day"
    assert payload["leaderboards"]["hot_pitchers"][0]["window"] == "7-day"
    assert payload["leaderboards"]["breakout_hitters"][0]["window"] == "15-day"
    assert payload["leaderboards"]["breakout_pitchers"][0]["window"] == "15-day"


def test_closer_row_maps_role_specific_fields() -> None:
    payload = build_data_json(_full_digest(), generated_at=GENERATED_AT)
    row = payload["leaderboards"]["hot_pitchers"][0]
    assert row["role"] == "closer"
    assert row["sv"] == 2 and row["bs"] == 0
    assert row["sv_pct"] == 1.0
    assert row["appearances"] == 3
    assert row["gs"] is None and row["whip"] is None and row["k9"] is None


# ---------------------------------------------------------------------------
# Empty / off-day contract
# ---------------------------------------------------------------------------


def test_off_day_digest_produces_valid_empty_payload() -> None:
    digest = Digest(digest_date=date(2026, 6, 14), is_off_day=True, leaderboards=None)
    payload = build_data_json(digest, generated_at=GENERATED_AT)
    _validate(payload)
    assert payload["scores"] == []
    assert payload["storylines"] == []
    assert payload["tonight"] == []
    assert payload["transactions"] == []
    for board in payload["leaderboards"].values():
        assert board == []


# ---------------------------------------------------------------------------
# Write-once idempotency
# ---------------------------------------------------------------------------


def test_write_data_json_writes_then_refuses_overwrite(tmp_path: Path) -> None:
    digest = _full_digest()
    path = write_data_json(digest, base_dir=tmp_path, generated_at=GENERATED_AT)
    assert path == data_json_path(tmp_path, "2026-06-14")
    assert path is not None and path.exists()
    original = path.read_text()

    # A second call (e.g. a later cron slot) must not overwrite the record.
    second = write_data_json(
        digest, base_dir=tmp_path, generated_at="2099-01-01T00:00:00Z"
    )
    assert second is None
    assert path.read_text() == original


def test_written_data_json_validates_against_schema(tmp_path: Path) -> None:
    write_data_json(_full_digest(), base_dir=tmp_path, generated_at=GENERATED_AT)
    written = json.loads(data_json_path(tmp_path, "2026-06-14").read_text())
    _validate(written)


# ---------------------------------------------------------------------------
# index.json manifest (U3)
# ---------------------------------------------------------------------------

UPDATED = "2026-06-15T16:05:00Z"


def _make_data_json_days(base_dir: Path, dates: list[str]) -> None:
    """Create digests/<date>/data.json for each given date."""
    for d in dates:
        day_dir = base_dir / "digests" / d
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "data.json").write_text("{}", encoding="utf-8")


def test_build_index_json_newest_first(tmp_path: Path) -> None:
    _make_data_json_days(tmp_path, ["2026-06-12", "2026-06-14", "2026-06-13"])
    manifest = build_index_json(tmp_path, updated=UPDATED)
    _validate(manifest, _INDEX_SCHEMA)
    assert manifest["dates"] == ["2026-06-14", "2026-06-13", "2026-06-12"]
    assert manifest["latest"] == "2026-06-14"
    assert manifest["updated"] == UPDATED


def test_build_index_json_tolerates_gaps(tmp_path: Path) -> None:
    # An off-day gap (no 06-13) must not break ordering or imply continuity.
    _make_data_json_days(tmp_path, ["2026-06-12", "2026-06-14"])
    manifest = build_index_json(tmp_path, updated=UPDATED)
    assert manifest["dates"] == ["2026-06-14", "2026-06-12"]
    assert manifest["latest"] == "2026-06-14"


def test_build_index_json_skips_dirs_without_data_json(tmp_path: Path) -> None:
    _make_data_json_days(tmp_path, ["2026-06-14"])
    # A legacy day rendered before the data layer: index.html only, no data.json.
    legacy = tmp_path / "digests" / "2026-06-10"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "index.html").write_text("<html></html>", encoding="utf-8")
    # A stray non-date directory must also be ignored.
    (tmp_path / "digests" / "archive").mkdir(parents=True, exist_ok=True)

    manifest = build_index_json(tmp_path, updated=UPDATED)
    assert manifest["dates"] == ["2026-06-14"]


def test_build_index_json_empty_when_no_digests(tmp_path: Path) -> None:
    manifest = build_index_json(tmp_path, updated=UPDATED)
    _validate(manifest, _INDEX_SCHEMA)
    assert manifest["dates"] == []
    assert manifest["latest"] is None


def test_build_index_json_is_idempotent(tmp_path: Path) -> None:
    _make_data_json_days(tmp_path, ["2026-06-12", "2026-06-13"])
    first = build_index_json(tmp_path, updated="2026-06-15T00:00:00Z")
    second = build_index_json(tmp_path, updated="2099-12-31T00:00:00Z")
    # Only the timestamp differs; the data-derived fields are stable.
    assert first["dates"] == second["dates"]
    assert first["latest"] == second["latest"]


def test_write_index_json_writes_and_validates(tmp_path: Path) -> None:
    _make_data_json_days(tmp_path, ["2026-06-13", "2026-06-14"])
    path = write_index_json(tmp_path, updated=UPDATED)
    assert path == index_json_path(tmp_path)
    written = json.loads(path.read_text())
    _validate(written, _INDEX_SCHEMA)
    assert written["dates"] == ["2026-06-14", "2026-06-13"]
