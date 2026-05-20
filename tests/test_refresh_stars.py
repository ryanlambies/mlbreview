"""Tests for scripts/refresh_stars.py — dynamic star-player list refresh.

Covers: WAR-based ranking, deduplication (Ohtani), min-WAR filtering,
position enrichment, league-neutral output shape, dry-run mode, and
graceful degradation when pybaseball or MLB API calls fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# The script lives outside the package, so we import from the file path.
import importlib.util

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "refresh_stars.py"
_spec = importlib.util.spec_from_file_location("refresh_stars", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
refresh_stars = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh_stars)

build_star_list = refresh_stars.build_star_list
write_stars_json = refresh_stars.write_stars_json
main = refresh_stars.main


# ---------------------------------------------------------------------------
# Fixtures — fake DataFrames matching pybaseball output shapes
# ---------------------------------------------------------------------------


def _bat_df(rows: list[dict]) -> pd.DataFrame:
    """Build a batting WAR DataFrame with required columns."""
    defaults = {
        "year_ID": 2024,
        "pitcher": "N",
        "mlb_ID": 0,
        "name_common": "Test Player",
        "WAR": 0.0,
        "team_ID": "TST",
    }
    data = [{**defaults, **r} for r in rows]
    return pd.DataFrame(data)


def _pitch_df(rows: list[dict]) -> pd.DataFrame:
    """Build a pitching WAR DataFrame with required columns."""
    defaults = {
        "year_ID": 2024,
        "mlb_ID": 0,
        "name_common": "Test Pitcher",
        "WAR": 0.0,
        "team_ID": "TST",
    }
    data = [{**defaults, **r} for r in rows]
    return pd.DataFrame(data)


def _mock_bwar_bat(df: pd.DataFrame):
    """Return a mock for _fetch_batter_war that returns *df*."""
    return patch.object(refresh_stars, "_fetch_batter_war", return_value=df)


def _mock_bwar_pitch(df: pd.DataFrame):
    """Return a mock for _fetch_pitcher_war that returns *df*."""
    return patch.object(refresh_stars, "_fetch_pitcher_war", return_value=df)


def _mock_positions(positions: dict[int, str] | None = None):
    """Mock the MLB API position lookup."""
    if positions is None:
        positions = {}
    return patch.object(refresh_stars, "_lookup_positions", return_value=positions)


# ---------------------------------------------------------------------------
# build_star_list tests
# ---------------------------------------------------------------------------


class TestBuildStarList:
    def test_ranks_by_war_descending(self) -> None:
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "Player A", "WAR": 5.0},
            {"mlb_ID": 2, "name_common": "Player B", "WAR": 8.0},
            {"mlb_ID": 3, "name_common": "Player C", "WAR": 3.0},
        ])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=3, min_war=0.0)

        assert len(result) == 3
        assert result[0]["name"] == "Player B"
        assert result[1]["name"] == "Player A"
        assert result[2]["name"] == "Player C"

    def test_respects_top_n(self) -> None:
        bat = _bat_df([
            {"mlb_ID": i, "name_common": f"Player {i}", "WAR": 10.0 - i}
            for i in range(10)
        ])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=5, min_war=0.0)

        assert len(result) == 5

    def test_min_war_filters(self) -> None:
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "Star", "WAR": 5.0},
            {"mlb_ID": 2, "name_common": "Average", "WAR": 1.5},
            {"mlb_ID": 3, "name_common": "Bench", "WAR": 0.5},
        ])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=30, min_war=2.0)

        assert len(result) == 1
        assert result[0]["name"] == "Star"

    def test_mixes_batters_and_pitchers(self) -> None:
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "Batter A", "WAR": 7.0},
            {"mlb_ID": 2, "name_common": "Batter B", "WAR": 5.0},
        ])
        pitch = _pitch_df([
            {"mlb_ID": 10, "name_common": "Pitcher A", "WAR": 6.0},
        ])
        positions = {1: "RF", 2: "SS", 10: "P"}

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions(positions):
            result = build_star_list(2024, top_n=30, min_war=0.0)

        assert len(result) == 3
        # Sorted by WAR: Batter A (7.0), Pitcher A (6.0), Batter B (5.0)
        assert result[0]["name"] == "Batter A"
        assert result[0]["position"] == "RF"
        assert result[1]["name"] == "Pitcher A"
        assert result[1]["position"] == "P"
        assert result[2]["name"] == "Batter B"
        assert result[2]["position"] == "SS"

    def test_deduplicates_ohtani(self) -> None:
        """A player appearing in both batter and pitcher tables only appears once."""
        bat = _bat_df([
            {"mlb_ID": 660271, "name_common": "Shohei Ohtani", "WAR": 9.0},
            {"mlb_ID": 2, "name_common": "Other Batter", "WAR": 5.0},
        ])
        pitch = _pitch_df([
            {"mlb_ID": 660271, "name_common": "Shohei Ohtani", "WAR": 3.0},
            {"mlb_ID": 10, "name_common": "Some Pitcher", "WAR": 4.0},
        ])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=30, min_war=0.0)

        ohtani_entries = [e for e in result if e["id"] == 660271]
        assert len(ohtani_entries) == 1
        assert len(result) == 3  # Ohtani + Other Batter + Some Pitcher

    def test_skips_nan_mlb_id(self) -> None:
        bat = _bat_df([
            {"mlb_ID": float("nan"), "name_common": "Ghost", "WAR": 10.0},
            {"mlb_ID": 1, "name_common": "Real Player", "WAR": 5.0},
        ])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=30, min_war=0.0)

        assert len(result) == 1
        assert result[0]["name"] == "Real Player"

    def test_fallback_positions_when_api_fails(self) -> None:
        """Without MLB API data, batters get 'DH' and pitchers get 'P'."""
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "Batter", "WAR": 5.0},
        ])
        pitch = _pitch_df([
            {"mlb_ID": 2, "name_common": "Pitcher", "WAR": 4.0},
        ])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions({}):
            result = build_star_list(2024, top_n=30, min_war=0.0)

        assert result[0]["position"] == "DH"
        assert result[1]["position"] == "P"

    def test_empty_season_returns_empty(self) -> None:
        bat = _bat_df([])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=30, min_war=2.0)

        assert result == []

    def test_output_schema_matches_stars_json(self) -> None:
        """Every entry has exactly {id, name, position} — matching load_star_ids()."""
        bat = _bat_df([
            {"mlb_ID": 592450, "name_common": "Aaron Judge", "WAR": 10.0},
        ])
        pitch = _pitch_df([])
        positions = {592450: "RF"}

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions(positions):
            result = build_star_list(2024, top_n=30, min_war=0.0)

        assert len(result) == 1
        entry = result[0]
        assert set(entry.keys()) == {"id", "name", "position"}
        assert isinstance(entry["id"], int)
        assert isinstance(entry["name"], str)
        assert isinstance(entry["position"], str)


# ---------------------------------------------------------------------------
# League-neutral guarantee
# ---------------------------------------------------------------------------


class TestLeagueNeutral:
    def test_no_team_bias_in_selection(self) -> None:
        """Selection is purely WAR-based — multiple players from one team is fine."""
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "NYY Star 1", "WAR": 9.0, "team_ID": "NYY"},
            {"mlb_ID": 2, "name_common": "NYY Star 2", "WAR": 8.0, "team_ID": "NYY"},
            {"mlb_ID": 3, "name_common": "LAD Star", "WAR": 7.0, "team_ID": "LAD"},
            {"mlb_ID": 4, "name_common": "BOS Star", "WAR": 6.0, "team_ID": "BOS"},
        ])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            result = build_star_list(2024, top_n=4, min_war=0.0)

        # All four selected — no team-capping (WAR is the only criterion)
        assert len(result) == 4
        names = [e["name"] for e in result]
        assert "NYY Star 1" in names
        assert "NYY Star 2" in names


# ---------------------------------------------------------------------------
# write_stars_json
# ---------------------------------------------------------------------------


class TestWriteStarsJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        entries = [
            {"id": 660271, "name": "Shohei Ohtani", "position": "DH"},
            {"id": 592450, "name": "Aaron Judge", "position": "RF"},
        ]
        out_path = tmp_path / "config" / "stars.json"
        write_stars_json(entries, out_path)

        loaded = json.loads(out_path.read_text())
        assert loaded == entries

    def test_preserves_unicode(self, tmp_path: Path) -> None:
        entries = [{"id": 1, "name": "José Ramírez", "position": "3B"}]
        out_path = tmp_path / "stars.json"
        write_stars_json(entries, out_path)

        text = out_path.read_text(encoding="utf-8")
        assert "José Ramírez" in text
        # ensure_ascii=False means no \\u escapes
        assert "\\u" not in text

    def test_roundtrip_with_load_star_ids(self, tmp_path: Path) -> None:
        """Verify the output is consumable by hype.load_star_ids()."""
        from mlbreview.scoring.hype import load_star_ids

        entries = [
            {"id": 660271, "name": "Shohei Ohtani", "position": "DH"},
            {"id": 592450, "name": "Aaron Judge", "position": "RF"},
        ]
        out_path = tmp_path / "stars.json"
        write_stars_json(entries, out_path)

        star_ids = load_star_ids(out_path)
        assert star_ids == frozenset({660271, 592450})


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestCli:
    def test_dry_run_prints_without_writing(self, capsys, tmp_path: Path) -> None:
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "Test Player", "WAR": 5.0},
        ])
        pitch = _pitch_df([])
        out_path = tmp_path / "stars.json"

        with (
            _mock_bwar_bat(bat),
            _mock_bwar_pitch(pitch),
            _mock_positions(),
            patch.object(refresh_stars, "STARS_JSON", out_path),
        ):
            code = main(["--season", "2024", "--dry-run", "--min-war", "0"])

        assert code == 0
        assert not out_path.exists()  # dry-run doesn't write
        captured = capsys.readouterr()
        assert "Test Player" in captured.out

    def test_writes_on_real_run(self, tmp_path: Path) -> None:
        bat = _bat_df([
            {"mlb_ID": 1, "name_common": "Player One", "WAR": 5.0},
        ])
        pitch = _pitch_df([])
        out_path = tmp_path / "config" / "stars.json"

        with (
            _mock_bwar_bat(bat),
            _mock_bwar_pitch(pitch),
            _mock_positions(),
            patch.object(refresh_stars, "STARS_JSON", out_path),
        ):
            code = main(["--season", "2024", "--min-war", "0"])

        assert code == 0
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert len(data) == 1
        assert data[0]["name"] == "Player One"

    def test_returns_1_on_failure(self) -> None:
        with patch.object(
            refresh_stars, "_fetch_batter_war", side_effect=RuntimeError("network")
        ):
            code = main(["--season", "2024"])

        assert code == 1

    def test_returns_1_when_no_players(self) -> None:
        bat = _bat_df([])
        pitch = _pitch_df([])

        with _mock_bwar_bat(bat), _mock_bwar_pitch(pitch), _mock_positions():
            code = main(["--season", "2024", "--min-war", "10.0"])

        assert code == 1

    def test_custom_args(self, tmp_path: Path) -> None:
        bat = _bat_df([
            {"mlb_ID": i, "name_common": f"Player {i}", "WAR": 10.0 - i * 0.5}
            for i in range(20)
        ])
        pitch = _pitch_df([])
        out_path = tmp_path / "stars.json"

        with (
            _mock_bwar_bat(bat),
            _mock_bwar_pitch(pitch),
            _mock_positions(),
            patch.object(refresh_stars, "STARS_JSON", out_path),
        ):
            code = main(["--season", "2024", "--top", "10", "--min-war", "5.0"])

        assert code == 0
        data = json.loads(out_path.read_text())
        assert len(data) == 10
        # All should have WAR >= 5.0 (players 0-10 have WAR 10.0 to 5.0)
