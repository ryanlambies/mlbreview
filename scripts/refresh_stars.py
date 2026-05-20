#!/usr/bin/env python3
"""Refresh config/stars.json with current-season WAR leaders.

Uses Baseball Reference WAR data via ``pybaseball`` (``bwar_bat`` /
``bwar_pitch``) to select the top players by Wins Above Replacement.
The star-player list powers the hype-score ``star_density`` sub-signal
for tonight's game previews.

The script is designed to run weekly (via a GitHub Actions workflow step
or manually) and produces a league-neutral list — no team-bias filtering
is needed because WAR is inherently cross-team.

Position data is enriched via the MLB Stats API ``/people`` endpoint so
the JSON output stays human-readable.

Usage::

    python scripts/refresh_stars.py [--season YEAR] [--top N] [--min-war FLOAT]

Defaults::

    --season  : current year
    --top     : 30
    --min-war : 2.0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

STARS_JSON = Path(__file__).resolve().parent.parent / "config" / "stars.json"

# Sensible defaults — configurable via CLI flags.
DEFAULT_TOP_N = 30
DEFAULT_MIN_WAR = 2.0

# How many batters vs pitchers to pull from each WAR table before
# combining.  We over-fetch from each side so the final merged list
# (sorted by WAR, trimmed to --top) can settle at whatever natural
# batter/pitcher mix the data yields.
_FETCH_POOL_MULTIPLIER = 2


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_batter_war(season: int) -> pd.DataFrame:
    """Return Baseball-Reference batting WAR for *season*, position players only."""
    from pybaseball import bwar_bat

    df = bwar_bat()
    mask = (df["year_ID"] == season) & (df["pitcher"] == "N")
    return df.loc[mask].copy()


def _fetch_pitcher_war(season: int) -> pd.DataFrame:
    """Return Baseball-Reference pitching WAR for *season*."""
    from pybaseball import bwar_pitch

    df = bwar_pitch()
    return df.loc[df["year_ID"] == season].copy()


def _lookup_positions(player_ids: list[int]) -> dict[int, str]:
    """Batch-lookup primary positions from the MLB Stats API.

    Returns a dict mapping MLBAM player ID → position abbreviation
    (e.g. ``"SS"``, ``"P"``).  On failure returns an empty dict so
    the caller can fall back to generic labels.
    """
    if not player_ids:
        return {}

    ids_str = ",".join(str(pid) for pid in player_ids)
    url = f"https://statsapi.mlb.com/api/v1/people?personIds={ids_str}"

    try:
        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("MLB API position lookup failed; using fallback positions")
        return {}

    positions: dict[int, str] = {}
    for person in resp.json().get("people", []):
        pid = person.get("id")
        abbr = person.get("primaryPosition", {}).get("abbreviation", "")
        if pid and abbr:
            positions[pid] = abbr
    return positions


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def build_star_list(
    season: int,
    top_n: int = DEFAULT_TOP_N,
    min_war: float = DEFAULT_MIN_WAR,
) -> list[dict]:
    """Build the star-player list for *season*.

    Steps:
    1. Fetch batter and pitcher WAR tables for *season*.
    2. Filter by ``min_war`` and take the top pool from each.
    3. Merge, deduplicate (Ohtani may appear in both), sort by WAR.
    4. Trim to ``top_n``.
    5. Look up positions via MLB API for human-readable output.

    Returns a list of dicts matching the ``config/stars.json`` schema::

        [{"id": 660271, "name": "Shohei Ohtani", "position": "DH"}, ...]
    """
    pool_size = top_n * _FETCH_POOL_MULTIPLIER

    # -- Batters --
    bat_df = _fetch_batter_war(season)
    if bat_df.empty:
        bat_df = pd.DataFrame(columns=["mlb_ID", "name_common", "WAR"])
    else:
        bat_df = bat_df[bat_df["WAR"] >= min_war]
    bat_df = bat_df.sort_values("WAR", ascending=False).head(pool_size)

    players: list[dict] = []
    seen_ids: set[int] = set()

    for _, row in bat_df.iterrows():
        mlb_id = row.get("mlb_ID")
        if pd.isna(mlb_id):
            continue
        pid = int(mlb_id)
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        players.append(
            {
                "id": pid,
                "name": str(row["name_common"]),
                "war": round(float(row["WAR"]), 2),
                "is_pitcher": False,
            }
        )

    # -- Pitchers --
    pitch_df = _fetch_pitcher_war(season)
    if pitch_df.empty:
        pitch_df = pd.DataFrame(columns=["mlb_ID", "name_common", "WAR"])
    else:
        pitch_df = pitch_df[pitch_df["WAR"] >= min_war]
    pitch_df = pitch_df.sort_values("WAR", ascending=False).head(pool_size)

    for _, row in pitch_df.iterrows():
        mlb_id = row.get("mlb_ID")
        if pd.isna(mlb_id):
            continue
        pid = int(mlb_id)
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        players.append(
            {
                "id": pid,
                "name": str(row["name_common"]),
                "war": round(float(row["WAR"]), 2),
                "is_pitcher": True,
            }
        )

    # -- Merge, sort, trim --
    players.sort(key=lambda p: p["war"], reverse=True)
    players = players[:top_n]

    if not players:
        logger.warning(
            "No players found for season %d with WAR >= %.1f", season, min_war
        )
        return []

    # -- Position enrichment --
    positions = _lookup_positions([p["id"] for p in players])

    result: list[dict] = []
    for p in players:
        pos = positions.get(p["id"])
        if pos is None:
            pos = "P" if p["is_pitcher"] else "DH"
        result.append(
            {
                "id": p["id"],
                "name": p["name"],
                "position": pos,
            }
        )

    return result


def write_stars_json(entries: list[dict], path: Path = STARS_JSON) -> None:
    """Write the star list to ``config/stars.json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh config/stars.json with current-season WAR leaders."
    )
    parser.add_argument(
        "--season",
        type=int,
        default=date.today().year,
        help="MLB season year (default: current year)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of players to include (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--min-war",
        type=float,
        default=DEFAULT_MIN_WAR,
        help=f"Minimum WAR threshold (default: {DEFAULT_MIN_WAR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the list without writing to disk.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info(
        "Fetching WAR leaders for %d (top %d, min WAR %.1f) …",
        args.season,
        args.top,
        args.min_war,
    )

    try:
        entries = build_star_list(
            season=args.season, top_n=args.top, min_war=args.min_war
        )
    except Exception:
        logger.error("Failed to fetch WAR data", exc_info=True)
        return 1

    if not entries:
        logger.error("No players found — stars.json not updated.")
        return 1

    # -- League-neutral audit --
    team_counts: dict[str, int] = {}
    for e in entries:
        # Position lookup doesn't give us team, but we can log the list
        pass
    logger.info(
        "Selected %d players: %s",
        len(entries),
        ", ".join(f"{e['name']} ({e['position']})" for e in entries[:5])
        + (" …" if len(entries) > 5 else ""),
    )

    if args.dry_run:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0

    write_stars_json(entries, STARS_JSON)
    logger.info("Wrote %d entries to %s", len(entries), STARS_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
