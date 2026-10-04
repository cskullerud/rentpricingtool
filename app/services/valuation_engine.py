import logging

from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
from app.persistence.repositories import PersistenceError, ValuationRepository
from app.schemas import ValuationRequest
from app.services import comparables as comps
from app.services import statistics as stats
from app.services.data_sources.base import ComparableDataSource

logger = logging.getLogger(__name__)


class NoComparablesError(Exception):
    """No comparable properties matched the subject after filtering."""


def run_valuation(
    subject: ValuationRequest,
    source: ComparableDataSource,
    repository: ValuationRepository | None = None,
) -> dict:
    """Price a subject property from the comparables supplied by `source`.

    The engine does not know where comparables come from; the caller injects a source.
    The subject's coordinates are used for the distance filter; if the request has none,
    the default mock coordinate from app/config.py is used.

    If a `repository` is given, each successful valuation is saved to it. Saving is best
    effort: if the database is unavailable the valuation is still returned. Failed
    valuations (no comparables) are not saved.
    comparable_count is the number of comparables actually used for pricing, i.e. after
    both the filters and the outlier removal.
    """
    latitude = subject.latitude if subject.latitude is not None else DEFAULT_SUBJECT_LATITUDE
    longitude = subject.longitude if subject.longitude is not None else DEFAULT_SUBJECT_LONGITUDE

    candidates = source.get_comparables()
    candidates = comps.filter_by_distance(candidates, latitude, longitude)
    candidates = comps.filter_by_bedrooms(candidates, subject.beds)
    candidates = comps.filter_by_bathrooms(candidates, subject.baths)
    candidates = comps.filter_by_sqft(candidates, subject.sqft)

    rents = [c["rent"] for c in candidates]
    if not rents:
        raise NoComparablesError("No comparable properties found for this property.")

    rents = stats.remove_outliers(rents)
    percentiles = stats.calculate_percentiles(rents)
    median = round(percentiles["median"])
    result = {
        "comparable_count": len(rents),
        "p25": round(percentiles["p25"]),
        "median": median,
        "p75": round(percentiles["p75"]),
        "average": round(stats.calculate_average(rents)),
        "recommended_rent": median,
    }
    if repository is not None:
        try:
            repository.save_valuation_request(subject, result)
        except PersistenceError:
            logger.exception("Could not save the valuation; returning it anyway")
    return result
