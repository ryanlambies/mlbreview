"""Tests for the daily stat snapshot persistence layer."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from mlbreview.data.snapshots import (
    CloserDayStats,
    DailySnapshot,
    HitterDayStats,
    StarterGameStats,
    load_snapshot,
    load_snapshots,
    snapshot_path,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _hitter(
    player_id: int = 545361,
    full_name: str = "Mike Trout",
    team_abbr: str = "LAA",
) -> HitterDayStats:
    return HitterDayStats(
        player_id=player_id,
        full_name=full_name,
        team_abbr=team_abbr,
        plate_appearances=4,
        at_bats=3,
        hits=2,
        doubles=1,
        triples=0,
        home_runs=1,
        rbi=3,
        stolen_bases=0,
        walks=1,
        strikeouts=0,
    )


def _starter(
    player_id: int = 543037,
    full_name: str = "Gerrit Cole",
    team_abbr: str = "NYY",
) -> StarterGameStats:
    return StarterGameStats(
        player_id=player_id,
        full_name=full_name,
        team_abbr=team_abbr,
        game_date="2026-05-18",
        opponent_abbr="BOS",
        outs_recorded=19,
        hits_allowed=4,
        earned_runs=1,
        walks=2,
        strikeouts=8,
        home_runs_allowed=0,
        pitches_thrown=98,
    )


def _closer(
    player_id: int = 608566,
    full_name: str = "Emmanuel Clase",
    team_abbr: str = "CLE",
) -> CloserDayStats:
    return CloserDayStats(
        player_id=player_id,
        full_name=full_name,
        team_abbr=team_abbr,
        outs_recorded=3,
        earned_runs=0,
        saves=1,
        blown_saves=0,
        holds=0,
        strikeouts=1,
        walks=0,
    )


def _snapshot(
    snapshot_date: str = "2026-05-18",
    hitters: tuple[HitterDayStats, ...] | None = None,
    starters: tuple[StarterGameStats, ...] | None = None,
    closers: tuple[CloserDayStats, ...] | None = None,
) -> DailySnapshot:
    return DailySnapshot(
        snapshot_date=snapshot_date,
        hitters=hitters if hitters is not None else (_hitter(),),
        starters=starters if starters is not None else (_starter(),),
        closers=closers if closers is not None else (_closer(),),
    )


# ---------------------------------------------------------------------------
# Dataclass properties
# ---------------------------------------------------------------------------


class TestDataclassProperties:
    def test_starter_innings_pitched(self):
        s = _starter()  # default outs_recorded=19
        assert abs(s.innings_pitched - 6.333) < 0.01

    def test_starter_innings_pitched_exact(self):
        s = StarterGameStats(
            player_id=1, full_name="Test", team_abbr="TST",
            game_date="2026-05-18", opponent_abbr="OPP",
            outs_recorded=18, hits_allowed=0, earned_runs=0,
            walks=0, strikeouts=0, home_runs_allowed=0, pitches_thrown=0,
        )
        assert s.innings_pitched == 6.0

    def test_closer_innings_pitched(self):
        c = _closer()  # default outs_recorded=3
        assert c.innings_pitched == 1.0

    def test_closer_zero_outs(self):
        c = CloserDayStats(
            player_id=1, full_name="Test", team_abbr="TST",
            outs_recorded=0, earned_runs=0, saves=0,
            blown_saves=0, holds=0, strikeouts=0, walks=0,
        )
        assert c.innings_pitched == 0.0

    def test_dataclasses_are_frozen(self):
        h = _hitter()
        with pytest.raises(AttributeError):
            h.hits = 99  # type: ignore[misc]

        s = _starter()
        with pytest.raises(AttributeError):
            s.earned_runs = 99  # type: ignore[misc]

        c = _closer()
        with pytest.raises(AttributeError):
            c.saves = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_write_and_read_single(self, tmp_path: Path):
        original = _snapshot()
        written_path = write_snapshot(original, base_dir=tmp_path)

        assert written_path.exists()
        assert written_path.name == "2026-05-18.json"

        loaded = load_snapshot(written_path)
        assert loaded == original

    def test_round_trip_preserves_all_fields(self, tmp_path: Path):
        hitter = _hitter(player_id=660271, full_name="Shohei Ohtani", team_abbr="LAD")
        starter = _starter(player_id=477132, full_name="Clayton Kershaw", team_abbr="LAD")
        closer = _closer(player_id=665795, full_name="Ryan Helsley", team_abbr="STL")

        original = _snapshot(
            hitters=(hitter,),
            starters=(starter,),
            closers=(closer,),
        )
        write_snapshot(original, base_dir=tmp_path)
        loaded = load_snapshot(snapshot_path(tmp_path, date(2026, 5, 18)))

        assert loaded.hitters[0].full_name == "Shohei Ohtani"
        assert loaded.hitters[0].player_id == 660271
        assert loaded.starters[0].full_name == "Clayton Kershaw"
        assert loaded.closers[0].full_name == "Ryan Helsley"

    def test_round_trip_multiple_players(self, tmp_path: Path):
        original = _snapshot(
            hitters=(
                _hitter(545361, "Mike Trout", "LAA"),
                _hitter(660271, "Shohei Ohtani", "LAD"),
            ),
            starters=(
                _starter(543037, "Gerrit Cole", "NYY"),
                _starter(477132, "Clayton Kershaw", "LAD"),
            ),
            closers=(
                _closer(608566, "Emmanuel Clase", "CLE"),
            ),
        )
        write_snapshot(original, base_dir=tmp_path)
        loaded = load_snapshot(snapshot_path(tmp_path, date(2026, 5, 18)))

        assert len(loaded.hitters) == 2
        assert len(loaded.starters) == 2
        assert len(loaded.closers) == 1

    def test_empty_snapshot(self, tmp_path: Path):
        original = _snapshot(hitters=(), starters=(), closers=())
        write_snapshot(original, base_dir=tmp_path)
        loaded = load_snapshot(snapshot_path(tmp_path, date(2026, 5, 18)))

        assert loaded.hitters == ()
        assert loaded.starters == ()
        assert loaded.closers == ()

    def test_snapshot_is_valid_json(self, tmp_path: Path):
        write_snapshot(_snapshot(), base_dir=tmp_path)
        path = snapshot_path(tmp_path, date(2026, 5, 18))

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["snapshot_date"] == "2026-05-18"
        assert len(data["hitters"]) == 1
        assert data["hitters"][0]["full_name"] == "Mike Trout"


# ---------------------------------------------------------------------------
# snapshot_path
# ---------------------------------------------------------------------------


class TestSnapshotPath:
    def test_canonical_path(self, tmp_path: Path):
        p = snapshot_path(tmp_path, date(2026, 5, 18))
        assert p == tmp_path / "snapshots" / "2026-05-18.json"

    def test_different_dates(self, tmp_path: Path):
        p1 = snapshot_path(tmp_path, date(2026, 3, 20))
        p2 = snapshot_path(tmp_path, date(2026, 11, 10))
        assert p1.name == "2026-03-20.json"
        assert p2.name == "2026-11-10.json"


# ---------------------------------------------------------------------------
# load_snapshots — multi-file loading
# ---------------------------------------------------------------------------


class TestLoadSnapshots:
    def test_loads_most_recent_n(self, tmp_path: Path):
        for day in range(15, 22):
            write_snapshot(
                _snapshot(snapshot_date=f"2026-05-{day:02d}"),
                base_dir=tmp_path,
            )

        loaded = load_snapshots(base_dir=tmp_path, n_days=3)

        assert len(loaded) == 3
        assert loaded[0].snapshot_date == "2026-05-21"
        assert loaded[1].snapshot_date == "2026-05-20"
        assert loaded[2].snapshot_date == "2026-05-19"

    def test_returns_all_when_fewer_than_n(self, tmp_path: Path):
        write_snapshot(_snapshot(snapshot_date="2026-05-18"), base_dir=tmp_path)
        write_snapshot(_snapshot(snapshot_date="2026-05-19"), base_dir=tmp_path)

        loaded = load_snapshots(base_dir=tmp_path, n_days=10)
        assert len(loaded) == 2

    def test_empty_directory(self, tmp_path: Path):
        loaded = load_snapshots(base_dir=tmp_path, n_days=7)
        assert loaded == []

    def test_nonexistent_directory(self, tmp_path: Path):
        loaded = load_snapshots(base_dir=tmp_path / "does_not_exist", n_days=7)
        assert loaded == []

    def test_skips_non_json_files(self, tmp_path: Path):
        write_snapshot(_snapshot(snapshot_date="2026-05-18"), base_dir=tmp_path)

        # Write a non-JSON file in the snapshots dir
        (tmp_path / "snapshots" / "README.md").write_text("ignore me")

        loaded = load_snapshots(base_dir=tmp_path, n_days=7)
        assert len(loaded) == 1

    def test_skips_non_date_filenames(self, tmp_path: Path):
        write_snapshot(_snapshot(snapshot_date="2026-05-18"), base_dir=tmp_path)

        # Write a JSON file with a non-date name
        (tmp_path / "snapshots" / "metadata.json").write_text("{}")

        loaded = load_snapshots(base_dir=tmp_path, n_days=7)
        assert len(loaded) == 1

    def test_skips_corrupt_files(self, tmp_path: Path):
        write_snapshot(_snapshot(snapshot_date="2026-05-18"), base_dir=tmp_path)

        # Write a corrupt JSON file with a valid date name
        (tmp_path / "snapshots" / "2026-05-17.json").write_text("not json{{{")

        loaded = load_snapshots(base_dir=tmp_path, n_days=7)
        assert len(loaded) == 1
        assert loaded[0].snapshot_date == "2026-05-18"

    def test_sorted_newest_first(self, tmp_path: Path):
        # Write in non-chronological order
        write_snapshot(_snapshot(snapshot_date="2026-05-15"), base_dir=tmp_path)
        write_snapshot(_snapshot(snapshot_date="2026-05-20"), base_dir=tmp_path)
        write_snapshot(_snapshot(snapshot_date="2026-05-17"), base_dir=tmp_path)

        loaded = load_snapshots(base_dir=tmp_path, n_days=10)
        dates = [s.snapshot_date for s in loaded]
        assert dates == ["2026-05-20", "2026-05-17", "2026-05-15"]


# ---------------------------------------------------------------------------
# write_snapshot — directory creation
# ---------------------------------------------------------------------------


class TestWriteSnapshot:
    def test_creates_snapshots_directory(self, tmp_path: Path):
        assert not (tmp_path / "snapshots").exists()
        write_snapshot(_snapshot(), base_dir=tmp_path)
        assert (tmp_path / "snapshots").is_dir()

    def test_overwrites_existing_file(self, tmp_path: Path):
        original = _snapshot(hitters=(_hitter(545361, "Mike Trout", "LAA"),))
        write_snapshot(original, base_dir=tmp_path)

        updated = _snapshot(hitters=(
            _hitter(545361, "Mike Trout", "LAA"),
            _hitter(660271, "Shohei Ohtani", "LAD"),
        ))
        write_snapshot(updated, base_dir=tmp_path)

        loaded = load_snapshot(snapshot_path(tmp_path, date(2026, 5, 18)))
        assert len(loaded.hitters) == 2
