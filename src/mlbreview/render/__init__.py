"""Jinja2 renderers for email + dashboard.

Public API:
    Digest, Storyline, TonightPreview, IndexEntry — template input shapes
    render_email_html, render_email_text — email output
    render_dashboard_day, render_dashboard_index — static site output
"""

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

__all__ = [
    "Digest",
    "IndexEntry",
    "Storyline",
    "TonightPreview",
    "render_dashboard_day",
    "render_dashboard_index",
    "render_email_html",
    "render_email_text",
]
