import logging

from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE, MIN_COMPARABLES_REQUIRED
from app.persistence.repositories import PersistenceError, ValuationRepository
from app.schemas import ValuationRequest
from app.services import comparables as comps
from app.services import quality
from app.services import statistics as stats
from app.services.data_sources.base import ComparableDataSource, SubjectProperty
from app.services.geocoding.base import Geocoder

logger = logging.getLogger(__name__)


class NoComparablesError(Exception):
    """No comparable properties matched the subject after filtering."""


class InsufficientDataError(NoComparablesError):
    """Too few comparables remained (after filtering and outlier removal) to price reliably.

    A subclass of NoComparablesError, so callers that already handle "nothing matched" also
    handle this. `comparable_count` can be 0.
    """

    def __init__(self, comparable_count: int, minimum_required: int, funnel: quality.Funnel | None = None):
        self.comparable_count = comparable_count
        self.minimum_required = minimum_required
        self.funnel = funnel
        if comparable_count == 0:
            message = "No comparable properties found for this property."
        else:
            noun = "property" if comparable_count == 1 else "properties"
            message = (
                f"Only {comparable_count} comparable {noun} remained after filtering; "
                f"at least {minimum_required} are required for a valuation."
            )
        super().__init__(message)

    def to_response(self) -> dict:
        """The body returned to API clients instead of a valuation."""
        body = {
            "status": "insufficient_data",
            "detail": str(self),
            "comparable_count": self.comparable_count,
            "minimum_required": self.minimum_required,
        }
        if self.funnel is not None:
            body["funnel"] = self.funnel.as_dict()
        return body


def _locate_subject(subject: ValuationRequest, geocoder: Geocoder | None) -> tuple[float, float]:
    if subject.latitude is not None and subject.longitude is not None:
        return subject.latitude, subject.longitude
    if geocoder is not None:
        found = geocoder.geocode(subject.address)
        return found["latitude"], found["longitude"]
    return DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE


def _log_funnel(funnel: quality.Funnel, confidence: str | None, status: str, minimum: int) -> None:
    """One structured line per valuation: key=value text, plus the same fields as log-record
    attributes (record.funnel, record.confidence, ...) for JSON or other structured handlers.
    No address or other request detail is logged."""
    fields = funnel.as_dict()
    logger.info(
        "valuation_funnel status=%s fetched=%d after_distance=%d after_attributes=%d "
        "after_outliers=%d used=%d minimum=%d confidence=%s",
        status,
        fields["comparables_fetched"],
        fields["comparables_after_distance_filter"],
        fields["comparables_after_attribute_filter"],
        fields["comparables_after_outlier_filter"],
        fields["comparables_used"],
        minimum,
        confidence or "none",
        extra={
            "valuation_status": status,
            "funnel": fields,
            "minimum_required": minimum,
            "confidence": confidence,
        },
    )


def run_valuation(
    subject: ValuationRequest,
    source: ComparableDataSource,
    repository: ValuationRepository | None = None,
    geocoder: Geocoder | None = None,
    min_comparables: int | None = None,
) -> dict:
    """Price a subject property from the comparables supplied by `source`.

    The engine does not know where comparables come from; the caller injects a source. The
    source is given the subject with its resolved location, and the engine still applies
    all the filters itself.

    Where the subject is: coordinates in the request are used as given and the geocoder is
    not called. Otherwise the address is geocoded (which raises AddressNotFoundError if it
    can't be). Callers that pass no geocoder get the default mock coordinate from
    app/config.py instead.

    If a `repository` is given, each successful valuation is saved to it. Saving is best
    effort: if the database is unavailable the valuation is still returned. Failed
    valuations (insufficient data) are not saved.
    comparable_count is the number of comparables actually used for pricing, i.e. after
    both the filters and the outlier removal.

    If fewer than `min_comparables` (default: config.MIN_COMPARABLES_REQUIRED, 3) remain
    after the filters and the outlier removal, InsufficientDataError is raised instead of
    returning a valuation.
    """
    minimum = MIN_COMPARABLES_REQUIRED if min_comparables is None else min_comparables
    latitude, longitude = _locate_subject(subject, geocoder)

    located = SubjectProperty(
        address=subject.address,
        latitude=latitude,
        longitude=longitude,
        beds=subject.beds,
        baths=subject.baths,
        sqft=subject.sqft,
    )
    candidates = source.get_comparables(located)
    fetched = len(candidates)
    candidates = comps.filter_by_distance(candidates, latitude, longitude)
    after_distance = len(candidates)
    candidates = comps.filter_by_bedrooms(candidates, subject.beds)
    candidates = comps.filter_by_bathrooms(candidates, subject.baths)
    candidates = comps.filter_by_sqft(candidates, subject.sqft)
    after_attributes = len(candidates)

    rents = stats.remove_outliers([c["rent"] for c in candidates])
    funnel = quality.Funnel(
        comparables_fetched=fetched,
        comparables_after_distance_filter=after_distance,
        comparables_after_attribute_filter=after_attributes,
        comparables_after_outlier_filter=len(rents),
        comparables_used=len(rents),
    )
    if len(rents) < minimum:
        _log_funnel(funnel, confidence=None, status="insufficient_data", minimum=minimum)
        raise InsufficientDataError(len(rents), minimum, funnel)
    percentiles = stats.calculate_percentiles(rents)
    median = round(percentiles["median"])
    result = {
        "comparable_count": len(rents),
        "p25": round(percentiles["p25"]),
        "median": median,
        "p75": round(percentiles["p75"]),
        "average": round(stats.calculate_average(rents)),
        "recommended_rent": median,
        "confidence": quality.confidence_for(funnel.comparables_used),
        "funnel": funnel.as_dict(),
    }
    _log_funnel(funnel, confidence=result["confidence"], status="ok", minimum=minimum)
    if repository is not None:
        try:
            repository.save_valuation_request(subject, result)
        except PersistenceError:
            logger.exception("Could not save the valuation; returning it anyway")
    return result
