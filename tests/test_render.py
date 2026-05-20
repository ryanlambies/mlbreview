"""Tests for the Jinja2 renderers — email + dashboard output.

Covers: all four section headers in email, dashboard link (AE5),
off-day rendering (AE3), empty news brief, Gmail size limit, and
dashboard index with varying entry counts.
"""

from __future__ import annotations

from datetime import date

from mlbreview.data.game import GameFeed, Play
from mlbreview.data.schedule import (
    Broadcast,
    Decisions,
    Game,
    InningLine,
    ProbablePitcher,
    TonightGame,
)
from mlbreview.data.transactions import Transaction, TransactionCategory
from mlbreview.render.pages import (
    GMAIL_CLIP_THRESHOLD,
    Digest,
    IndexEntry,
    Storyline,
    TonightPreview,
    render_dashboard_day,
    render_dashboard_index,
    render_email_html,
    render_email_text,
)
from mlbreview.scoring.drama import ScoredGame
from mlbreview.scoring.hype import ScoredTonightGame
from mlbreview.scoring.leaderboards import (
    LeaderboardHitter,
    LeaderboardPitcher,
    Leaderboards,
    LuckStatus,
)


def _game(
    *,
    gamePk: int = 1,
    away_abbr: str = "NYY",
    home_abbr: str = "BOS",
    away_score: int = 5,
    home_score: int = 3,
    innings: int = 9,
) -> Game:
    return Game(
        gamePk=gamePk,
        game_type="R",
        status="Final",
        away_team_name="Away",
        away_team_abbr=away_abbr,
        away_score=away_score,
        home_team_name="Home",
        home_team_abbr=home_abbr,
        home_score=home_score,
        decisions=Decisions(winner="W", loser="L", save=None),
        line_score=tuple(
            InningLine(inning=i + 1, away_runs=0, home_runs=0)
            for i in range(innings)
        ),
    )


def _feed(gamePk: int = 1) -> GameFeed:
    return GameFeed(
        gamePk=gamePk,
        plays=(),
        max_wpa_swing=0.0,
        late_inning_max_wpa=0.0,
        biggest_play=None,
    )


def _tonight_game(
    *,
    gamePk: int = 10,
    away_abbr: str = "LAD",
    home_abbr: str = "SF",
    national: bool = True,
) -> TonightGame:
    broadcasts = ()
    if national:
        broadcasts = (Broadcast(name="ESPN", type="TV", is_national=True),)
    return TonightGame(
        gamePk=gamePk,
        game_type="R",
        game_date_utc="2025-08-16T23:10:00Z",
        away_team_name="Los Angeles Dodgers",
        away_team_abbr=away_abbr,
        away_record="65-45",
        home_team_name="San Francisco Giants",
        home_team_abbr=home_abbr,
        home_record="58-52",
        away_probable_pitcher=ProbablePitcher(player_id=1, full_name="Walker Buehler"),
        home_probable_pitcher=ProbablePitcher(player_id=2, full_name="Logan Webb"),
        broadcasts=broadcasts,
    )


def _full_digest() -> Digest:
    """A typical game-day digest with all sections populated."""
    games = [
        _game(gamePk=1, away_abbr="NYY", home_abbr="BOS", away_score=5, home_score=3),
        _game(gamePk=2, away_abbr="LAD", home_abbr="SF", away_score=2, home_score=4),
        _game(gamePk=3, away_abbr="CHC", home_abbr="STL", away_score=7, home_score=8, innings=11),
    ]

    storylines = [
        Storyline(
            scored=ScoredGame(game=games[2], feed=_feed(3), score=0.88, category="walkoff"),
            prose="The Cardinals walked off in the 11th inning on a two-run homer.",
        ),
        Storyline(
            scored=ScoredGame(game=games[0], feed=_feed(1), score=0.75, category="default"),
            prose="The Yankees pounded out 12 hits to cruise past the Red Sox.",
        ),
        Storyline(
            scored=ScoredGame(game=games[1], feed=_feed(2), score=0.70, category="pitchers_duel"),
            prose="The Giants squeezed past the Dodgers behind Logan Webb's 7 shutout innings.",
        ),
    ]

    tonight = TonightPreview(
        scored=ScoredTonightGame(game=_tonight_game(), score=0.85),
        prose="Buehler vs Webb in a crucial NL West showdown with playoff implications.",
    )

    transactions = [
        Transaction(
            transaction_id=1,
            date="2025-08-15",
            category=TransactionCategory.TRADE,
            player_name="Player X",
            team_name="Team A",
            description="Team A acquired Player X from Team B for two prospects.",
        ),
        Transaction(
            transaction_id=2,
            date="2025-08-15",
            category=TransactionCategory.INJURED_LIST,
            player_name="Player Y",
            team_name="Team C",
            description="Team C placed Player Y on the 10-day IL with a hamstring strain.",
        ),
    ]

    return Digest(
        digest_date=date(2025, 8, 16),
        games=games,
        storylines=storylines,
        tonight=tonight,
        transactions=transactions,
    )


def _sample_leaderboards() -> Leaderboards:
    """Build a minimal Leaderboards for render tests."""
    hot_h = LeaderboardHitter(
        player_id=1, full_name="Aaron Judge", team_abbr="NYY",
        games=7, plate_appearances=30, avg=0.412, obp=0.480, slg=0.824,
        home_runs=4, rbi=10, stolen_bases=1,
        composite_score=0.85, luck_status=LuckStatus.CONFIRMED,
        xwoba=0.420, barrel_pct=18.5,
    )
    cold_h = LeaderboardHitter(
        player_id=2, full_name="Cody Bellinger", team_abbr="CHC",
        games=6, plate_appearances=24, avg=0.125, obp=0.200, slg=0.167,
        home_runs=0, rbi=1, stolen_bases=0,
        composite_score=0.12, luck_status=LuckStatus.UNLUCKY,
        xwoba=0.340, barrel_pct=12.0,
    )
    hot_p = LeaderboardPitcher(
        player_id=3, full_name="Zack Wheeler", team_abbr="PHI",
        role="starter", era=1.50, innings_pitched=12.0, strikeouts=15,
        composite_score=0.90, luck_status=LuckStatus.CONFIRMED,
        whip=0.83, k_per_9=11.25, starts=2, fip=2.10, xera=2.30,
    )
    cold_p = LeaderboardPitcher(
        player_id=4, full_name="Marcus Stroman", team_abbr="NYM",
        role="starter", era=7.20, innings_pitched=10.0, strikeouts=5,
        composite_score=0.15, luck_status=LuckStatus.CONFIRMED,
        whip=1.90, k_per_9=4.50, starts=2, fip=5.50, xera=5.80,
    )
    return Leaderboards(
        hot_hitters=[hot_h], cold_hitters=[cold_h],
        hot_pitchers=[hot_p], cold_pitchers=[cold_p],
        breakout_hitters=[], breakout_pitchers=[],
        snapshots_7d=7, snapshots_15d=12,
    )


def _full_digest_with_leaderboards() -> Digest:
    """A game-day digest with V2 leaderboards attached."""
    base = _full_digest()
    return Digest(
        digest_date=base.digest_date,
        games=base.games,
        storylines=base.storylines,
        tonight=base.tonight,
        transactions=base.transactions,
        leaderboards=_sample_leaderboards(),
    )


def _off_day_digest() -> Digest:
    return Digest(
        digest_date=date(2025, 8, 16),
        is_off_day=True,
        tonight_games=[_tonight_game()],
    )


# ---------------------------------------------------------------------------
# Email HTML
# ---------------------------------------------------------------------------


class TestRenderEmailHtml:
    def test_contains_all_four_sections(self) -> None:
        """AE5: all section headers present."""
        html = render_email_html(_full_digest())
        assert "Scores" in html
        assert "Storylines" in html
        assert "Tonight" in html
        assert "Off-field" in html

    def test_contains_dashboard_link_above_fold(self) -> None:
        """AE5: dashboard CTA is in the email."""
        html = render_email_html(_full_digest())
        assert "Open in dashboard" in html
        assert "ryanlambies.github.io/mlbreview/digests/2025-08-16" in html

    def test_scores_show_team_abbreviations(self) -> None:
        html = render_email_html(_full_digest())
        assert "NYY" in html
        assert "BOS" in html

    def test_extra_innings_indicator(self) -> None:
        html = render_email_html(_full_digest())
        assert "(11)" in html

    def test_storyline_prose_rendered(self) -> None:
        html = render_email_html(_full_digest())
        assert "Cardinals walked off" in html

    def test_tonight_preview_rendered(self) -> None:
        html = render_email_html(_full_digest())
        assert "Buehler vs Webb" in html

    def test_transactions_rendered(self) -> None:
        html = render_email_html(_full_digest())
        assert "Player X" in html

    def test_off_day_email(self) -> None:
        """AE3: off-day email shows no-games message + tonight schedule."""
        html = render_email_html(_off_day_digest())
        assert "No games last night" in html
        assert "LAD" in html
        assert "Scores" not in html

    def test_empty_transactions_omits_section(self) -> None:
        digest = Digest(
            digest_date=date(2025, 8, 16),
            games=[_game()],
            storylines=[],
            transactions=[],
        )
        html = render_email_html(digest)
        assert "Off-field" not in html

    def test_size_under_gmail_clip(self) -> None:
        html = render_email_html(_full_digest())
        assert len(html.encode("utf-8")) < GMAIL_CLIP_THRESHOLD

    def test_leaderboard_teaser_in_email(self) -> None:
        html = render_email_html(_full_digest_with_leaderboards())
        assert "Hottest hitter" in html
        assert "Aaron Judge" in html
        assert ".412" in html
        assert "Hottest pitcher" in html
        assert "Zack Wheeler" in html

    def test_no_leaderboard_teaser_without_data(self) -> None:
        html = render_email_html(_full_digest())
        assert "Hottest hitter" not in html

    def test_leaderboard_teaser_links_to_dashboard(self) -> None:
        html = render_email_html(_full_digest_with_leaderboards())
        assert "full leaderboards on the dashboard" in html


# ---------------------------------------------------------------------------
# Email text
# ---------------------------------------------------------------------------


class TestRenderEmailText:
    def test_contains_all_sections(self) -> None:
        text = render_email_text(_full_digest())
        assert "SCORES" in text
        assert "STORYLINES" in text
        assert "TONIGHT" in text
        assert "OFF-FIELD" in text

    def test_contains_dashboard_url(self) -> None:
        text = render_email_text(_full_digest())
        assert "ryanlambies.github.io/mlbreview/digests/2025-08-16" in text

    def test_off_day_text(self) -> None:
        text = render_email_text(_off_day_digest())
        assert "No games last night" in text
        assert "SCORES" not in text

    def test_leaderboard_teaser_in_text(self) -> None:
        text = render_email_text(_full_digest_with_leaderboards())
        assert "Hottest hitter" in text
        assert "Aaron Judge" in text
        assert "Hottest pitcher" in text
        assert "Zack Wheeler" in text

    def test_no_leaderboard_teaser_in_text_without_data(self) -> None:
        text = render_email_text(_full_digest())
        assert "PLAYER LEADERBOARDS" not in text


# ---------------------------------------------------------------------------
# Dashboard day
# ---------------------------------------------------------------------------


class TestRenderDashboardDay:
    def test_contains_dashboard_link(self) -> None:
        html = render_dashboard_day(_full_digest())
        assert "All digests" in html

    def test_contains_all_sections(self) -> None:
        html = render_dashboard_day(_full_digest())
        assert "Scores" in html
        assert "Storylines" in html
        assert "Tonight" in html
        assert "Off-field" in html

    def test_off_day_dashboard(self) -> None:
        html = render_dashboard_day(_off_day_digest())
        assert "No games last night" in html

    def test_no_leaderboard_section_without_data(self) -> None:
        html = render_dashboard_day(_full_digest())
        assert "Player Leaderboards" not in html

    def test_leaderboard_section_renders(self) -> None:
        html = render_dashboard_day(_full_digest_with_leaderboards())
        assert "Player Leaderboards" in html
        assert "Aaron Judge" in html
        assert "Cody Bellinger" in html
        assert "Zack Wheeler" in html
        assert "Hot Hitters" in html
        assert "Cold Pitchers" in html

    def test_leaderboard_luck_badges(self) -> None:
        html = render_dashboard_day(_full_digest_with_leaderboards())
        assert "Confirmed" in html
        assert "Unlucky" in html

    def test_leaderboard_tabs_and_sort_js(self) -> None:
        html = render_dashboard_day(_full_digest_with_leaderboards())
        assert "lb-tab" in html
        assert "data-sortable" in html
        assert "<script>" in html


# ---------------------------------------------------------------------------
# Dashboard index
# ---------------------------------------------------------------------------


class TestRenderDashboardIndex:
    def test_renders_entries(self) -> None:
        entries = [
            IndexEntry(date=date(2025, 8, 16), headline="Walk-off win", url="digests/2025-08-16/"),
            IndexEntry(date=date(2025, 8, 15), headline="Pitchers' duel", url="digests/2025-08-15/"),
        ]
        html = render_dashboard_index(entries)
        assert "Walk-off win" in html
        assert "2025-08-16" in html

    def test_30_entries_renders_correctly(self) -> None:
        entries = [
            IndexEntry(
                date=date(2025, 8, 16 - i) if 16 - i > 0 else date(2025, 7, 31 - (i - 16)),
                headline=f"Headline {i}" + " extra" * (i % 5),
                url=f"digests/2025-08-{16 - i:02d}/",
            )
            for i in range(30)
        ]
        html = render_dashboard_index(entries)
        assert "Headline 0" in html
        assert "Headline 29" in html

    def test_more_than_30_shows_collapsed(self) -> None:
        entries = [
            IndexEntry(date=date(2025, 1, 1), headline=f"H{i}", url=f"d/{i}/")
            for i in range(35)
        ]
        html = render_dashboard_index(entries)
        assert "+ 5 older digests" in html

    def test_empty_entries(self) -> None:
        html = render_dashboard_index([])
        assert "No digests yet" in html
