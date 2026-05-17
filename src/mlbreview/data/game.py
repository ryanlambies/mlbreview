"""Game-feed fetcher — pulls the play list with WPA from `/winProbability`.

The MLB Stats API exposes per-play Win Probability Added (in percentage
points, 0–100 scale) at `/api/v1/game/{gamePk}/winProbability`. Leverage Index
is **not** populated on the public endpoints, so the drama formula uses
late-inning peak |WPA| as a proxy for the leverage term — see `docs/formulas.md`
(landing in U3) for the full rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from mlbreview.data.client import BASE_URL_V1, get_json
from mlbreview.config import DRAMA_LATE_INNING_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Play:
    """One play from the win-probability feed.

    `wpa` is signed home-team Win Probability Added in percentage points
    (e.g. -34.6 means the home team's win probability dropped 34.6 points).
    Consumers take `abs(wpa)` for swing magnitude.
    """

    description: str
    event: str
    inning: int
    half_inning: str
    wpa: float
    home_win_probability: float | None
    away_win_probability: float | None
    batter: str | None
    batter_id: int | None
    pitcher: str | None
    pitcher_id: int | None


@dataclass(frozen=True)
class GameFeed:
    """Aggregate play-level data for a single completed game."""

    gamePk: int
    plays: tuple[Play, ...]
    max_wpa_swing: float
    late_inning_max_wpa: float
    biggest_play: Play | None

    @property
    def has_data(self) -> bool:
        return len(self.plays) > 0


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_winprob(
    payload: list[dict[str, Any]],
    *,
    gamePk: int,
    late_inning_threshold: int = DRAMA_LATE_INNING_THRESHOLD,
) -> GameFeed:
    """Parse a `/winProbability` payload into a `GameFeed`.

    Plays without a numeric `homeTeamWinProbabilityAdded` are silently dropped
    rather than surfaced as `None` to scoring (per scope boundary in the V1
    plan).
    """
    plays: list[Play] = []
    max_swing = 0.0
    late_max = 0.0
    biggest: Play | None = None

    for raw in payload:
        wpa = _safe_float(raw.get("homeTeamWinProbabilityAdded"))
        if wpa is None:
            continue

        about = raw.get("about") or {}
        result = raw.get("result") or {}
        matchup = raw.get("matchup") or {}

        try:
            inning = int(about.get("inning") or 0)
        except (TypeError, ValueError):
            inning = 0

        batter_block = matchup.get("batter") or {}
        pitcher_block = matchup.get("pitcher") or {}

        play = Play(
            description=result.get("description") or "",
            event=result.get("event") or "",
            inning=inning,
            half_inning=about.get("halfInning") or "",
            wpa=wpa,
            home_win_probability=_safe_float(raw.get("homeTeamWinProbability")),
            away_win_probability=_safe_float(raw.get("awayTeamWinProbability")),
            batter=batter_block.get("fullName"),
            batter_id=batter_block.get("id"),
            pitcher=pitcher_block.get("fullName"),
            pitcher_id=pitcher_block.get("id"),
        )
        plays.append(play)

        swing = abs(wpa)
        if swing > max_swing:
            max_swing = swing
            biggest = play
        if inning >= late_inning_threshold and swing > late_max:
            late_max = swing

    return GameFeed(
        gamePk=int(gamePk),
        plays=tuple(plays),
        max_wpa_swing=max_swing,
        late_inning_max_wpa=late_max,
        biggest_play=biggest,
    )


def fetch_game_feed(
    gamePk: int,
    *,
    client: httpx.Client,
    late_inning_threshold: int = DRAMA_LATE_INNING_THRESHOLD,
) -> GameFeed:
    """Fetch the win-probability play list for one game."""
    payload = get_json(client, f"{BASE_URL_V1}/game/{gamePk}/winProbability")
    if not isinstance(payload, list):
        # The API normally returns a JSON array; defend against shape drift.
        logger.warning("winProbability payload for %s was not a list", gamePk)
        payload = []
    return parse_winprob(
        payload,
        gamePk=gamePk,
        late_inning_threshold=late_inning_threshold,
    )
