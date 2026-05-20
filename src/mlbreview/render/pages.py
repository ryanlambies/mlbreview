"""Jinja2 renderers for the email and dashboard.

All templates receive a `Digest` dataclass (defined here) that carries the
full day's content: scores, storylines, tonight's preview, and news brief.
Render functions take that dataclass plus render-time context (date, dashboard
URL) and return rendered strings.

The Jinja2 environment is configured with autoescape for HTML safety and
trim_blocks / lstrip_blocks for readable templates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import jinja2

from mlbreview.config import OFF_DAY_BODY, OFF_DAY_HEADLINE
from mlbreview.data.schedule import Game, InningLine, TonightGame
from mlbreview.data.transactions import Transaction
from mlbreview.scoring.drama import ScoredGame
from mlbreview.scoring.hype import ScoredTonightGame
from mlbreview.scoring.leaderboards import Leaderboards

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"

GMAIL_CLIP_THRESHOLD = 102_400  # bytes
GMAIL_CLIP_WARNING = 81_920  # 80 KB — warn before we hit the cliff

DASHBOARD_BASE_URL = "https://ryanlambies.github.io/mlbreview"


@dataclass(frozen=True)
class Storyline:
    """A scored game paired with its LLM-generated prose."""

    scored: ScoredGame
    prose: str


@dataclass(frozen=True)
class TonightPreview:
    """The most-hyped tonight-game paired with its LLM-generated preview."""

    scored: ScoredTonightGame
    prose: str


@dataclass(frozen=True)
class Digest:
    """The full content payload for one day's digest.

    This is the single shape all templates (email + dashboard) consume.
    The pipeline (U6) constructs this; renderers only read it.
    """

    digest_date: date
    is_off_day: bool = False

    # Section 1: scores recap (all completed games)
    games: list[Game] = field(default_factory=list)

    # Section 2: top 3 storylines (drama-ranked, variety-filtered)
    storylines: list[Storyline] = field(default_factory=list)

    # Section 3: tonight's most-hyped game preview
    tonight: TonightPreview | None = None

    # Section 4: off-field news brief
    transactions: list[Transaction] = field(default_factory=list)

    # Tonight's full schedule (used on off-days and as supplemental)
    tonight_games: list[TonightGame] = field(default_factory=list)

    # V2: player leaderboards (None when V2 data is unavailable or failed)
    leaderboards: Leaderboards | None = None

    @property
    def off_day_headline(self) -> str:
        return OFF_DAY_HEADLINE

    @property
    def off_day_body(self) -> str:
        return OFF_DAY_BODY

    @property
    def dashboard_url(self) -> str:
        return f"{DASHBOARD_BASE_URL}/digests/{self.digest_date.isoformat()}/"


def _build_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["format_date"] = _format_date
    env.filters["ordinal"] = _ordinal
    env.filters["category_label"] = _category_label
    env.filters["format_avg"] = _format_avg
    env.filters["format_era"] = _format_era
    env.filters["format_ip"] = _format_ip
    env.filters["luck_badge"] = _luck_badge
    env.filters["luck_class"] = _luck_class
    return env


def _format_date(d: date) -> str:
    return d.strftime("%B %-d, %Y")


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _category_label(cat: str) -> str:
    labels = {
        "walkoff": "Walk-Off",
        "comeback": "Comeback",
        "pitchers_duel": "Pitchers' Duel",
        "feat": "Feat",
        "extra_innings": "Extra Innings",
        "default": "",
    }
    return labels.get(cat, "")


def _format_avg(value: float) -> str:
    """Format a batting average to .XXX (3 decimal places, no leading zero)."""
    return f"{value:.3f}".lstrip("0") or ".000"


def _format_era(value: float) -> str:
    """Format ERA to 2 decimal places."""
    return f"{value:.2f}"


def _format_ip(value: float) -> str:
    """Format innings pitched in baseball notation (e.g., 6.333... → 6.1)."""
    full = int(value)
    partial_outs = round((value - full) * 3)
    return f"{full}.{partial_outs}"


def _luck_badge(status: str) -> str:
    """Return a short badge label for a luck status value."""
    labels = {
        "confirmed": "Confirmed",
        "lucky": "Lucky",
        "unlucky": "Unlucky",
        "unconfirmed": "",
    }
    return labels.get(status, "")


def _luck_class(status: str) -> str:
    """Return a CSS class suffix for a luck status value."""
    return status if status in ("confirmed", "lucky", "unlucky") else ""


_env = _build_env()


def render_email_html(digest: Digest) -> str:
    """Render the HTML email body.

    Logs a warning if the rendered output exceeds 80KB (Gmail clips at 102KB).
    """
    template = _env.get_template("email.html.j2")
    html = template.render(digest=digest)

    size = len(html.encode("utf-8"))
    if size > GMAIL_CLIP_THRESHOLD:
        logger.warning(
            "Email HTML is %d bytes — exceeds Gmail's 102KB clip threshold", size
        )
    elif size > GMAIL_CLIP_WARNING:
        logger.warning(
            "Email HTML is %d bytes — approaching Gmail's 102KB clip threshold", size
        )

    return html


def render_email_text(digest: Digest) -> str:
    """Render the plaintext email fallback."""
    template = _env.get_template("email.txt.j2")
    return template.render(digest=digest)


def render_dashboard_day(digest: Digest) -> str:
    """Render the per-day dashboard HTML page."""
    template = _env.get_template("dashboard_day.html.j2")
    return template.render(digest=digest)


@dataclass(frozen=True)
class IndexEntry:
    """One row in the dashboard archive index."""

    date: date
    headline: str
    url: str


def render_dashboard_index(entries: list[IndexEntry]) -> str:
    """Render the dashboard index/archive page.

    Parameters
    ----------
    entries : list[IndexEntry]
        Recent digest entries, newest first. Entries beyond 30 are collapsed.
    """
    template = _env.get_template("dashboard_index.html.j2")
    return template.render(entries=entries)
