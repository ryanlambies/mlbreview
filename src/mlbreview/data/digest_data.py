"""Serialize a rendered digest into the immutable per-day ``data.json``.

``data.json`` is the source-of-truth record of one day's digest: scores,
storylines, tonight's preview, roster moves, and the six leaderboards — each
leaderboard row carrying a stable ``player_id`` join key. It is the dumb,
immutable layer of the dashboard data contract: **no day-over-day trends, no
rank deltas, no sparkline series**. Those derived values are computed later by
the bundle builder (``scripts/build_dashboard_bundle.py``) into ``dashboard.json``.

Two-layer principle: changing the trend window or adding a derived metric must
touch only the bundle builder, never this serializer. Once written, a day's
``data.json`` never changes — the writer is write-once and refuses to overwrite.

The shape is validated in tests against ``schemas/data.schema.json``; the
serializer itself stays dependency-light (no jsonschema import at runtime).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from mlbreview.data.schedule import Game
from mlbreview.data.transactions import Transaction, TransactionCategory
from mlbreview.render.pages import Digest, Storyline, TonightPreview
from mlbreview.scoring.leaderboards import (
    Leaderboards,
    LeaderboardHitter,
    LeaderboardPitcher,
)

logger = logging.getLogger(__name__)

# A non-storyline game with this many combined runs is tagged a "Slugfest" on
# the scoreboard. Storyline games keep their richer drama-derived tag instead.
SLUGFEST_MIN_COMBINED_RUNS = 18

# data.json window labels per board, matching the real leaderboard windows
# (hot/cold are 7-day rolling; breakout requires 15-day confirmation).
_WINDOW_7D = "7-day"
_WINDOW_15D = "15-day"

# Drama category -> scoreboard/storyline display tag. Categories without a
# data.json enum equivalent (extra_innings, feat, default) map to None.
_CATEGORY_TAG: dict[str, str | None] = {
    "walkoff": "Walk-off",
    "comeback": "Comeback",
    "pitchers_duel": "Pitchers' Duel",
}

# Transaction category -> data.json transaction type enum. The current feed
# does not distinguish IL placement from activation, or recall from option, so
# ACT/OPT are reserved in the schema but not yet emitted.
_TXN_TYPE: dict[TransactionCategory, str] = {
    TransactionCategory.INJURED_LIST: "IL",
    TransactionCategory.CALL_UP: "REC",
    TransactionCategory.TRADE: "OTHER",
}


def _round(value: float | None, ndigits: int) -> float | None:
    return None if value is None else round(value, ndigits)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _score_tag(game: Game, storyline_category: str | None) -> str | None:
    """Scoreboard tag for a completed game.

    Storyline games surface their drama-derived tag (Walk-off / Comeback /
    Pitchers' Duel) when it maps to a data.json enum value. Every other game
    falls back to a tag derivable from the line score alone: a shutout (one
    side held scoreless) or a slugfest (high combined run total).
    """
    if storyline_category is not None:
        tag = _CATEGORY_TAG.get(storyline_category)
        if tag is not None:
            return tag
    if game.away_score == 0 or game.home_score == 0:
        return "Shutout"
    if game.away_score + game.home_score >= SLUGFEST_MIN_COMBINED_RUNS:
        return "Slugfest"
    return None


def _score_entry(game: Game, storyline_category: str | None) -> dict[str, Any]:
    return {
        "away": game.away_team_abbr,
        "away_score": game.away_score,
        "home": game.home_team_abbr,
        "home_score": game.home_score,
        "final": True,
        "tag": _score_tag(game, storyline_category),
    }


def _matchup(away_abbr: str, home_abbr: str) -> str:
    return f"{away_abbr} @ {home_abbr}"


def _storyline_entry(storyline: Storyline) -> dict[str, Any]:
    game = storyline.scored.game
    return {
        "matchup": _matchup(game.away_team_abbr, game.home_team_abbr),
        "score": f"{game.away_score}-{game.home_score}",
        "tag": _CATEGORY_TAG.get(storyline.scored.category),
        "body": storyline.prose,
    }


def _tonight_entry(preview: TonightPreview) -> dict[str, Any]:
    game = preview.scored.game
    broadcast: str | None = None
    national = [b for b in game.broadcasts if b.is_national]
    if national:
        broadcast = national[0].name
    elif game.broadcasts:
        broadcast = game.broadcasts[0].name
    return {
        "matchup": _matchup(game.away_team_abbr, game.home_team_abbr),
        "broadcast": broadcast,
        "note": preview.prose,
    }


def _transaction_entry(txn: Transaction) -> dict[str, Any]:
    return {
        "team": txn.team_name,
        "type": _TXN_TYPE.get(txn.category, "OTHER"),
        "player_id": txn.player_id,
        "player": txn.player_name,
        "pos": None,  # position not carried by the current transactions feed
        "detail": txn.description,
    }


def _hitter_row(hitter: LeaderboardHitter, rank: int, window: str) -> dict[str, Any]:
    return {
        "rank": rank,
        "player_id": hitter.player_id,
        "player": hitter.full_name,
        "team": hitter.team_abbr,
        "avg": _round(hitter.avg, 3),
        "hr": hitter.home_runs,
        "rbi": hitter.rbi,
        "ops": _round(hitter.obp + hitter.slg, 3),
        "obp": _round(hitter.obp, 3),
        "slg": _round(hitter.slg, 3),
        "games": hitter.games,
        "pa": hitter.plate_appearances,
        "sb": hitter.stolen_bases,
        "window": window,
    }


def _pitcher_row(pitcher: LeaderboardPitcher, rank: int, window: str) -> dict[str, Any]:
    return {
        "rank": rank,
        "player_id": pitcher.player_id,
        "player": pitcher.full_name,
        "team": pitcher.team_abbr,
        "era": _round(pitcher.era, 2),
        "ip": _round(pitcher.innings_pitched, 1),
        "k": pitcher.strikeouts,
        "role": pitcher.role,
        "appearances": pitcher.appearances,
        "gs": pitcher.starts,
        "sv": pitcher.saves,
        "bs": pitcher.blown_saves,
        "sv_pct": _round(pitcher.save_pct, 3),
        "whip": _round(pitcher.whip, 2),
        "k9": _round(pitcher.k_per_9, 1),
        "window": window,
    }


def _hitter_board(hitters: list[LeaderboardHitter], window: str) -> list[dict[str, Any]]:
    return [_hitter_row(h, i + 1, window) for i, h in enumerate(hitters)]


def _pitcher_board(pitchers: list[LeaderboardPitcher], window: str) -> list[dict[str, Any]]:
    return [_pitcher_row(p, i + 1, window) for i, p in enumerate(pitchers)]


def _leaderboards(boards: Leaderboards | None) -> dict[str, list[dict[str, Any]]]:
    if boards is None:
        return {
            "hot_hitters": [], "cold_hitters": [], "hot_pitchers": [],
            "cold_pitchers": [], "breakout_hitters": [], "breakout_pitchers": [],
        }
    return {
        "hot_hitters": _hitter_board(boards.hot_hitters, _WINDOW_7D),
        "cold_hitters": _hitter_board(boards.cold_hitters, _WINDOW_7D),
        "hot_pitchers": _pitcher_board(boards.hot_pitchers, _WINDOW_7D),
        "cold_pitchers": _pitcher_board(boards.cold_pitchers, _WINDOW_7D),
        "breakout_hitters": _hitter_board(boards.breakout_hitters, _WINDOW_15D),
        "breakout_pitchers": _pitcher_board(boards.breakout_pitchers, _WINDOW_15D),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_data_json(digest: Digest, *, generated_at: str) -> dict[str, Any]:
    """Build the immutable ``data.json`` dict for one day's digest.

    Parameters
    ----------
    digest : Digest
        The fully-assembled day's content the renderers consume.
    generated_at : str
        ISO-8601 timestamp for ``meta.generated_at`` (passed in so the output
        is deterministic and testable — this module does not read the clock).
    """
    # Map gamePk -> storyline drama category so scoreboard entries can surface
    # the richer storyline tag on the games that have one.
    storyline_category: dict[int, str] = {
        s.scored.game.gamePk: s.scored.category for s in digest.storylines
    }

    tonight = [_tonight_entry(digest.tonight)] if digest.tonight is not None else []

    return {
        "meta": {
            "date": digest.digest_date.isoformat(),
            "generated_at": generated_at,
            "season": str(digest.digest_date.year),
        },
        "scores": [
            _score_entry(g, storyline_category.get(g.gamePk)) for g in digest.games
        ],
        "storylines": [_storyline_entry(s) for s in digest.storylines],
        "tonight": tonight,
        "transactions": [_transaction_entry(t) for t in digest.transactions],
        "leaderboards": _leaderboards(digest.leaderboards),
    }


def data_json_path(base_dir: Path, digest_date: str) -> Path:
    """Canonical path for a day's ``data.json`` (alongside its ``index.html``)."""
    return base_dir / "digests" / digest_date / "data.json"


def write_data_json(
    digest: Digest, *, base_dir: Path, generated_at: str
) -> Path | None:
    """Write ``data.json`` for the digest's day. Write-once / immutable.

    Returns the written path, or ``None`` if a ``data.json`` already exists for
    the date (the immutable record is never overwritten — later cron slots on
    the same day are a no-op). The ``LuckStatus`` enum and the ``date`` field
    are coerced to JSON-native values by ``build_data_json``; nothing here needs
    a custom encoder.
    """
    date_str = digest.digest_date.isoformat()
    path = data_json_path(base_dir, date_str)
    if path.exists():
        logger.info("data.json already exists for %s — leaving immutable record", date_str)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_data_json(digest, generated_at=generated_at)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    logger.info("Wrote data.json to %s", path)
    return path


# ---------------------------------------------------------------------------
# index.json manifest
# ---------------------------------------------------------------------------


def index_json_path(base_dir: Path) -> Path:
    """Canonical path for the machine-readable digest manifest."""
    return base_dir / "digests" / "index.json"


def _available_dates(base_dir: Path) -> list[str]:
    """ISO dates of every ``digests/<date>/`` dir that has a ``data.json``.

    Newest first. A digest dir without ``data.json`` (e.g. a legacy day
    rendered before the data layer existed) is skipped — the manifest tracks
    the data layer the bundle builder consumes, not the human archive.
    """
    digests_dir = base_dir / "digests"
    if not digests_dir.exists():
        return []

    dates: list[str] = []
    for day_dir in digests_dir.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if (day_dir / "data.json").exists():
            dates.append(day_dir.name)

    dates.sort(reverse=True)  # ISO dates sort lexicographically == chronologically
    return dates


def build_index_json(base_dir: Path, *, updated: str) -> dict[str, Any]:
    """Build the ``index.json`` manifest dict by scanning the digests dir.

    Derived entirely from the filesystem, so it is idempotent: re-running over
    the same set of ``data.json`` files yields identical ``dates``/``latest``
    (only ``updated`` changes). Gaps in publishing are tolerated — the manifest
    lists whatever dates exist, newest first, and does not assume consecutive
    calendar dates.
    """
    dates = _available_dates(base_dir)
    return {
        "updated": updated,
        "latest": dates[0] if dates else None,
        "dates": dates,
    }


def write_index_json(base_dir: Path, *, updated: str) -> Path:
    """Rebuild and write ``digests/index.json``. Overwrites every run."""
    path = index_json_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_index_json(base_dir, updated=updated)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    logger.info("Wrote index.json manifest (%d dates) to %s", len(payload["dates"]), path)
    return path
