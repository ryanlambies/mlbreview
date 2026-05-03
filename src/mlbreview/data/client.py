"""Shared HTTP client and error type for MLB Stats API calls.

Centralizes timeout, transport-level retries, user-agent, and the application
error wrapper so every fetcher behaves consistently. Application-level retry on
5xx responses is one extra attempt; persistent failure raises `MlbApiError`,
which the orchestrator surfaces as a hard failure (no half-good digests).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL_V1 = "https://statsapi.mlb.com/api/v1"
BASE_URL_V1_1 = "https://statsapi.mlb.com/api/v1.1"

DEFAULT_TIMEOUT_SECONDS: float = 30.0
TRANSPORT_RETRIES: int = 2
USER_AGENT: str = "mlbreview/0.1 (+https://github.com/ryanlambies/mlbreview)"


class MlbApiError(Exception):
    """Raised on persistent MLB Stats API failure (network, HTTP, or JSON)."""


def make_client() -> httpx.Client:
    """Build a configured httpx.Client. Caller is responsible for closing it."""
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        transport=httpx.HTTPTransport(retries=TRANSPORT_RETRIES),
        headers={"User-Agent": USER_AGENT},
    )


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    """GET `url` and return its JSON body.

    Returns whatever JSON the endpoint emits (dict for most endpoints, list
    for `/winProbability`). Retries once on 5xx. Raises `MlbApiError` on
    network failure, non-2xx status (after retry), or invalid JSON.
    """

    def _request() -> httpx.Response:
        return client.get(url, params=params)

    try:
        resp = _request()
        if resp.status_code >= 500:
            logger.warning("MLB API %s on %s; retrying once", resp.status_code, url)
            resp = _request()
    except httpx.HTTPError as exc:
        raise MlbApiError(f"Network error fetching {url}: {exc}") from exc

    if resp.status_code != 200:
        raise MlbApiError(
            f"HTTP {resp.status_code} fetching {url}: {resp.text[:200]}"
        )

    try:
        return resp.json()
    except ValueError as exc:
        raise MlbApiError(f"Invalid JSON from {url}: {exc}") from exc
