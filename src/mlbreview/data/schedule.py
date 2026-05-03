"""Schedule fetchers — yesterday's finals and tonight's previews.

Both endpoints are MLB Stats API `/schedule` calls with different hydrate sets.
Returned dataclasses are immutable, well-typed structures the rest of the
pipeline consumes — no business logic lives here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from mlbreview.data.client import BASE_URL_V1, get_json

logger = logging.getLogger(__name__)

# Regular season + postseason game types. Drops spring training (S) and
# exhibitions (E). Used for both finals and tonight queries.
ACTIVE_GAME_TYPES: frozenset[str] = frozenset({"R", "F", "D", "L", "W"})

# Status strings the MLB Stats API uses for "completed" games.
FINAL_STATUSES: frozenset[str] = frozenset(
    {"Final", "Game Over", "Completed Early"}
)


@dataclass(frozen=True)
class Decisions:
    winner: str | None
    loser: str | None
    save: str | None


@dataclass(frozen=True)
class InningLine:
    inning: int
    away_runs: int
    home_runs: int


@dataclass(frozen=True)
class Game:
    """A completed game from yesterday."""

    gamePk: int
    game_type: str
    status: str
    away_team_name: str
    away_team_abbr: str
    away_score: int
    home_team_name: str
    home_team_abbr: str
    home_score: int
    decisions: Decisions
    line_score: tuple[InningLine, ...]

    @property
    def margin(self) -> int:
        return abs(self.away_score - self.home_score)

    @property
    def extra_innings(self) -> bool:
        return len(self.line_score) > 9


@dataclass(frozen=True)
class ProbablePitcher:
    player_id: int | None
    full_name: str | None


@dataclass(frozen=True)
class Broadcast:
    name: str
    type: str
    is_national: bool


@dataclass(frozen=True)
class TonightGame:
    """A scheduled game for tonight's preview."""

    gamePk: int
    game_type: str
    game_date_utc: str
    away_team_name: str
    away_team_abbr: str
    away_record: str | None
    home_team_name: str
    home_team_abbr: str
    home_record: str | None
    away_probable_pitcher: ProbablePitcher | None
    home_probable_pitcher: ProbablePitcher | None
    broadcasts: tuple[Broadcast, ...]

    @property
    def is_national(self) -> bool:
        return any(b.is_national for b in self.broadcasts)


def _team_name(team_block: dict[str, Any], key: str) -> str:
    return team_block.get("team", {}).get(key, "")


def _league_record(team_block: dict[str, Any]) -> str | None:
    rec = team_block.get("leagueRecord") or {}
    wins = rec.get("wins")
    losses = rec.get("losses")
    if wins is None or losses is None:
        return None
    return f"{wins}-{losses}"


def _line_score(linescore: dict[str, Any]) -> tuple[InningLine, ...]:
    innings = linescore.get("innings") or []
    return tuple(
        InningLine(
            inning=int(inn.get("num") or i + 1),
            away_runs=int((inn.get("away") or {}).get("runs") or 0),
            home_runs=int((inn.get("home") or {}).get("runs") or 0),
        )
        for i, inn in enumerate(innings)
    )


def _decisions(game: dict[str, Any]) -> Decisions:
    raw = game.get("decisions") or {}
    return Decisions(
        winner=(raw.get("winner") or {}).get("fullName"),
        loser=(raw.get("loser") or {}).get("fullName"),
        save=(raw.get("save") or {}).get("fullName"),
    )


def _broadcasts(game: dict[str, Any]) -> tuple[Broadcast, ...]:
    raw = game.get("broadcasts") or []
    return tuple(
        Broadcast(
            name=b.get("name", ""),
            type=b.get("type", ""),
            is_national=bool(b.get("isNational", False)),
        )
        for b in raw
        if b.get("name")
    )


def _probable(team_block: dict[str, Any]) -> ProbablePitcher | None:
    pp = team_block.get("probablePitcher")
    if not pp:
        return None
    return ProbablePitcher(
        player_id=pp.get("id"),
        full_name=pp.get("fullName"),
    )


def parse_finals(payload: dict[str, Any]) -> list[Game]:
    """Parse a `/schedule` payload into completed `Game` objects.

    Filters to active game types and final statuses; everything else is
    silently dropped.
    """
    games: list[Game] = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("gameType") not in ACTIVE_GAME_TYPES:
                continue
            status = (g.get("status") or {}).get("detailedState", "")
            if status not in FINAL_STATUSES:
                continue
            teams = g.get("teams") or {}
            away = teams.get("away") or {}
            home = teams.get("home") or {}
            games.append(
                Game(
                    gamePk=int(g["gamePk"]),
                    game_type=g.get("gameType", "R"),
                    status=status,
                    away_team_name=_team_name(away, "name"),
                    away_team_abbr=_team_name(away, "abbreviation"),
                    away_score=int(away.get("score") or 0),
                    home_team_name=_team_name(home, "name"),
                    home_team_abbr=_team_name(home, "abbreviation"),
                    home_score=int(home.get("score") or 0),
                    decisions=_decisions(g),
                    line_score=_line_score(g.get("linescore") or {}),
                )
            )
    return games


def parse_tonight(payload: dict[str, Any]) -> list[TonightGame]:
    """Parse a `/schedule` payload into upcoming `TonightGame` objects.

    Filters to active game types. Status filtering is intentionally absent —
    games that have already started or ended are still returned, since the
    pipeline always asks for future-dated tonight schedules.
    """
    games: list[TonightGame] = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("gameType") not in ACTIVE_GAME_TYPES:
                continue
            teams = g.get("teams") or {}
            away = teams.get("away") or {}
            home = teams.get("home") or {}
            games.append(
                TonightGame(
                    gamePk=int(g["gamePk"]),
                    game_type=g.get("gameType", "R"),
                    game_date_utc=g.get("gameDate", ""),
                    away_team_name=_team_name(away, "name"),
                    away_team_abbr=_team_name(away, "abbreviation"),
                    away_record=_league_record(away),
                    home_team_name=_team_name(home, "name"),
                    home_team_abbr=_team_name(home, "abbreviation"),
                    home_record=_league_record(home),
                    away_probable_pitcher=_probable(away),
                    home_probable_pitcher=_probable(home),
                    broadcasts=_broadcasts(g),
                )
            )
    return games


def fetch_finals(target: date, *, client: httpx.Client) -> list[Game]:
    """Fetch all completed MLB games for `target` from the MLB Stats API."""
    payload = get_json(
        client,
        f"{BASE_URL_V1}/schedule",
        params={
            "sportId": 1,
            "date": target.isoformat(),
            "hydrate": "team,linescore,decisions",
        },
    )
    return parse_finals(payload)


def fetch_tonight(target: date, *, client: httpx.Client) -> list[TonightGame]:
    """Fetch tonight's scheduled MLB games with probable pitchers + broadcasts."""
    payload = get_json(
        client,
        f"{BASE_URL_V1}/schedule",
        params={
            "sportId": 1,
            "date": target.isoformat(),
            "hydrate": "team,linescore,probablePitcher,broadcasts",
        },
    )
    return parse_tonight(payload)
