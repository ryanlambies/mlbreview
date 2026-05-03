"""Transactions fetcher for the off-field news brief.

The brief is factual bullets only — no LLM prose, no rumor coverage. We
filter the MLB Stats API `/transactions` feed to the categories that matter
to a digest reader: trades, IL moves, and call-ups / debuts. Everything else
(minor-league shuffles, status changes that aren't IL-related, etc.) is
dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

import httpx

from mlbreview.data.client import BASE_URL_V1, get_json

logger = logging.getLogger(__name__)


class TransactionCategory(str, Enum):
    TRADE = "trade"
    INJURED_LIST = "injured_list"
    CALL_UP = "call_up"


# MLB Stats API typeCodes we surface directly.
# - "TR" trade
# - "CU" called up to majors (often a debut path)
# - "SE" signed extension (skip — not news-brief-worthy at single-line level)
TRADE_TYPE_CODES: frozenset[str] = frozenset({"TR"})
CALL_UP_TYPE_CODES: frozenset[str] = frozenset({"CU"})

# IL moves come through as `typeCode == "SC"` (status change) with descriptive
# text. We match on the description rather than the code.
INJURED_LIST_MARKERS: tuple[str, ...] = (
    "injured list",
    "10-day il",
    "15-day il",
    "60-day il",
    "paternity list",
    "bereavement list",
)


@dataclass(frozen=True)
class Transaction:
    transaction_id: int
    date: str  # ISO YYYY-MM-DD
    category: TransactionCategory
    player_name: str | None
    team_name: str | None
    description: str


def _classify(raw: dict[str, Any]) -> TransactionCategory | None:
    type_code = raw.get("typeCode") or ""
    if type_code in TRADE_TYPE_CODES:
        return TransactionCategory.TRADE
    if type_code in CALL_UP_TYPE_CODES:
        return TransactionCategory.CALL_UP
    desc = (raw.get("description") or "").lower()
    if any(marker in desc for marker in INJURED_LIST_MARKERS):
        return TransactionCategory.INJURED_LIST
    return None


def parse_transactions(payload: dict[str, Any]) -> list[Transaction]:
    """Parse a `/transactions` payload into our filtered news-brief list."""
    out: list[Transaction] = []
    for raw in payload.get("transactions", []):
        category = _classify(raw)
        if category is None:
            continue
        out.append(
            Transaction(
                transaction_id=int(raw.get("id") or 0),
                date=str(raw.get("date") or ""),
                category=category,
                player_name=(raw.get("person") or {}).get("fullName"),
                team_name=(raw.get("toTeam") or {}).get("name"),
                description=str(raw.get("description") or ""),
            )
        )
    return out


def fetch_transactions(
    start: date,
    end: date,
    *,
    client: httpx.Client,
) -> list[Transaction]:
    """Fetch and filter transactions in `[start, end]` (inclusive)."""
    payload = get_json(
        client,
        f"{BASE_URL_V1}/transactions",
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        },
    )
    if not isinstance(payload, dict):
        logger.warning("transactions payload was not a dict")
        return []
    return parse_transactions(payload)
