"""Pipeline orchestrator — ties data, scoring, LLM, and rendering together.

Single entry point: ``run(target_date, dry_run, out_dir)``. Fetches MLB data,
scores storylines and tonight's game, generates LLM prose, renders the
dashboard and email, and sends the email via Resend. Handles off-day and
season-pause branches.

The orchestrator owns sequencing only — all business logic lives in
submodules. The GitHub Actions workflow (U7) calls this via ``__main__.py``.

Duplicate-send protection comes from the idempotency guard alone: the workflow
runs three cron slots per day (09:30, 10:30, 11:30 UTC) for delay tolerance,
and the second/third runs no-op once ``public/digests/{target_date}/index.html``
exists on gh-pages.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import anthropic
import httpx
import resend

from mlbreview.config import Config
from mlbreview.data.client import MlbApiError, make_client
from mlbreview.data.game import GameFeed, fetch_game_feed
from mlbreview.data.schedule import Game, fetch_finals, fetch_tonight
from mlbreview.data.stats import BatterSeasonStats, fetch_batter_season_stats
from mlbreview.data.transactions import fetch_transactions
from mlbreview.llm import write_preview, write_storyline
from mlbreview.render.pages import (
    Digest,
    IndexEntry,
    Storyline,
    TonightPreview,
    render_dashboard_day,
    render_dashboard_index,
    render_email_html,
    render_email_text,
)
from mlbreview.scoring.drama import ScoredGame, score_games
from mlbreview.scoring.hype import (
    GameContext,
    ScoredTonightGame,
    load_star_ids,
    select_most_hyped,
)
from mlbreview.scoring.variety import apply_variety_rule

logger = logging.getLogger(__name__)

OPENING_DAY_MONTH = 3
OPENING_DAY_DAY = 20
SEASON_END_MONTH = 11
SEASON_END_DAY = 10


def _is_active_season(target: date) -> bool:
    month_day = (target.month, target.day)
    return (OPENING_DAY_MONTH, OPENING_DAY_DAY) <= month_day <= (SEASON_END_MONTH, SEASON_END_DAY)


def _fetch_game_feeds(
    games: list[Game], *, client: httpx.Client
) -> dict[int, GameFeed]:
    feeds: dict[int, GameFeed] = {}
    for game in games:
        try:
            feeds[game.gamePk] = fetch_game_feed(game.gamePk, client=client)
        except MlbApiError:
            logger.warning(
                "Failed to fetch game feed for gamePk=%d (%s vs %s); skipping",
                game.gamePk, game.away_team_abbr, game.home_team_abbr,
            )
    return feeds


def _build_hype_contexts(
    tonight_games: list,
) -> dict[int, GameContext]:
    contexts: dict[int, GameContext] = {}
    for game in tonight_games:
        away_era: float | None = None
        home_era: float | None = None

        same_division = False
        both_above_500 = _both_above_500(game)

        contexts[game.gamePk] = GameContext(
            away_pitcher_era=away_era,
            home_pitcher_era=home_era,
            same_division=same_division,
            both_above_500=both_above_500,
            playoff_delta=None,
            away_roster_ids=frozenset(),
            home_roster_ids=frozenset(),
        )
    return contexts


def _both_above_500(game) -> bool:
    try:
        away = game.away_record
        home = game.home_record
        if not away or not home:
            return False
        aw, al = away.split("-")
        hw, hl = home.split("-")
        return int(aw) > int(al) and int(hw) > int(hl)
    except (ValueError, AttributeError):
        return False


def _collect_batter_ids(top_games: list[ScoredGame]) -> set[int]:
    """Extract unique batter IDs from top plays across storyline games."""
    ids: set[int] = set()
    for scored in top_games:
        top_plays = sorted(
            scored.feed.plays, key=lambda p: abs(p.wpa), reverse=True
        )[:3]
        for play in top_plays:
            if play.batter_id is not None:
                ids.add(play.batter_id)
        if scored.feed.biggest_play and scored.feed.biggest_play.batter_id is not None:
            ids.add(scored.feed.biggest_play.batter_id)
    return ids


def _generate_storyline_prose(
    top_games: list[ScoredGame],
    *,
    llm_client: anthropic.Anthropic,
    season_stats: dict[str, BatterSeasonStats] | None = None,
) -> list[Storyline]:
    storylines: list[Storyline] = []
    for scored in top_games:
        prose = write_storyline(scored, client=llm_client, season_stats=season_stats)
        storylines.append(Storyline(scored=scored, prose=prose))
    return storylines


def _generate_preview_prose(
    most_hyped: ScoredTonightGame | None,
    *,
    llm_client: anthropic.Anthropic,
) -> TonightPreview | None:
    if most_hyped is None:
        return None
    prose = write_preview(most_hyped, client=llm_client)
    return TonightPreview(scored=most_hyped, prose=prose)


def _write_dashboard(
    digest: Digest,
    out_dir: Path,
) -> None:
    day_dir = out_dir / "digests" / digest.digest_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    day_html = render_dashboard_day(digest)
    (day_dir / "index.html").write_text(day_html, encoding="utf-8")
    logger.info("Wrote dashboard page to %s", day_dir / "index.html")

    entries = _build_index_entries(out_dir)
    index_html = render_dashboard_index(entries)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("Wrote dashboard index to %s", out_dir / "index.html")


def _build_index_entries(out_dir: Path) -> list[IndexEntry]:
    digests_dir = out_dir / "digests"
    if not digests_dir.exists():
        return []

    entries: list[IndexEntry] = []
    for day_dir in sorted(digests_dir.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        try:
            d = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        entries.append(IndexEntry(
            date=d,
            headline=f"MLB Digest — {d.strftime('%B %-d, %Y')}",
            url=f"digests/{d.isoformat()}/",
        ))
    return entries


def _send_email(
    digest: Digest,
    *,
    config: Config,
) -> None:
    html = render_email_html(digest)
    text = render_email_text(digest)

    resend.api_key = config.resend_api_key

    resend.Emails.send({
        "from": config.digest_from_email,
        "to": [config.digest_to_email],
        "subject": f"MLB Digest — {digest.digest_date.strftime('%B %-d, %Y')}",
        "html": html,
        "text": text,
    })
    logger.info("Email sent to %s", config.digest_to_email)


def run(
    target_date: date,
    *,
    dry_run: bool = False,
    out_dir: str = "./public",
    config: Config | None = None,
) -> int:
    """Run the full digest pipeline for *target_date*.

    Returns 0 on success or clean early-exit, 1 on fatal error.
    """
    out_path = Path(out_dir)

    if not _is_active_season(target_date):
        logger.info("Season pause: %s is outside the active season window.", target_date)
        return 0

    day_index = out_path / "digests" / target_date.isoformat() / "index.html"
    if day_index.exists():
        logger.warning(
            "Idempotency guard: %s already exists. Skipping to avoid duplicate send.",
            day_index,
        )
        return 0

    if config is None:
        config = Config.load(require_secrets=not dry_run)

    mlb_client = make_client()
    try:
        return _run_pipeline(target_date, dry_run=dry_run, out_path=out_path, config=config, mlb_client=mlb_client)
    finally:
        mlb_client.close()


def _run_pipeline(
    target_date: date,
    *,
    dry_run: bool,
    out_path: Path,
    config: Config,
    mlb_client: httpx.Client,
) -> int:
    # --- Fetch yesterday's finals ---
    try:
        finals = fetch_finals(target_date, client=mlb_client)
    except MlbApiError as exc:
        logger.error("Failed to fetch schedule for %s: %s", target_date, exc)
        return 1

    # --- Fetch tonight's games (today = target_date + 1 day, i.e. "today") ---
    tonight_date = target_date + timedelta(days=1)
    try:
        tonight_games = fetch_tonight(tonight_date, client=mlb_client)
    except MlbApiError as exc:
        logger.warning("Failed to fetch tonight's schedule: %s", exc)
        tonight_games = []

    # --- Off-day branch ---
    if not finals:
        logger.info("No completed games on %s — off-day branch.", target_date)
        digest = Digest(
            digest_date=target_date,
            is_off_day=True,
            tonight_games=tonight_games,
        )
        _write_dashboard(digest, out_path)
        if not dry_run:
            _send_email(digest, config=config)
        else:
            _print_dry_run(digest)
        return 0

    # --- Fetch game feeds and score ---
    feeds = _fetch_game_feeds(finals, client=mlb_client)
    scored_games = score_games(finals, feeds)
    top_storylines = apply_variety_rule(scored_games)

    # --- Score tonight's hype ---
    stars = load_star_ids()
    contexts = _build_hype_contexts(tonight_games)
    most_hyped = select_most_hyped(tonight_games, contexts, stars)

    # --- Fetch season stats for featured batters ---
    batter_ids = _collect_batter_ids(top_storylines)
    if batter_ids:
        season_stats = fetch_batter_season_stats(
            batter_ids, season=target_date.year, client=mlb_client,
        )
    else:
        season_stats = {}

    # --- Generate LLM prose ---
    if dry_run and not config.anthropic_api_key:
        llm_client = None
    else:
        llm_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    if llm_client is not None:
        storylines = _generate_storyline_prose(
            top_storylines, llm_client=llm_client, season_stats=season_stats,
        )
        preview = _generate_preview_prose(most_hyped, llm_client=llm_client)
    else:
        from mlbreview.llm import _storyline_fallback, _preview_fallback
        storylines = [
            Storyline(scored=sg, prose=_storyline_fallback(sg))
            for sg in top_storylines
        ]
        preview = None
        if most_hyped is not None:
            preview = TonightPreview(scored=most_hyped, prose=_preview_fallback(most_hyped))

    # --- Fetch transactions ---
    try:
        transactions = fetch_transactions(target_date, target_date, client=mlb_client)
    except MlbApiError as exc:
        logger.warning("Failed to fetch transactions: %s", exc)
        transactions = []

    # --- Build digest and render ---
    digest = Digest(
        digest_date=target_date,
        is_off_day=False,
        games=finals,
        storylines=storylines,
        tonight=preview,
        transactions=transactions,
        tonight_games=tonight_games,
    )

    _write_dashboard(digest, out_path)

    if not dry_run:
        _send_email(digest, config=config)
    else:
        _print_dry_run(digest)

    logger.info(
        "Pipeline complete for %s: %d games, %d storylines, tonight=%s",
        target_date, len(finals), len(storylines),
        "yes" if preview else "no",
    )
    return 0


def _print_dry_run(digest: Digest) -> None:
    html = render_email_html(digest)
    text = render_email_text(digest)
    print("=" * 72)
    print("DRY RUN — Email HTML preview")
    print("=" * 72)
    print(html[:2000])
    if len(html) > 2000:
        print(f"... ({len(html)} bytes total, truncated)")
    print()
    print("=" * 72)
    print("DRY RUN — Email text preview")
    print("=" * 72)
    print(text)
