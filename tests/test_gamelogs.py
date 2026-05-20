"""Tests for the game-log fetcher (boxscore parsing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mlbreview.data.gamelogs import (
    _get_outs,
    _innings_to_outs,
    _parse_boxscore,
    _parse_closer,
    _parse_hitter,
    _parse_starter,
    _safe_int,
    fetch_daily_gamelogs,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_boxscore() -> dict[str, Any]:
    return json.loads((FIXTURES / "boxscore_sample.json").read_text())


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_normal_int(self):
        assert _safe_int(5) == 5

    def test_string_int(self):
        assert _safe_int("3") == 3

    def test_none(self):
        assert _safe_int(None) is None

    def test_bad_string(self):
        assert _safe_int("abc") is None

    def test_float_truncates(self):
        assert _safe_int(3.7) == 3


class TestInningsToOuts:
    def test_full_innings(self):
        assert _innings_to_outs("6.0") == 18

    def test_one_third(self):
        assert _innings_to_outs("6.1") == 19

    def test_two_thirds(self):
        assert _innings_to_outs("6.2") == 20

    def test_zero(self):
        assert _innings_to_outs("0.0") == 0

    def test_single_out(self):
        assert _innings_to_outs("0.1") == 1

    def test_none(self):
        assert _innings_to_outs(None) is None

    def test_integer_only(self):
        assert _innings_to_outs("7") == 21

    def test_numeric_input(self):
        # Some API responses may return a number instead of string.
        # float 6.1 → str "6.1" → 6*3 + 1 = 19 outs
        assert _innings_to_outs(6.1) == 19

    def test_bad_string(self):
        assert _innings_to_outs("abc") is None


# ---------------------------------------------------------------------------
# Parse hitter
# ---------------------------------------------------------------------------


class TestParseHitter:
    def test_normal_hitter(self):
        player = {
            "person": {"id": 660271, "fullName": "Aaron Judge"},
            "stats": {
                "batting": {
                    "plateAppearances": 5, "atBats": 4,
                    "hits": 2, "doubles": 1, "triples": 0,
                    "homeRuns": 1, "rbi": 3, "stolenBases": 0,
                    "baseOnBalls": 1, "strikeOuts": 1,
                },
            },
        }
        h = _parse_hitter(player, team_abbr="NYY")
        assert h is not None
        assert h.player_id == 660271
        assert h.full_name == "Aaron Judge"
        assert h.team_abbr == "NYY"
        assert h.plate_appearances == 5
        assert h.hits == 2
        assert h.home_runs == 1
        assert h.rbi == 3

    def test_skips_zero_pa(self):
        player = {
            "person": {"id": 100, "fullName": "Pitcher"},
            "stats": {
                "batting": {
                    "plateAppearances": 0, "atBats": 0,
                    "hits": 0, "doubles": 0, "triples": 0,
                    "homeRuns": 0, "rbi": 0, "stolenBases": 0,
                    "baseOnBalls": 0, "strikeOuts": 0,
                },
            },
        }
        assert _parse_hitter(player, team_abbr="TST") is None

    def test_skips_missing_pa(self):
        player = {
            "person": {"id": 100, "fullName": "No Stats"},
            "stats": {"batting": {}},
        }
        assert _parse_hitter(player, team_abbr="TST") is None

    def test_skips_no_person_id(self):
        player = {
            "person": {"fullName": "No ID"},
            "stats": {
                "batting": {"plateAppearances": 3},
            },
        }
        assert _parse_hitter(player, team_abbr="TST") is None

    def test_missing_stats_returns_none(self):
        player = {
            "person": {"id": 1, "fullName": "Ghost"},
            "stats": {},
        }
        assert _parse_hitter(player, team_abbr="TST") is None

    def test_handles_missing_optional_fields(self):
        """Fields that default to 0 when missing."""
        player = {
            "person": {"id": 1, "fullName": "Sparse"},
            "stats": {
                "batting": {
                    "plateAppearances": 1,
                    "atBats": 1,
                    "hits": 1,
                    # Everything else missing
                },
            },
        }
        h = _parse_hitter(player, team_abbr="TST")
        assert h is not None
        assert h.doubles == 0
        assert h.triples == 0
        assert h.home_runs == 0
        assert h.rbi == 0
        assert h.stolen_bases == 0
        assert h.walks == 0
        assert h.strikeouts == 0


# ---------------------------------------------------------------------------
# Parse starter
# ---------------------------------------------------------------------------


class TestParseStarter:
    def test_normal_starter(self):
        player = {
            "person": {"id": 543037, "fullName": "Gerrit Cole"},
            "stats": {
                "pitching": {
                    "gamesStarted": 1,
                    "inningsPitched": "6.2",
                    "outs": 20,
                    "hits": 4, "earnedRuns": 2,
                    "baseOnBalls": 1, "strikeOuts": 9,
                    "homeRuns": 1, "numberOfPitches": 102,
                },
            },
        }
        s = _parse_starter(player, team_abbr="NYY",
                           game_date="2026-05-18", opponent_abbr="BOS")
        assert s is not None
        assert s.player_id == 543037
        assert s.full_name == "Gerrit Cole"
        assert s.team_abbr == "NYY"
        assert s.game_date == "2026-05-18"
        assert s.opponent_abbr == "BOS"
        assert s.outs_recorded == 20  # 6.2 IP = 20 outs
        assert s.hits_allowed == 4
        assert s.earned_runs == 2
        assert s.strikeouts == 9

    def test_falls_back_to_innings_pitched(self):
        """When outs field is missing, parse inningsPitched string."""
        player = {
            "person": {"id": 100, "fullName": "Legacy Data"},
            "stats": {
                "pitching": {
                    "inningsPitched": "5.1",
                    "hits": 4, "earnedRuns": 2,
                    "baseOnBalls": 1, "strikeOuts": 5,
                    "homeRuns": 0, "numberOfPitches": 80,
                },
            },
        }
        s = _parse_starter(player, team_abbr="TST",
                           game_date="2026-05-18", opponent_abbr="OPP")
        assert s is not None
        assert s.outs_recorded == 16  # 5.1 = 5*3 + 1 = 16

    def test_skips_zero_outs(self):
        player = {
            "person": {"id": 100, "fullName": "No Outs"},
            "stats": {
                "pitching": {
                    "outs": 0,
                    "inningsPitched": "0.0",
                    "hits": 3, "earnedRuns": 5,
                    "baseOnBalls": 3, "strikeOuts": 0,
                    "homeRuns": 2, "numberOfPitches": 25,
                },
            },
        }
        assert _parse_starter(player, team_abbr="TST",
                              game_date="2026-05-18", opponent_abbr="OPP") is None

    def test_skips_no_pitching_stats(self):
        player = {
            "person": {"id": 1, "fullName": "No Pitch"},
            "stats": {"pitching": {}},
        }
        assert _parse_starter(player, team_abbr="TST",
                              game_date="2026-05-18", opponent_abbr="OPP") is None


# ---------------------------------------------------------------------------
# Parse closer
# ---------------------------------------------------------------------------


class TestGetOuts:
    def test_prefers_outs_field(self):
        pitching = {"outs": 20, "inningsPitched": "6.2"}
        assert _get_outs(pitching) == 20

    def test_falls_back_to_innings_pitched(self):
        pitching = {"inningsPitched": "6.2"}
        assert _get_outs(pitching) == 20

    def test_returns_none_when_both_missing(self):
        assert _get_outs({}) is None

    def test_outs_field_zero(self):
        pitching = {"outs": 0, "inningsPitched": "0.0"}
        assert _get_outs(pitching) == 0


class TestParseCloser:
    def test_normal_save(self):
        player = {
            "person": {"id": 665795, "fullName": "Luke Weaver"},
            "stats": {
                "pitching": {
                    "outs": 3,
                    "inningsPitched": "1.0",
                    "hits": 0, "earnedRuns": 0,
                    "baseOnBalls": 0, "strikeOuts": 2,
                    "homeRuns": 0, "numberOfPitches": 14,
                    "saves": 1, "blownSaves": 0, "holds": 0,
                },
            },
        }
        c = _parse_closer(player, team_abbr="NYY")
        assert c is not None
        assert c.player_id == 665795
        assert c.saves == 1
        assert c.outs_recorded == 3

    def test_hold(self):
        player = {
            "person": {"id": 622253, "fullName": "Clay Holmes"},
            "stats": {
                "pitching": {
                    "outs": 4,
                    "inningsPitched": "1.1",
                    "hits": 1, "earnedRuns": 0,
                    "baseOnBalls": 0, "strikeOuts": 1,
                    "homeRuns": 0, "numberOfPitches": 18,
                    "saves": 0, "blownSaves": 0, "holds": 1,
                },
            },
        }
        c = _parse_closer(player, team_abbr="NYY")
        assert c is not None
        assert c.holds == 1
        assert c.outs_recorded == 4

    def test_blown_save(self):
        player = {
            "person": {"id": 1, "fullName": "Blower"},
            "stats": {
                "pitching": {
                    "outs": 2,
                    "inningsPitched": "0.2",
                    "hits": 3, "earnedRuns": 2,
                    "baseOnBalls": 1, "strikeOuts": 0,
                    "homeRuns": 1, "numberOfPitches": 20,
                    "saves": 0, "blownSaves": 1, "holds": 0,
                },
            },
        }
        c = _parse_closer(player, team_abbr="TST")
        assert c is not None
        assert c.blown_saves == 1

    def test_closer_with_no_outs_data(self):
        """Closer with save activity but missing outs/inningsPitched defaults to 0."""
        player = {
            "person": {"id": 1, "fullName": "Quick Save"},
            "stats": {
                "pitching": {
                    # No "outs" or "inningsPitched" keys
                    "hits": 0, "earnedRuns": 0,
                    "baseOnBalls": 0, "strikeOuts": 1,
                    "homeRuns": 0, "numberOfPitches": 5,
                    "saves": 1, "blownSaves": 0, "holds": 0,
                },
            },
        }
        c = _parse_closer(player, team_abbr="TST")
        assert c is not None
        assert c.saves == 1
        assert c.outs_recorded == 0

    def test_skips_middle_reliever(self):
        """Middle reliever with no save/hold/blown save is excluded."""
        player = {
            "person": {"id": 1, "fullName": "Middle Guy"},
            "stats": {
                "pitching": {
                    "outs": 6,
                    "inningsPitched": "2.0",
                    "hits": 2, "earnedRuns": 1,
                    "baseOnBalls": 1, "strikeOuts": 2,
                    "homeRuns": 0, "numberOfPitches": 30,
                    "saves": 0, "blownSaves": 0, "holds": 0,
                },
            },
        }
        assert _parse_closer(player, team_abbr="TST") is None


# ---------------------------------------------------------------------------
# Full boxscore parse
# ---------------------------------------------------------------------------


class TestParseBoxscore:
    def test_parses_fixture(self):
        payload = _load_boxscore()
        hitters, starters, closers = _parse_boxscore(
            payload, game_date="2026-05-18",
        )

        # Hitters: Judge (5 PA), Rizzo (4 PA), Duran (4 PA)
        # Bench Player (0 PA) and pitchers (0 PA) excluded
        assert len(hitters) == 3
        names = {h.full_name for h in hitters}
        assert "Aaron Judge" in names
        assert "Anthony Rizzo" in names
        assert "Jarren Duran" in names
        assert "Bench Player" not in names

    def test_starters_identified(self):
        payload = _load_boxscore()
        _, starters, _ = _parse_boxscore(payload, game_date="2026-05-18")

        # Two starters: Cole (away) and Bello (home)
        assert len(starters) == 2
        starter_names = {s.full_name for s in starters}
        assert "Gerrit Cole" in starter_names
        assert "Brayan Bello" in starter_names

    def test_starter_fields(self):
        payload = _load_boxscore()
        _, starters, _ = _parse_boxscore(payload, game_date="2026-05-18")

        cole = next(s for s in starters if s.full_name == "Gerrit Cole")
        assert cole.team_abbr == "NYY"
        assert cole.opponent_abbr == "BOS"
        assert cole.game_date == "2026-05-18"
        assert cole.outs_recorded == 20  # 6.2 IP
        assert cole.strikeouts == 9

    def test_closers_identified(self):
        payload = _load_boxscore()
        _, _, closers = _parse_boxscore(payload, game_date="2026-05-18")

        # Closers: Holmes (hold=1), Weaver (save=1)
        # Bello is the home starter, not a closer
        assert len(closers) == 2
        closer_names = {c.full_name for c in closers}
        assert "Luke Weaver" in closer_names
        assert "Clay Holmes" in closer_names

    def test_empty_boxscore(self):
        hitters, starters, closers = _parse_boxscore({}, game_date="2026-05-18")
        assert hitters == []
        assert starters == []
        assert closers == []

    def test_missing_teams(self):
        payload = {"teams": {}}
        hitters, starters, closers = _parse_boxscore(payload, game_date="2026-05-18")
        assert hitters == []
        assert starters == []
        assert closers == []


# ---------------------------------------------------------------------------
# fetch_daily_gamelogs — integration
# ---------------------------------------------------------------------------


class TestFetchDailyGamelogs:
    def test_empty_game_list(self):
        """No games → empty results, no API calls."""
        import httpx
        # Client won't be used since no games are passed
        client = httpx.Client()
        try:
            hitters, starters, closers = fetch_daily_gamelogs(
                [], client=client, game_date="2026-05-18",
            )
            assert hitters == []
            assert starters == []
            assert closers == []
        finally:
            client.close()

    def test_api_failure_skips_game(self, mocker):
        """When one boxscore fetch fails, other games still succeed."""
        from mlbreview.data.client import MlbApiError

        call_count = 0

        def mock_get_json(client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "111111" in url:
                raise MlbApiError("boxscore fetch failed")
            return _load_boxscore()

        mocker.patch("mlbreview.data.gamelogs.get_json", side_effect=mock_get_json)
        import httpx
        client = httpx.Client()
        try:
            hitters, starters, closers = fetch_daily_gamelogs(
                [111111, 222222], client=client, game_date="2026-05-18",
            )
            # First game failed, second succeeded
            assert len(hitters) > 0
            assert call_count == 2
        finally:
            client.close()

    def test_combines_multiple_games(self, mocker):
        """Stats from multiple games are combined."""
        mocker.patch(
            "mlbreview.data.gamelogs.get_json",
            return_value=_load_boxscore(),
        )
        import httpx
        client = httpx.Client()
        try:
            hitters, starters, closers = fetch_daily_gamelogs(
                [111111, 222222], client=client, game_date="2026-05-18",
            )
            # Each game produces 3 hitters, 2 starters, 2 closers
            # Two games combined
            assert len(hitters) == 6
            assert len(starters) == 4
            assert len(closers) == 4
        finally:
            client.close()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_player_no_person_block(self):
        player = {
            "stats": {
                "batting": {
                    "plateAppearances": 3,
                    "atBats": 3,
                    "hits": 1,
                },
            },
        }
        # person block missing → no player_id → returns None
        assert _parse_hitter(player, team_abbr="TST") is None

    def test_pitcher_no_person_block(self):
        player = {
            "stats": {
                "pitching": {
                    "inningsPitched": "6.0",
                    "saves": 0, "blownSaves": 0, "holds": 0,
                },
            },
        }
        assert _parse_starter(player, team_abbr="TST",
                              game_date="2026-05-18", opponent_abbr="OPP") is None

    def test_innings_pitched_edge_cases(self):
        """Verify various IP formats are handled."""
        assert _innings_to_outs("9.0") == 27  # Complete game
        assert _innings_to_outs("0.2") == 2   # Two outs, no full inning
        assert _innings_to_outs("10.0") == 30  # Extra innings
        assert _innings_to_outs("0.0") == 0

    def test_two_way_player(self):
        """Player who both bats and pitches (e.g., Ohtani) produces both entries."""
        payload = {
            "teams": {
                "away": {
                    "team": {"abbreviation": "LAD"},
                    "players": {
                        "ID660271": {
                            "person": {"id": 660271, "fullName": "Shohei Ohtani"},
                            "stats": {
                                "batting": {
                                    "plateAppearances": 4,
                                    "atBats": 3,
                                    "hits": 2,
                                    "doubles": 1,
                                    "triples": 0,
                                    "homeRuns": 1,
                                    "rbi": 3,
                                    "stolenBases": 0,
                                    "baseOnBalls": 1,
                                    "strikeOuts": 0,
                                },
                                "pitching": {
                                    "gamesStarted": 1,
                                    "outs": 21,
                                    "hits": 3,
                                    "earnedRuns": 1,
                                    "baseOnBalls": 1,
                                    "strikeOuts": 10,
                                    "homeRuns": 0,
                                    "numberOfPitches": 95,
                                    "saves": 0,
                                    "blownSaves": 0,
                                    "holds": 0,
                                },
                            },
                        },
                    },
                },
                "home": {
                    "team": {"abbreviation": "OPP"},
                    "players": {},
                },
            },
        }
        hitters, starters, closers = _parse_boxscore(payload, game_date="2026-05-18")
        # Should appear as BOTH a hitter and a starter
        assert len(hitters) == 1
        assert hitters[0].full_name == "Shohei Ohtani"
        assert hitters[0].home_runs == 1
        assert len(starters) == 1
        assert starters[0].full_name == "Shohei Ohtani"
        assert starters[0].strikeouts == 10

    def test_pitcher_with_games_started_flag(self):
        """Starter identification uses gamesStarted field."""
        payload = {
            "teams": {
                "away": {
                    "team": {"abbreviation": "TST"},
                    "players": {
                        "ID1": {
                            "person": {"id": 1, "fullName": "Hitter"},
                            "stats": {
                                "batting": {
                                    "plateAppearances": 4,
                                    "atBats": 3,
                                    "hits": 1,
                                },
                                "pitching": {},
                            },
                        },
                        "ID2": {
                            "person": {"id": 2, "fullName": "The Starter"},
                            "stats": {
                                "batting": {"plateAppearances": 0},
                                "pitching": {
                                    "gamesStarted": 1,
                                    "outs": 18,
                                    "hits": 3,
                                    "earnedRuns": 1,
                                    "baseOnBalls": 1,
                                    "strikeOuts": 6,
                                    "homeRuns": 0,
                                    "numberOfPitches": 85,
                                    "saves": 0, "blownSaves": 0, "holds": 0,
                                },
                            },
                        },
                    },
                },
                "home": {
                    "team": {"abbreviation": "OPP"},
                    "players": {},
                },
            },
        }
        hitters, starters, closers = _parse_boxscore(payload, game_date="2026-05-18")
        assert len(hitters) == 1
        assert len(starters) == 1
        assert starters[0].full_name == "The Starter"
