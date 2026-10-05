"""Diagnostics about how good a valuation's evidence is: the filter funnel and confidence."""
from dataclasses import asdict, dataclass
from typing import Literal

Confidence = Literal["low", "medium", "high"]

# Comparables used for pricing at or above these counts. Below MEDIUM is "low" (the engine's
# minimum, MIN_COMPARABLES_REQUIRED, normally keeps that to 3-4).
MEDIUM_CONFIDENCE_MIN = 5
HIGH_CONFIDENCE_MIN = 10


@dataclass(frozen=True)
class Funnel:
    """How many comparables survived each stage, from what the source returned to what was priced."""

    comparables_fetched: int
    comparables_after_distance_filter: int
    comparables_after_attribute_filter: int  # bedrooms, bathrooms and square footage
    comparables_after_outlier_filter: int
    comparables_used: int
    comparables_after_lookback_filter: int | None = None  # listings within the lookback window

    def as_dict(self) -> dict[str, int | None]:
        return asdict(self)


def confidence_for(comparables_used: int) -> Confidence:
    """low: fewer than 5 (3-4 in practice), medium: 5-9, high: 10 or more."""
    if comparables_used >= HIGH_CONFIDENCE_MIN:
        return "high"
    if comparables_used >= MEDIUM_CONFIDENCE_MIN:
        return "medium"
    return "low"


_LEVELS: tuple[Confidence, ...] = ("low", "medium", "high")


def lower_confidence(level: Confidence) -> Confidence:
    """One level down (high -> medium, medium -> low); low stays low."""
    return _LEVELS[max(_LEVELS.index(level) - 1, 0)]
