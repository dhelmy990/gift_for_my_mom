"""Pure spelling-based suggestions for unresolved company aliases."""

from __future__ import annotations

from dataclasses import dataclass
import math

from rapidfuzz.fuzz import ratio

from .cleaning import normalize_lookup_key
from .repository import AliasMapping


FUZZY_THRESHOLD = 90.0


@dataclass(frozen=True)
class AliasSuggestion:
    saved_alias: str
    canonical_name: str
    score: float


def suggest_alias(
    cleaned_name: str,
    aliases: list[AliasMapping],
    threshold: float = FUZZY_THRESHOLD,
) -> AliasSuggestion | None:
    """Return the unique best spelling suggestion above ``threshold``."""
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0.0 <= threshold <= 100.0
    ):
        raise ValueError("threshold must be between 0 and 100")

    query = normalize_lookup_key(cleaned_name)
    if any(query == item.alias_key for item in aliases):
        return None

    scored = [(float(ratio(query, item.alias_key)), item) for item in aliases]
    eligible = [(score, item) for score, item in scored if score >= threshold]
    if not eligible:
        return None

    best_score = max(score for score, _ in eligible)
    winners = [item for score, item in eligible if score == best_score]
    if len(winners) != 1:
        return None

    winner = winners[0]
    return AliasSuggestion(winner.cleaned_alias, winner.canonical_name, best_score)
