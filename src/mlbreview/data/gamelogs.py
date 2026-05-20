"""Game-log fetcher — pulls per-player box score stats for snapshot writing.

Fetches box scores from the MLB Stats API ``/game/{gamePk}/boxscore``
endpoint and extracts individual hitter and pitcher stats for each player
who appeared in the game.  The results feed into daily snapshots (U1)
which are then aggregated into rolling stats (leaderboards.py).

Design decisions:
- **One API call per game:** the boxscore endpoint contains both teams'
  stats in a single response, so we iterate over yesterday's completed
  games and fetch each boxscore.  A typical day has ~15 games = 15 calls.
- **Field filtering:** the ``fields`` parameter reduces payloads from
  ~173 KB to ~17 KB per game (10× reduction), so a full 15-game day
  downloads ~255 KB instead of ~2.6 MB.
- **Starter vs. closer classification:** a pitcher with ``gamesStarted == 1``
  is classified as a starter.  All other pitchers with save/hold/blown-save
  activity are classified as closers.  Pitchers with none of the above are
  skipped (middle relievers are not tracked for V2 leaderboards).
- **Outs encoding:** the API provides both ``inningsPitched`` (baseball
  notation, e.g. ``"6.1"`` = 6⅓ IP) and ``outs`` (integer).  We prefer
  ``outs`` when available, falling back to parsing ``inningsPitched``.
- **Graceful degradation:** if a boxscore fetch fails, that game is skipped.
  The snapshot will have fewer players but the pipeline continues.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mlbreview.data.client import BASE_URL_V1, MlbApiError, get_json
from mlbreview.data.snapshots import (
    CloserDayStats,
    HitterDayStats,
    StarterGameStats,
)

logger = logging.getLogger(__name__)

# Field filter for the boxscore endpoint — reduces payload ~10× (~173 KB → ~17 KB).
# Only request the fields we actually parse.
_BOXSCORE_FIELDS = ",".join([
    "teams", "away", "home", "players", "person", "id", "fullName",
    "stats", "batting", "pitching", "team", "name", "abbreviation",
    "plateAppearances", "atBats", "hits", "doubles", "triples",
    "homeRuns", "rbi", "stolenBases", "baseOnBalls", "strikeOuts",
    "gamesStarted", "inningsPitched", "earnedRuns", "numberOfPitches",
    "saves", "blownSaves", "holds", "outs",
])


# ---------------------------------------------------------------------------
# Boxscore parsing — hitters
# ---------------------------------------------------------------------------


def _parse_hitter(
    player: dict[str, Any],
    *,
    team_abbr: str,
) -> HitterDayStats | None:
    """Extract a single hitter's stats from a boxscore player entry.

    Returns None if the player did not bat (e.g. pitcher in AL game
    who was substituted before batting).
    """
    person = player.get("person") or {}
    stats = player.get("stats") or {}
    batting = stats.get("batting") or {}

    # Skip players who didn't get a plate appearance
    plate_appearances = _safe_int(batting.get("plateAppearances"))
    if plate_appearances is None or plate_appearances == 0:
        return None

    player_id = person.get("id")
    if player_id is None:
        return None

    return HitterDayStats(
        player_id=int(player_id),
        full_name=person.get("fullName", ""),
        team_abbr=team_abbr,
        plate_appearances=plate_appearances,
        at_bats=_safe_int(batting.get("atBats")) or 0,
        hits=_safe_int(batting.get("hits")) or 0,
        doubles=_safe_int(batting.get("doubles")) or 0,
        triples=_safe_int(batting.get("triples")) or 0,
        home_runs=_safe_int(batting.get("homeRuns")) or 0,
        rbi=_safe_int(batting.get("rbi")) or 0,
        stolen_bases=_safe_int(batting.get("stolenBases")) or 0,
        walks=_safe_int(batting.get("baseOnBalls")) or 0,
        strikeouts=_safe_int(batting.get("strikeOuts")) or 0,
    )


# ---------------------------------------------------------------------------
# Boxscore parsing — pitchers
# ---------------------------------------------------------------------------


def _parse_starter(
    player: dict[str, Any],
    *,
    team_abbr: str,
    game_date: str,
    opponent_abbr: str,
) -> StarterGameStats | None:
    """Extract a starting pitcher's stats from a boxscore player entry.

    Returns None if the player has no pitching stats.
    """
    person = player.get("person") or {}
    stats = player.get("stats") or {}
    pitching = stats.get("pitching") or {}

    player_id = person.get("id")
    if player_id is None:
        return None

    outs = _get_outs(pitching)
    if outs is None or outs == 0:
        return None

    return StarterGameStats(
        player_id=int(player_id),
        full_name=person.get("fullName", ""),
        team_abbr=team_abbr,
        game_date=game_date,
        opponent_abbr=opponent_abbr,
        outs_recorded=outs,
        hits_allowed=_safe_int(pitching.get("hits")) or 0,
        earned_runs=_safe_int(pitching.get("earnedRuns")) or 0,
        walks=_safe_int(pitching.get("baseOnBalls")) or 0,
        strikeouts=_safe_int(pitching.get("strikeOuts")) or 0,
        home_runs_allowed=_safe_int(pitching.get("homeRuns")) or 0,
        pitches_thrown=_safe_int(pitching.get("numberOfPitches")) or 0,
    )


def _parse_closer(
    player: dict[str, Any],
    *,
    team_abbr: str,
) -> CloserDayStats | None:
    """Extract a closer/reliever's stats from a boxscore player entry.

    Only tracks pitchers with save, blown save, or hold activity.
    Middle relievers without any of these are excluded from leaderboards.
    """
    person = player.get("person") or {}
    stats = player.get("stats") or {}
    pitching = stats.get("pitching") or {}

    player_id = person.get("id")
    if player_id is None:
        return None

    saves = _safe_int(pitching.get("saves")) or 0
    blown_saves = _safe_int(pitching.get("blownSaves")) or 0
    holds = _safe_int(pitching.get("holds")) or 0

    if saves == 0 and blown_saves == 0 and holds == 0:
        return None

    outs = _get_outs(pitching)

    return CloserDayStats(
        player_id=int(player_id),
        full_name=person.get("fullName", ""),
        team_abbr=team_abbr,
        outs_recorded=outs or 0,
        earned_runs=_safe_int(pitching.get("earnedRuns")) or 0,
        saves=saves,
        blown_saves=blown_saves,
        holds=holds,
        strikeouts=_safe_int(pitching.get("strikeOuts")) or 0,
        walks=_safe_int(pitching.get("baseOnBalls")) or 0,
    )


# ---------------------------------------------------------------------------
# Boxscore parsing — full game
# ---------------------------------------------------------------------------


def _parse_boxscore(
    payload: dict[str, Any],
    *,
    game_date: str,
) -> tuple[list[HitterDayStats], list[StarterGameStats], list[CloserDayStats]]:
    """Parse a ``/game/{gamePk}/boxscore`` payload into player stats.

    Returns three lists: hitters, starters, closers.
    """
    hitters: list[HitterDayStats] = []
    starters: list[StarterGameStats] = []
    closers: list[CloserDayStats] = []

    teams = payload.get("teams") or {}
    for side in ("away", "home"):
        team_block = teams.get(side) or {}
        team_info = team_block.get("team") or {}
        team_abbr = team_info.get("abbreviation", "")
        opponent_side = "home" if side == "away" else "away"
        opp_block = teams.get(opponent_side) or {}
        opp_info = opp_block.get("team") or {}
        opponent_abbr = opp_info.get("abbreviation", "")

        players = team_block.get("players") or {}

        for player_key, player in players.items():
            # --- Hitter stats ---
            hitter = _parse_hitter(player, team_abbr=team_abbr)
            if hitter is not None:
                hitters.append(hitter)

            # --- Pitcher stats ---
            stats = player.get("stats") or {}
            pitching = stats.get("pitching") or {}
            games_started = _safe_int(pitching.get("gamesStarted")) or 0

            if games_started >= 1:
                # Starting pitcher (identified by gamesStarted == 1)
                starter = _parse_starter(
                    player, team_abbr=team_abbr,
                    game_date=game_date, opponent_abbr=opponent_abbr,
                )
                if starter is not None:
                    starters.append(starter)
            else:
                # Check if this reliever qualifies as a closer
                closer = _parse_closer(player, team_abbr=team_abbr)
                if closer is not None:
                    closers.append(closer)

    return hitters, starters, closers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_game_boxscore(
    gamePk: int,
    *,
    client: httpx.Client,
    game_date: str,
) -> tuple[list[HitterDayStats], list[StarterGameStats], list[CloserDayStats]]:
    """Fetch and parse a single game's boxscore.

    Returns three lists of player stats.  Raises ``MlbApiError`` on
    network or HTTP failure.
    """
    payload = get_json(
        client,
        f"{BASE_URL_V1}/game/{gamePk}/boxscore",
        params={"fields": _BOXSCORE_FIELDS},
    )
    return _parse_boxscore(payload, game_date=game_date)


def fetch_daily_gamelogs(
    game_pks: list[int],
    *,
    client: httpx.Client,
    game_date: str,
) -> tuple[list[HitterDayStats], list[StarterGameStats], list[CloserDayStats]]:
    """Fetch boxscores for all games and combine player stats.

    Skips individual games on failure (logs a warning) so a single bad
    boxscore doesn't lose all data for the day.  Returns combined lists
    of all player stats across all games.

    Parameters
    ----------
    game_pks
        List of gamePk IDs for completed games.
    client
        Configured httpx.Client.
    game_date
        Date string (YYYY-MM-DD) for starter game_date field.
    """
    all_hitters: list[HitterDayStats] = []
    all_starters: list[StarterGameStats] = []
    all_closers: list[CloserDayStats] = []

    for gamePk in game_pks:
        try:
            hitters, starters, closers = fetch_game_boxscore(
                gamePk, client=client, game_date=game_date,
            )
            all_hitters.extend(hitters)
            all_starters.extend(starters)
            all_closers.extend(closers)
        except MlbApiError:
            logger.warning(
                "Failed to fetch boxscore for gamePk=%d; skipping", gamePk,
            )

    logger.info(
        "Fetched daily gamelogs for %d games: %d hitters, %d starters, %d closers",
        len(game_pks), len(all_hitters), len(all_starters), len(all_closers),
    )
    return all_hitters, all_starters, all_closers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(value: object) -> int | None:
    """Convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _get_outs(pitching: dict[str, Any]) -> int | None:
    """Get total outs recorded from a pitching stats dict.

    Prefers the ``outs`` field (integer) when available, falling back to
    parsing ``inningsPitched`` (baseball notation string).
    """
    outs = _safe_int(pitching.get("outs"))
    if outs is not None:
        return outs
    return _innings_to_outs(pitching.get("inningsPitched"))


def _innings_to_outs(ip_str: object) -> int | None:
    """Convert MLB innings-pitched string to total outs.

    The MLB API uses ``"6.1"`` to mean 6 and 1/3 innings (19 outs),
    not 6.1 innings.  The fractional part is always 0, 1, or 2
    (representing 0, 1, or 2 additional outs beyond full innings).

    Examples:
        "6.0" → 18 outs
        "6.1" → 19 outs
        "6.2" → 20 outs
        "0.1" → 1 out
    """
    if ip_str is None:
        return None
    try:
        s = str(ip_str)
        if "." in s:
            parts = s.split(".")
            full_innings = int(parts[0])
            extra_outs = int(parts[1])
            return full_innings * 3 + extra_outs
        else:
            return int(s) * 3
    except (ValueError, IndexError):
        return None
