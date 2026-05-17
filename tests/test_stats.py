"""Tests for the season-stats fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mlbreview.data.stats import (
    BatterSeasonStats,
    _parse_player,
    fetch_batter_season_stats,
)


def _people_response(*players: dict) -> dict:
    return {"people": list(players)}


def _player_with_hitting(
    player_id: int = 545361,
    full_name: str = "Mike Trout",
    home_runs: int = 15,
    avg: str = ".285",
) -> dict:
    return {
        "id": player_id,
        "fullName": full_name,
        "stats": [
            {
                "type": {"displayName": "season"},
                "group": {"displayName": "hitting"},
                "splits": [
                    {
                        "stat": {
                            "homeRuns": home_runs,
                            "doubles": 20,
                            "triples": 2,
                            "hits": 85,
                            "rbi": 42,
                            "stolenBases": 5,
                            "avg": avg,
                        }
                    }
                ],
            }
        ],
    }


def _pitcher_only(player_id: int = 543037, full_name: str = "Gerrit Cole") -> dict:
    return {
        "id": player_id,
        "fullName": full_name,
        "stats": [
            {
                "type": {"displayName": "season"},
                "group": {"displayName": "pitching"},
                "splits": [{"stat": {"era": "3.21", "wins": 10}}],
            }
        ],
    }


class TestParsePlayer:
    def test_parses_hitting_stats(self):
        result = _parse_player(_player_with_hitting())
        assert result is not None
        assert result.full_name == "Mike Trout"
        assert result.home_runs == 15
        assert result.avg == ".285"
        assert result.doubles == 20

    def test_returns_none_for_pitcher(self):
        result = _parse_player(_pitcher_only())
        assert result is None

    def test_returns_none_for_empty_stats(self):
        result = _parse_player({"id": 1, "fullName": "Nobody", "stats": []})
        assert result is None


class TestFetchBatterSeasonStats:
    def test_empty_ids_returns_empty(self):
        client = MagicMock()
        result = fetch_batter_season_stats(set(), season=2026, client=client)
        assert result == {}

    @patch("mlbreview.data.stats.get_json")
    def test_fetches_and_parses(self, mock_get):
        mock_get.return_value = _people_response(
            _player_with_hitting(545361, "Mike Trout", 15, ".285"),
            _player_with_hitting(660271, "Shohei Ohtani", 22, ".301"),
        )
        client = MagicMock()
        result = fetch_batter_season_stats({545361, 660271}, season=2026, client=client)

        assert "Mike Trout" in result
        assert "Shohei Ohtani" in result
        assert result["Mike Trout"].home_runs == 15
        assert result["Shohei Ohtani"].home_runs == 22

        call_args = mock_get.call_args
        assert "personIds" in call_args.kwargs.get("params", call_args[1].get("params", {}))

    @patch("mlbreview.data.stats.get_json")
    def test_filters_out_pitchers(self, mock_get):
        mock_get.return_value = _people_response(
            _player_with_hitting(545361, "Mike Trout"),
            _pitcher_only(543037, "Gerrit Cole"),
        )
        client = MagicMock()
        result = fetch_batter_season_stats({545361, 543037}, season=2026, client=client)

        assert "Mike Trout" in result
        assert "Gerrit Cole" not in result

    @patch("mlbreview.data.stats.get_json")
    def test_api_error_returns_empty(self, mock_get):
        from mlbreview.data.client import MlbApiError
        mock_get.side_effect = MlbApiError("timeout")
        client = MagicMock()
        result = fetch_batter_season_stats({545361}, season=2026, client=client)
        assert result == {}
