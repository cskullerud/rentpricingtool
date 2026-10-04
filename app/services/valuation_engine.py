from app.schemas import ValuationRequest
from app.services import comparables as comps
from app.services import statistics as stats


class NoComparablesError(Exception):
    """No comparable properties matched the subject after filtering."""


def run_valuation(subject: ValuationRequest) -> dict:
    """Price a subject property from the comparable dataset.

    comparable_count is the number of comparables actually used for pricing, i.e. after
    both the filters and the outlier removal.
    """
    candidates = comps.load_comparables()
    candidates = comps.filter_by_distance(candidates)
    candidates = comps.filter_by_bedrooms(candidates, subject.beds)
    candidates = comps.filter_by_bathrooms(candidates, subject.baths)
    candidates = comps.filter_by_sqft(candidates, subject.sqft)

    rents = [c["rent"] for c in candidates]
    if not rents:
        raise NoComparablesError("No comparable properties found for this property.")

    rents = stats.remove_outliers(rents)
    percentiles = stats.calculate_percentiles(rents)
    median = round(percentiles["median"])
    return {
        "comparable_count": len(rents),
        "p25": round(percentiles["p25"]),
        "median": median,
        "p75": round(percentiles["p75"]),
        "average": round(stats.calculate_average(rents)),
        "recommended_rent": median,
    }
