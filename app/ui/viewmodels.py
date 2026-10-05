"""Turns engine results into what the templates display. Pure functions, no web framework."""
from app.services import comparables as comps
from app.services import quality
from app.services.search_options import (
    LOOKBACK_OPTIONS_DAYS,
    PROPERTY_TYPES,
    RADIUS_OPTIONS_MILES,
    format_days,
    format_miles,
    property_type_label,
)

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


def funnel_rows(
    funnel: dict, radius_miles: float | None = None, lookback_days: int | None = None, sqft_used: bool = True
) -> list[dict]:
    """The funnel stages in order, each with its count, its width as a percentage of what was
    fetched, and how many were lost at that stage. The distance label shows the radius used (miles)."""
    radius = MILES if radius_miles is None else radius_miles
    stages = [("Fetched from the data source", funnel["comparables_fetched"])]
    if funnel.get("comparables_after_lookback_filter") is not None:
        label = (
            f"Listed within the last {format_days(lookback_days)}" if lookback_days else "Within the lookback window"
        )
        stages.append((label, funnel["comparables_after_lookback_filter"]))
    stages += [
        (f"Within {format_miles(radius)} of the property", funnel["comparables_after_distance_filter"]),
        (
            "Similar bedrooms, bathrooms and size" if sqft_used else "Similar bedrooms and bathrooms",
            funnel["comparables_after_attribute_filter"],
        ),
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


def explain_insufficient(funnel: dict, minimum: int, search: dict | None = None) -> str:
    """One plain sentence about the first stage that left too few comparables."""
    search = search or {}
    radius = search.get("radius_miles", MILES)
    nearest = search.get("nearest_listing_miles")
    sqft_used = search.get("sqft_used", True)
    fetched = funnel["comparables_fetched"]
    recent = funnel.get("comparables_after_lookback_filter")
    near = funnel["comparables_after_distance_filter"]
    similar = funnel["comparables_after_attribute_filter"]
    left = funnel["comparables_after_outlier_filter"]
    if fetched < minimum:
        if fetched == 0:
            return "The data source returned no listings for this area."
        return f"The data source returned only {fetched} {plural(fetched, 'listing')} for this area."
    if recent is not None and recent < minimum:
        days = search.get("lookback_days")
        window = f"the last {format_days(days)}" if days else "the lookback window"
        return (
            f"Only {recent} of {fetched} listings were listed within {window}. "
            "Try a longer lookback window."
        )
    base = fetched if recent is None else recent
    if near < minimum:
        text = f"Only {near} of {base} listings are within {format_miles(radius)} of the property."
        if nearest is not None:
            text += f" The nearest listing found is {nearest:g} miles away, so try a larger search radius."
        return text + " If the address may have been placed in the wrong spot, enter exact coordinates."
    if similar < minimum:
        size = f" and {comps.DEFAULT_SQFT_TOLERANCE:.0%} of the square footage" if sqft_used else ""
        return (
            f"Only {similar} nearby {plural(similar, 'listing')} had similar features "
            f"(within {comps.DEFAULT_BEDROOM_TOLERANCE} bedroom, {comps.DEFAULT_BATHROOM_TOLERANCE} bathroom{size})."
        )
    return f"Removing unusual rents left only {left} {plural(left, 'comparable')}."


# ---- search controls and the criteria summary -----------------------------------------------------------------

def radius_options() -> list[dict]:
    return [{"value": f"{m:g}", "label": format_miles(m), "summary": format_miles(m)} for m in RADIUS_OPTIONS_MILES]


def lookback_options() -> list[dict]:
    return [
        {"value": str(d), "label": format_days(d), "summary": f"Last {format_days(d)}"} for d in LOOKBACK_OPTIONS_DAYS
    ]


def property_type_options() -> list[dict]:
    return [{"value": key, "label": label, "summary": label} for key, (label, _) in PROPERTY_TYPES.items()]


def search_summary(
    *,
    address: str,
    radius_miles: float,
    lookback_days: int,
    property_type: str,
    sqft_given: bool,
    minimum_comparables: int,
    data_source: str,
    geocoder: str,
) -> list[dict]:
    """Every setting and rule that affects a valuation, as label/value rows, so no assumption is
    hidden. `data_source` and `geocoder` are the badge labels (MOCK, RENTCAST, CSV / mock, census).
    Rows with a `key` of address, radius, lookback, property_type or size change as the form is edited
    (the page script updates them); the others are fixed rules. All distances are miles."""
    sample = data_source == "MOCK"
    size_with = f"Within {comps.DEFAULT_SQFT_TOLERANCE:.0%} of the square feet"
    size_without = "Not used: square feet were left blank, so confidence is lowered one level"
    return [
        {"key": "address", "label": "Address", "value": address or "Not entered yet"},
        {
            "key": "radius", "label": "Search radius", "value": format_miles(radius_miles),
            "note": "The data source is asked for this radius, and listings farther away are dropped.",
        },
        {"key": "units", "label": "Distance units", "value": "Miles", "note": "Every distance on this page is in miles."},
        {
            "key": "lookback", "label": "Lookback window", "value": f"Last {format_days(lookback_days)}",
            "note": (
                "Not applied to the built-in sample data, which has no listing dates."
                if sample
                else "Listings on the market longer than this are ignored; a listing with no known age is kept."
            ),
        },
        {
            "key": "property_type", "label": "Building type", "value": property_type_label(property_type),
            "note": "Applies to RentCast data only." if sample else None,
        },
        {
            "key": "minimum", "label": "Minimum comparables", "value": str(minimum_comparables),
            "note": "With fewer, no valuation is shown.",
        },
        {
            "key": "bedrooms", "label": "Bedroom filter",
            "value": f"Within {comps.DEFAULT_BEDROOM_TOLERANCE} bedroom of the property",
        },
        {
            "key": "bathrooms", "label": "Bathroom filter",
            "value": f"Within {comps.DEFAULT_BATHROOM_TOLERANCE} bathroom of the property",
        },
        {
            "key": "size", "label": "Size matching", "value": size_with if sqft_given else size_without,
            "with_value": size_with, "without_value": size_without,
        },
        {
            "key": "outliers", "label": "Outlier handling",
            "value": "Rents below Q1 - 1.5 x IQR or above Q3 + 1.5 x IQR are removed",
            "note": "Needs 4 or more comparables; with fewer, none are removed.",
        },
        {
            "key": "listings", "label": "Listings considered",
            "value": (
                "26 built-in sample listings (not real data)"
                if sample
                else "Active listings, up to 500, as RentCast returns them (most recently seen first)"
            ),
        },
        {
            "key": "rent", "label": "Recommended rent", "value": "The median rent of the comparables used",
            "note": "Also shown: the 25th and 75th percentile and the average.",
        },
        {
            "key": "confidence", "label": "Confidence",
            "value": f"Low under {quality.MEDIUM_CONFIDENCE_MIN} comparables, medium {quality.MEDIUM_CONFIDENCE_MIN}"
            f"-{quality.HIGH_CONFIDENCE_MIN - 1}, high {quality.HIGH_CONFIDENCE_MIN} or more",
            "note": "Lowered one level when square feet are left blank.",
        },
        {
            "key": "lookup", "label": "Address lookup",
            "value": (
                "Demo addresses only (not real lookups)" if geocoder == "mock" else "US Census geocoder (US addresses)"
            ),
            "note": "Skipped when you enter latitude and longitude.",
        },
    ]


def search_line(search: dict) -> str:
    """The search a result used, in one line (distances in miles)."""
    return " \u00b7 ".join(
        [
            f"Within {format_miles(search['radius_miles'])}",
            f"listed in the last {format_days(search['lookback_days'])}",
            property_type_label(search["property_type"]),
            "matched on size" if search["sqft_used"] else "not matched on size",
            f"minimum {search['minimum_comparables']} comparables",
        ]
    )
