"""Season-stats fetcher — batch-loads hitting totals for featured batters.

Uses the MLB Stats API ``/people`` endpoint with the ``hydrate`` parameter to
pull season hitting stats in a single HTTP call.  The pipeline calls this after
identifying top-play batters so the LLM can reference grounded season tallies
(e.g. "his 15th home run") instead of hallucinating them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from mlbreview.data.client import BASE_URL_V1, MlbApiError, get_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BatterSeasonStats:
    player_id: int
    full_name: str
    home_runs: int
    doubles: int
    triples: int
    hits: int
    rbi: int
    stolen_bases: int
    avg: str


def _parse_hitting(stat_block: dict[str, Any]) -> dict[str, Any] | None:
    """Find the season hitting split from a player's stats list."""
    for group in stat_block.get("stats", []):
        if group.get("type", {}).get("displayName") != "season":
            continue
        if group.get("group", {}).get("displayName") != "hitting":
            continue
        splits = group.get("splits") or []
        if splits:
            return splits[0].get("stat")
    return None


def _parse_player(person: dict[str, Any]) -> BatterSeasonStats | None:
    stats = _parse_hitting(person)
    if stats is None:
        return None
    return BatterSeasonStats(
        player_id=int(person["id"]),
        full_name=person.get("fullName", ""),
        home_runs=int(stats.get("homeRuns", 0)),
        doubles=int(stats.get("doubles", 0)),
        triples=int(stats.get("triples", 0)),
        hits=int(stats.get("hits", 0)),
        rbi=int(stats.get("rbi", 0)),
        stolen_bases=int(stats.get("stolenBases", 0)),
        avg=stats.get("avg", ".000"),
    )


def fetch_batter_season_stats(
    player_ids: set[int],
    *,
    season: int,
    client: httpx.Client,
) -> dict[str, BatterSeasonStats]:
    """Fetch season hitting stats for a set of batter IDs.

    Returns a dict keyed by full name (matching the names in ``Play.batter``)
    so the LLM payload builder can look up stats by name.  Players without
    hitting stats (pitchers, missing data) are silently omitted.
    """
    if not player_ids:
        return {}

    ids_param = ",".join(str(pid) for pid in sorted(player_ids))
    hydrate = f"stats(type=season,season={season},group=[hitting])"

    try:
        payload = get_json(
            client,
            f"{BASE_URL_V1}/people",
            params={"personIds": ids_param, "hydrate": hydrate},
        )
    except MlbApiError:
        logger.warning("Failed to fetch season stats for %d players", len(player_ids))
        return {}

    result: dict[str, BatterSeasonStats] = {}
    for person in payload.get("people", []):
        parsed = _parse_player(person)
        if parsed is not None:
            result[parsed.full_name] = parsed

    return result
