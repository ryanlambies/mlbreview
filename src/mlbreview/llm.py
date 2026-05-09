"""LLM prose generation — storyline narratives and tonight-game previews.

Uses Claude Haiku 4.5 to generate 2-3 sentence blurbs strictly grounded in
structured game data. Never fabricates player names, stats, or plays.

Two public functions:
    write_storyline(scored, *, client) -> str
    write_preview(scored, *, client)   -> str

Both accept an optional `anthropic.Anthropic` client for testability. When
the LLM fails or hallucinates, they fall back to deterministic template prose
so the digest always ships.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import anthropic

from mlbreview.config import LLM_MAX_TOKENS, LLM_MODEL, LLM_RETRY_DELAY
from mlbreview.data.game import GameFeed, Play
from mlbreview.data.schedule import TonightGame
from mlbreview.scoring.drama import ScoredGame
from mlbreview.scoring.hype import ScoredTonightGame

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You write 2-3 sentence baseball storyline blurbs grounded strictly in "
    "the JSON facts provided. Never invent player names, stat lines, or plays. "
    "If the JSON does not contain a fact, do not state it. Plain prose, no "
    "markdown, no headlines."
)

# Regex for capitalized multi-word names (e.g. "Aaron Judge", "Shohei Ohtani").
# Requires exactly two or three capitalized words — avoids matching articles
# or team-name fragments like "The Dodgers".
_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]+\s+(?:[A-Z][a-z]+\s+)?[A-Z][a-z]+)\b")

# Common phrases that look like player names but aren't.
_KNOWN_NON_PLAYER_NAMES = frozenset({
    "All Star", "World Series", "American League", "National League",
    "Wild Card", "Division Series", "Opening Day", "Spring Training",
})


def _build_storyline_payload(scored: ScoredGame) -> dict[str, Any]:
    game = scored.game
    feed = scored.feed

    top_plays = sorted(feed.plays, key=lambda p: abs(p.wpa), reverse=True)[:3]

    known_names: set[str] = set()
    plays_data = []
    for p in top_plays:
        entry: dict[str, Any] = {
            "description": p.description,
            "inning": p.inning,
            "half_inning": p.half_inning,
            "wpa": round(p.wpa, 1),
        }
        if p.batter:
            entry["batter"] = p.batter
            known_names.add(p.batter)
        if p.pitcher:
            entry["pitcher"] = p.pitcher
            known_names.add(p.pitcher)
        plays_data.append(entry)

    if game.decisions.winner:
        known_names.add(game.decisions.winner)
    if game.decisions.loser:
        known_names.add(game.decisions.loser)
    if game.decisions.save:
        known_names.add(game.decisions.save)

    payload = {
        "away_team": game.away_team_name,
        "away_abbr": game.away_team_abbr,
        "away_score": game.away_score,
        "home_team": game.home_team_name,
        "home_abbr": game.home_team_abbr,
        "home_score": game.home_score,
        "category": scored.category,
        "margin": game.margin,
        "winning_pitcher": game.decisions.winner,
        "losing_pitcher": game.decisions.loser,
        "save_pitcher": game.decisions.save,
        "top_plays": plays_data,
    }

    if feed.biggest_play:
        bp = feed.biggest_play
        payload["decisive_moment"] = {
            "description": bp.description,
            "inning": bp.inning,
            "half_inning": bp.half_inning,
            "wpa": round(bp.wpa, 1),
            "batter": bp.batter,
        }
        if bp.batter:
            known_names.add(bp.batter)

    return payload, known_names


def _build_preview_payload(scored: ScoredTonightGame) -> dict[str, Any]:
    game = scored.game

    known_names: set[str] = set()

    away_pitcher = "TBD"
    home_pitcher = "TBD"
    if game.away_probable_pitcher and game.away_probable_pitcher.full_name:
        away_pitcher = game.away_probable_pitcher.full_name
        known_names.add(away_pitcher)
    if game.home_probable_pitcher and game.home_probable_pitcher.full_name:
        home_pitcher = game.home_probable_pitcher.full_name
        known_names.add(home_pitcher)

    broadcast_names = [b.name for b in game.broadcasts if b.is_national]

    payload = {
        "away_team": game.away_team_name,
        "away_abbr": game.away_team_abbr,
        "away_record": game.away_record,
        "home_team": game.home_team_name,
        "home_abbr": game.home_team_abbr,
        "home_record": game.home_record,
        "away_probable_pitcher": away_pitcher,
        "home_probable_pitcher": home_pitcher,
        "national_broadcasts": broadcast_names,
        "is_national": game.is_national,
    }

    return payload, known_names


_ARTICLES = {"The", "A", "An"}


def _extract_names(text: str) -> set[str]:
    """Extract capitalized multi-word sequences that look like player names."""
    matches = _NAME_PATTERN.findall(text)
    cleaned: set[str] = set()
    for m in matches:
        # Strip leading articles ("The Dodgers" → "Dodgers" — single word, skip)
        parts = m.split()
        if parts[0] in _ARTICLES:
            parts = parts[1:]
        name = " ".join(parts)
        if len(parts) >= 2 and name not in _KNOWN_NON_PLAYER_NAMES:
            cleaned.add(name)
    return cleaned


def _grounding_check(prose: str, known_names: set[str]) -> bool:
    """Return True if the prose passes grounding — all names are known."""
    mentioned = _extract_names(prose)
    if not mentioned:
        return True

    # Build a set of last names and full names for matching.
    # LLM might use just the last name ("Judge" vs "Aaron Judge").
    name_variants: set[str] = set(known_names)
    for name in known_names:
        parts = name.split()
        if len(parts) > 1:
            name_variants.add(parts[-1])

    for name in mentioned:
        # Check if the full multi-word name or any of its words match
        if name in name_variants:
            continue
        parts = name.split()
        if all(part in name_variants for part in parts):
            continue
        logger.warning(
            "Grounding check failed: '%s' not found in known names %s",
            name, known_names,
        )
        return False
    return True


def _call_llm(
    payload: dict[str, Any],
    known_names: set[str],
    fallback: str,
    *,
    client: anthropic.Anthropic,
) -> str:
    """Call the LLM with retry and grounding check, falling back on failure."""
    user_message = f"Game facts:\n{json.dumps(payload)}"

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=LLM_MODEL,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=LLM_MAX_TOKENS,
            )
            prose = response.content[0].text

            if not _grounding_check(prose, known_names):
                logger.warning("Grounding check failed, using template fallback")
                return fallback

            return prose

        except anthropic.APIError as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "Anthropic API error (attempt %d), retrying in %.1fs: %s",
                    attempt + 1, LLM_RETRY_DELAY, exc,
                )
                time.sleep(LLM_RETRY_DELAY)

    logger.warning(
        "Anthropic API failed after 2 attempts, using template fallback: %s",
        last_error,
    )
    return fallback


def _storyline_fallback(scored: ScoredGame) -> str:
    """Deterministic template prose when the LLM is unavailable or hallucinates."""
    game = scored.game
    parts = [f"{game.away_team_name} {game.away_score}, {game.home_team_name} {game.home_score}."]

    if game.decisions.winner:
        parts.append(f"{game.decisions.winner} earned the win.")

    bp = scored.feed.biggest_play
    if bp and bp.batter and bp.description:
        parts.append(f"Key moment: {bp.description}.")

    return " ".join(parts)


def _preview_fallback(scored: ScoredTonightGame) -> str:
    """Deterministic template prose for the tonight-game preview."""
    game = scored.game
    away_pitcher = "TBD"
    home_pitcher = "TBD"
    if game.away_probable_pitcher and game.away_probable_pitcher.full_name:
        away_pitcher = game.away_probable_pitcher.full_name
    if game.home_probable_pitcher and game.home_probable_pitcher.full_name:
        home_pitcher = game.home_probable_pitcher.full_name

    parts = [f"{game.away_team_name} at {game.home_team_name}."]
    parts.append(f"{away_pitcher} vs {home_pitcher}.")

    if game.is_national:
        national = [b.name for b in game.broadcasts if b.is_national]
        if national:
            parts.append(f"National broadcast on {national[0]}.")

    return " ".join(parts)


def write_storyline(
    scored: ScoredGame,
    *,
    client: anthropic.Anthropic,
) -> str:
    """Generate a 2-3 sentence storyline blurb for a completed game.

    Falls back to deterministic template prose if the LLM fails or
    hallucinates names not present in the game data.
    """
    if not scored.feed.has_data:
        game = scored.game
        return (
            f"{game.away_team_name} {game.away_score}, "
            f"{game.home_team_name} {game.home_score}."
        )

    payload, known_names = _build_storyline_payload(scored)
    fallback = _storyline_fallback(scored)
    return _call_llm(payload, known_names, fallback, client=client)


def write_preview(
    scored: ScoredTonightGame,
    *,
    client: anthropic.Anthropic,
) -> str:
    """Generate a 2-3 sentence preview blurb for tonight's most-hyped game.

    Falls back to deterministic template prose if the LLM fails or
    hallucinates names not present in the game data.
    """
    payload, known_names = _build_preview_payload(scored)
    fallback = _preview_fallback(scored)
    return _call_llm(payload, known_names, fallback, client=client)
