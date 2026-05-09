"""Central configuration for mlbreview.

Every tunable knob in the product lives here:
- API credentials and recipient addresses (loaded from env)
- Drama formula weights and normalization constants
- Hype formula weights and sub-signal scoring
- Storyline variety rule threshold and storyline count
- Off-day phrasing

Calibration sessions should only need to touch this file. The scoring modules
read these constants directly; they do not redefine them.

See `docs/formulas.md` for the plain-language explainer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Drama formula (storyline ranking)
# ---------------------------------------------------------------------------
# drama = DRAMA_W_MAX_WPA   * norm_max_wpa
#       + DRAMA_W_LATE_WPA  * norm_late_inning_peak_wpa
#       + DRAMA_W_MARGIN    * inverse_margin
#
# Note on the middle term: the V1 plan called this `late_leverage` (Leverage
# Index of late-game plays). The MLB Stats API does not expose Leverage Index
# on `/feed/live` or `/winProbability` — only on Statcast / Baseball Savant
# behind the `pybaseball` dep, which is V2-only. We substitute "max |WPA| in
# innings >= DRAMA_LATE_INNING_THRESHOLD," which preserves the formula's
# intent ("credit late-game high-stakes plays") with data we have. See
# `docs/formulas.md` for the full rationale (lands in U3).
DRAMA_W_MAX_WPA: float = 0.5
DRAMA_W_LATE_WPA: float = 0.3
DRAMA_W_MARGIN: float = 0.2

# WPA from the MLB Stats API is in percentage points (0–100 scale, signed).
# A practical single-play ceiling is ~50 points (a play that flips the home
# win probability from 50% to 100%). Divide raw |WPA| by this to land in
# roughly [0, 1] for the drama formula.
DRAMA_MAX_WPA_CEILING: float = 50.0

# Innings >= this count toward the late-inning peak-WPA component.
DRAMA_LATE_INNING_THRESHOLD: int = 7


# ---------------------------------------------------------------------------
# Hype formula (tonight's most-hyped game)
# ---------------------------------------------------------------------------
# hype = HYPE_W_PITCHING * pitching_quality
#      + HYPE_W_STAKES   * stakes
#      + HYPE_W_STARS    * star_density
#      + HYPE_W_NATIONAL * national_broadcast_flag
HYPE_W_PITCHING: float = 0.35
HYPE_W_STAKES: float = 0.25
HYPE_W_STARS: float = 0.20
HYPE_W_NATIONAL: float = 0.20

# Stakes sub-signal additive components (capped at 1.0).
HYPE_STAKES_DIVISION_RIVALS: float = 0.4
HYPE_STAKES_BOTH_ABOVE_500: float = 0.3
HYPE_STAKES_PLAYOFF_RACE_DELTA: float = 0.3

# Star density: count of stars across both lineups, divided by this denominator
# and clamped to [0, 1].
HYPE_STAR_DENSITY_DIVISOR: float = 4.0


# ---------------------------------------------------------------------------
# Variety rule (storyline diversity)
# ---------------------------------------------------------------------------
# When two top-5 candidates share a category and their drama scores are within
# this fraction of each other, demote the lower one and try the next-highest of
# a different category.
VARIETY_THRESHOLD: float = 0.10

# Final number of storylines to surface in the digest.
MAX_STORYLINES: int = 3


# ---------------------------------------------------------------------------
# LLM (storyline + preview prose generation)
# ---------------------------------------------------------------------------
LLM_MODEL: str = "claude-haiku-4-5"
LLM_MAX_TOKENS: int = 200
LLM_RETRY_DELAY: float = 2.0


# ---------------------------------------------------------------------------
# Pipeline behavior
# ---------------------------------------------------------------------------
# Acceptable hour window (in America/New_York) for the digest to send.
# GitHub Actions cron can delay jobs by 30-120+ minutes, so we accept a
# range instead of a single hour. The two cron slots (09:30 and 10:30 UTC)
# target 5:30am ET across EST/EDT; the window covers typical delays.
SEND_HOUR_ET_MIN: int = 5
SEND_HOUR_ET_MAX: int = 7

# Phrasing surfaced when there were no MLB games the prior day.
OFF_DAY_HEADLINE: str = "No games last night"
OFF_DAY_BODY: str = "Quiet night across the league. Tonight's schedule is below."


# ---------------------------------------------------------------------------
# Runtime config (env-loaded)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """Runtime configuration loaded from environment variables.

    `dry_run` callers may pass `require_secrets=False` to tolerate missing
    Anthropic / Resend keys (the dry-run path skips the LLM and email send).
    """

    anthropic_api_key: str | None
    resend_api_key: str | None
    digest_to_email: str | None
    digest_from_email: str

    @classmethod
    def load(cls, *, require_secrets: bool = True) -> "Config":
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        resend_key = os.environ.get("RESEND_API_KEY")
        to_email = os.environ.get("DIGEST_TO_EMAIL")
        from_email = os.environ.get(
            "DIGEST_FROM_EMAIL", "MLB Digest <onboarding@resend.dev>"
        )

        if require_secrets:
            missing: list[str] = []
            if not anthropic_key:
                missing.append("ANTHROPIC_API_KEY")
            if not resend_key:
                missing.append("RESEND_API_KEY")
            if not to_email:
                missing.append("DIGEST_TO_EMAIL")
            if missing:
                raise EnvironmentError(
                    "Missing required environment variables: "
                    + ", ".join(missing)
                    + ". Set them in .env (local) or GitHub Actions secrets "
                    "(production), or pass --dry-run to skip the LLM and email."
                )

        return cls(
            anthropic_api_key=anthropic_key,
            resend_api_key=resend_key,
            digest_to_email=to_email,
            digest_from_email=from_email,
        )
