"""Variety rule — ensures the top 3 storylines aren't all the same type.

The variety rule prevents the digest from surfacing three multi-HR games
(or three of any single category) when the scores are close. It walks
the top candidates in drama-score order and skips duplicates whose scores
are within a configurable threshold of an already-selected candidate from
the same category.

Algorithm
---------
1. Take the top 5 candidates by drama score.
2. Walk in order. For each candidate:
   a. If its category was already selected AND its score is within
      VARIETY_THRESHOLD of the previous pick with that category → skip.
   b. Otherwise → accept.
3. Stop when k candidates are accepted (default k=3).
4. If fewer than k remain after filtering, accept the highest-scoring
   skipped candidates to fill the gap (never return fewer than k unless
   the input itself is shorter).

All thresholds are in `config.py`. See `docs/formulas.md` for the plain-
language explainer.

Tunable knobs (in config.py):
    VARIETY_THRESHOLD, MAX_STORYLINES
"""

from __future__ import annotations

from mlbreview.config import MAX_STORYLINES, VARIETY_THRESHOLD
from mlbreview.scoring.drama import ScoredGame


def apply_variety_rule(
    candidates: list[ScoredGame],
    *,
    k: int = MAX_STORYLINES,
    threshold: float = VARIETY_THRESHOLD,
) -> list[ScoredGame]:
    """Select up to *k* storylines from *candidates* with category diversity.

    Parameters
    ----------
    candidates : list[ScoredGame]
        Drama-scored games, already sorted descending by score.
    k : int
        Number of storylines to select (default from config).
    threshold : float
        When two candidates share a category and the lower one's score is
        within this fraction of the higher one's score, the lower one is
        demoted. Set to 0 to disable (always pick top-k regardless of
        category).

    Returns
    -------
    list[ScoredGame]
        Selected storylines in drama-score order. Length is
        ``min(k, len(candidates))``.
    """
    if len(candidates) <= k:
        return list(candidates)

    pool = candidates[:5]
    selected: list[ScoredGame] = []
    skipped: list[ScoredGame] = []
    category_scores: dict[str, float] = {}

    for candidate in pool:
        if len(selected) >= k:
            break

        prior_score = category_scores.get(candidate.category)
        if prior_score is not None and threshold > 0:
            diff = prior_score - candidate.score
            if diff <= prior_score * threshold:
                skipped.append(candidate)
                continue

        selected.append(candidate)
        category_scores[candidate.category] = candidate.score

    while len(selected) < k and skipped:
        selected.append(skipped.pop(0))

    return selected
