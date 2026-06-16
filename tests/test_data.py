"""Tests for the MLB Stats API data fetch layer.

Drives the parsers off committed JSON fixtures (see `tests/fixtures/`,
captured by `scripts/capture_fixtures.py`). Exercises HTTP error paths via
mocked transports — no live network in CI.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from mlbreview.data.client import MlbApiError, get_json
from mlbreview.data.game import fetch_game_feed, parse_winprob
from mlbreview.data.schedule import (
    fetch_finals,
    fetch_tonight,
    parse_finals,
    parse_tonight,
)
from mlbreview.data.transactions import (
    TransactionCategory,
    fetch_transactions,
    parse_transactions,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _mock_client(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


# ---------------------------------------------------------------------------
# schedule.py — fetch_finals
# ---------------------------------------------------------------------------


def test_parse_finals_returns_completed_games() -> None:
    payload = _load("schedule_2025-08-15.json")
    games = parse_finals(payload)

    assert len(games) == 15
    pit_at_chc = next(
        g for g in games if g.away_team_abbr == "PIT" and g.home_team_abbr == "CHC"
    )
    assert pit_at_chc.away_score == 3
    assert pit_at_chc.home_score == 2
    assert pit_at_chc.margin == 1
    assert pit_at_chc.status == "Final"
    assert pit_at_chc.decisions.winner is not None
    assert len(pit_at_chc.line_score) >= 9


def test_parse_finals_filters_non_final_statuses() -> None:
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 1,
                        "gameType": "R",
                        "status": {"detailedState": "In Progress"},
                        "teams": {"away": {}, "home": {}},
                    },
                    {
                        "gamePk": 2,
                        "gameType": "R",
                        "status": {"detailedState": "Final"},
                        "teams": {
                            "away": {
                                "team": {"name": "A", "abbreviation": "A"},
                                "score": 5,
                            },
                            "home": {
                                "team": {"name": "B", "abbreviation": "B"},
                                "score": 4,
                            },
                        },
                        "linescore": {"innings": []},
                    },
                ]
            }
        ]
    }
    games = parse_finals(payload)
    assert len(games) == 1
    assert games[0].gamePk == 2


def test_parse_finals_drops_spring_training_game_type() -> None:
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 1,
                        "gameType": "S",
                        "status": {"detailedState": "Final"},
                        "teams": {
                            "away": {
                                "team": {"name": "A", "abbreviation": "A"},
                                "score": 1,
                            },
                            "home": {
                                "team": {"name": "B", "abbreviation": "B"},
                                "score": 0,
                            },
                        },
                    },
                ]
            }
        ]
    }
    assert parse_finals(payload) == []


def test_parse_finals_empty_payload_off_day() -> None:
    """Off-day fixture: zero games yields empty list, not error.

    Covers the data-layer half of AE3.
    """
    assert parse_finals({"dates": []}) == []
    assert parse_finals({}) == []


def test_fetch_finals_calls_correct_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_load("schedule_2025-08-15.json"))

    with _mock_client(handler) as client:
        games = fetch_finals(date(2025, 8, 15), client=client)

    assert "/api/v1/schedule" in captured["url"]
    assert "date=2025-08-15" in captured["url"]
    assert "sportId=1" in captured["url"]
    assert len(games) == 15


# ---------------------------------------------------------------------------
# schedule.py — fetch_tonight
# ---------------------------------------------------------------------------


def test_parse_tonight_includes_probable_pitcher_and_broadcasts() -> None:
    payload = _load("tonight_2025-08-16.json")
    games = parse_tonight(payload)

    assert len(games) >= 1
    nationally_aired = [g for g in games if g.is_national]
    assert nationally_aired, "fixture has at least one nationally televised game"

    g = games[0]
    assert g.away_team_abbr
    assert g.home_team_abbr
    if g.home_probable_pitcher is not None:
        assert g.home_probable_pitcher.full_name


def test_parse_tonight_handles_missing_probable_pitcher() -> None:
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 99,
                        "gameType": "R",
                        "gameDate": "2025-08-16T18:20:00Z",
                        "teams": {
                            "away": {
                                "team": {"name": "A", "abbreviation": "A"},
                                "leagueRecord": {"wins": 50, "losses": 50},
                            },
                            "home": {
                                "team": {"name": "B", "abbreviation": "B"},
                                "leagueRecord": {"wins": 60, "losses": 40},
                            },
                        },
                        "broadcasts": [],
                    }
                ]
            }
        ]
    }
    games = parse_tonight(payload)
    assert len(games) == 1
    assert games[0].away_probable_pitcher is None
    assert games[0].home_probable_pitcher is None
    assert games[0].is_national is False
    assert games[0].away_record == "50-50"


# ---------------------------------------------------------------------------
# game.py — fetch_game_feed
# ---------------------------------------------------------------------------


def test_parse_winprob_walkoff_has_high_max_swing() -> None:
    payload = _load("winprob_walkoff.json")
    feed = parse_winprob(payload, gamePk=776735)

    assert feed.has_data
    assert feed.max_wpa_swing > 30.0  # 34.6 in fixture
    assert feed.late_inning_max_wpa > 30.0
    assert feed.biggest_play is not None
    assert feed.biggest_play.inning >= 7


def test_parse_winprob_blowout_has_lower_max_swing() -> None:
    """Sanity calibration: blowout swing < walkoff swing."""
    walkoff = parse_winprob(_load("winprob_walkoff.json"), gamePk=1)
    blowout = parse_winprob(_load("winprob_blowout.json"), gamePk=2)
    assert walkoff.max_wpa_swing > blowout.max_wpa_swing


def test_parse_winprob_drops_plays_with_null_wpa() -> None:
    payload = [
        {
            "homeTeamWinProbabilityAdded": 5.0,
            "about": {"inning": 1, "halfInning": "top"},
            "result": {"description": "single", "event": "Single"},
            "matchup": {},
        },
        {
            "homeTeamWinProbabilityAdded": None,
            "about": {"inning": 2, "halfInning": "top"},
            "result": {"description": "skipped", "event": "Skip"},
            "matchup": {},
        },
        {
            "about": {"inning": 3, "halfInning": "top"},
            "result": {"description": "no wpa key", "event": "Skip"},
            "matchup": {},
        },
    ]
    feed = parse_winprob(payload, gamePk=1)
    assert len(feed.plays) == 1
    assert feed.plays[0].event == "Single"


def test_parse_winprob_late_inning_threshold_filters_correctly() -> None:
    payload = [
        {
            "homeTeamWinProbabilityAdded": 50.0,  # massive swing in 3rd
            "about": {"inning": 3, "halfInning": "top"},
            "result": {"description": "early grand slam", "event": "Home Run"},
            "matchup": {},
        },
        {
            "homeTeamWinProbabilityAdded": 10.0,  # smaller swing in 8th
            "about": {"inning": 8, "halfInning": "bottom"},
            "result": {"description": "late single", "event": "Single"},
            "matchup": {},
        },
    ]
    feed = parse_winprob(payload, gamePk=1, late_inning_threshold=7)
    assert feed.max_wpa_swing == 50.0
    assert feed.late_inning_max_wpa == 10.0


def test_parse_winprob_empty_payload() -> None:
    feed = parse_winprob([], gamePk=1)
    assert feed.has_data is False
    assert feed.max_wpa_swing == 0.0
    assert feed.late_inning_max_wpa == 0.0
    assert feed.biggest_play is None


def test_fetch_game_feed_calls_correct_endpoint() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_load("winprob_walkoff.json"))

    with _mock_client(handler) as client:
        feed = fetch_game_feed(776735, client=client)

    assert "/api/v1/game/776735/winProbability" in captured["url"]
    assert feed.has_data


# ---------------------------------------------------------------------------
# transactions.py
# ---------------------------------------------------------------------------


def test_parse_transactions_classifies_call_ups_and_il_moves() -> None:
    payload = _load("transactions_sample.json")
    txns = parse_transactions(payload)

    categories = {t.category for t in txns}
    assert TransactionCategory.CALL_UP in categories
    assert TransactionCategory.INJURED_LIST in categories

    call_ups = [t for t in txns if t.category == TransactionCategory.CALL_UP]
    assert len(call_ups) == 12

    il = [t for t in txns if t.category == TransactionCategory.INJURED_LIST]
    # 13 placements + 3 activations off the IL — both are news-brief-worthy.
    assert len(il) == 16


def test_parse_transactions_drops_unclassified_codes() -> None:
    payload = {
        "transactions": [
            {
                "id": 1,
                "date": "2025-08-14",
                "typeCode": "ASG",  # rehab assignment — not in news brief
                "description": "Sent on rehab",
                "person": {"fullName": "X"},
                "toTeam": {"name": "Y"},
            },
            {
                "id": 2,
                "date": "2025-08-14",
                "typeCode": "TR",  # trade
                "description": "traded",
                "person": {"fullName": "X"},
                "toTeam": {"name": "Y"},
            },
        ]
    }
    txns = parse_transactions(payload)
    assert len(txns) == 1
    assert txns[0].category == TransactionCategory.TRADE


def test_parse_transactions_carries_player_id() -> None:
    payload = _load("transactions_sample.json")
    txns = parse_transactions(payload)

    # Every surfaced transaction in the sample carries a person.id, so the
    # join key for the dashboard data layer is populated end-to-end.
    classified = [t for t in txns if t.player_name is not None]
    assert classified, "expected at least one named transaction in the sample"
    assert all(isinstance(t.player_id, int) for t in classified)

    # Spot-check a known row: the call-up / IL names map to their MLBAM ids.
    by_name = {t.player_name: t.player_id for t in txns}
    assert by_name.get("Blake Perkins") == 663368


def test_parse_transactions_player_id_none_when_feed_omits_it() -> None:
    payload = {
        "transactions": [
            {
                "id": 3,
                "date": "2025-08-14",
                "typeCode": "TR",
                "description": "traded",
                "person": {"fullName": "No Id Player"},  # no person.id
                "toTeam": {"name": "Y"},
            },
        ]
    }
    txns = parse_transactions(payload)
    assert len(txns) == 1
    assert txns[0].player_name == "No Id Player"
    assert txns[0].player_id is None


def test_parse_transactions_empty_payload() -> None:
    assert parse_transactions({"transactions": []}) == []
    assert parse_transactions({}) == []


def test_fetch_transactions_passes_sport_id_and_dates() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_load("transactions_sample.json"))

    with _mock_client(handler) as client:
        fetch_transactions(date(2025, 8, 14), date(2025, 8, 15), client=client)

    url = captured["url"]
    assert "sportId=1" in url
    assert "startDate=2025-08-14" in url
    assert "endDate=2025-08-15" in url


# ---------------------------------------------------------------------------
# client.py — error paths
# ---------------------------------------------------------------------------


def test_get_json_retries_once_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="oops")
        return httpx.Response(200, json={"ok": True})

    with _mock_client(handler) as client:
        result = get_json(client, "https://example.test/x")

    assert result == {"ok": True}
    assert calls["n"] == 2


def test_get_json_raises_after_persistent_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with _mock_client(handler) as client:
        with pytest.raises(MlbApiError) as exc_info:
            get_json(client, "https://example.test/x")
    assert "503" in str(exc_info.value)


def test_get_json_raises_on_non_200_non_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    with _mock_client(handler) as client:
        with pytest.raises(MlbApiError):
            get_json(client, "https://example.test/x")


def test_get_json_raises_on_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with _mock_client(handler) as client:
        with pytest.raises(MlbApiError):
            get_json(client, "https://example.test/x")
