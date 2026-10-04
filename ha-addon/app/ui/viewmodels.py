"""Turns engine results into what the templates display. Pure functions, no web framework."""
from app.services import comparables as comps

# (badge css class, label, one-line meaning) for each confidence level.
CONFIDENCE_STYLES = {
    "low": ("text-bg-warning", "Low confidence", "Fewer than 5 comparables were used. Treat this as a rough estimate."),
    "medium": ("text-bg-info", "Medium confidence", "5 to 9 comparables were used."),
    "high": ("text-bg-success", "High confidence", "10 or more comparables were used."),
}

MILES = comps.DEFAULT_MAX_DISTANCE_MILES


def money(value) -> str:
    """2512 -> '$2,512'. Whole dollars only, as the engine reports."""
    return f"${round(value):,}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return singular if count == 1 else (plural_form or singular + "s")


def confidence_style(level: str) -> dict:
    css, label, meaning = CONFIDENCE_STYLES[level]
    return {"level": level, "css": css, "label": label, "meaning": meaning}


def funnel_rows(funnel: dict) -> list[dict]:
    """The five funnel stages in order, each with its count, its width as a percentage of what
    was fetched, and how many were lost at that stage."""
    stages = [
        ("Fetched from the data source", funnel["comparables_fetched"]),
        (f"Within {MILES:g} {plural(int(MILES), 'mile')} of the property", funnel["comparables_after_distance_filter"]),
        ("Similar bedrooms, bathrooms and size", funnel["comparables_after_attribute_filter"]),
        ("After removing unusual rents", funnel["comparables_after_outlier_filter"]),
        ("Used for pricing", funnel["comparables_used"]),
    ]
    fetched = funnel["comparables_fetched"]
    rows, previous = [], None
    for label, count in stages:
        rows.append(
            {
                "label": label,
                "count": count,
                "percent": round(100 * count / fetched) if fetched else 0,
                "lost": 0 if previous is None else previous - count,
            }
        )
        previous = count
    return rows


def explain_insufficient(funnel: dict, minimum: int) -> str:
    """One plain sentence about the first stage that left too few comparables."""
    fetched = funnel["comparables_fetched"]
    near = funnel["comparables_after_distance_filter"]
    similar = funnel["comparables_after_attribute_filter"]
    left = funnel["comparables_after_outlier_filter"]
    if fetched < minimum:
        if fetched == 0:
            return "The data source returned no listings for this area."
        return f"The data source returned only {fetched} {plural(fetched, 'listing')} for this area."
    if near < minimum:
        return (
            f"Only {near} of {fetched} listings are within {MILES:g} {plural(int(MILES), 'mile')} of the property. "
            "If the address may have been placed in the wrong spot, enter exact coordinates."
        )
    if similar < minimum:
        return (
            f"Only {similar} nearby {plural(similar, 'listing')} had similar features "
            f"(within {comps.DEFAULT_BEDROOM_TOLERANCE} bedroom, {comps.DEFAULT_BATHROOM_TOLERANCE} bathroom "
            f"and {comps.DEFAULT_SQFT_TOLERANCE:.0%} of the square footage)."
        )
    return f"Removing unusual rents left only {left} {plural(left, 'comparable')}."
