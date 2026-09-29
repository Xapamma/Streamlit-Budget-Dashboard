"""Case-insensitive fuzzy matching for local search fields."""

from rapidfuzz import fuzz


def fuzzy_match_indices(query: str, values: list[str], *, threshold: float = 68) -> list[int]:
    """Return matching value indexes ordered from strongest to weakest match."""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return list(range(len(values)))

    scored = []
    for index, value in enumerate(values):
        normalized_value = value.casefold()
        score = max(
            fuzz.WRatio(normalized_query, normalized_value),
            fuzz.partial_ratio(normalized_query, normalized_value),
        )
        if score >= threshold:
            scored.append((score, index))
    return [index for _, index in sorted(scored, reverse=True)]