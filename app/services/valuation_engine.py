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

    def __init__(
        self,
        comparable_count: int,
        minimum_required: int,
        funnel: quality.Funnel | None = None,
        search: dict | None = None,
    ):
        self.comparable_count = comparable_count
        self.minimum_required = minimum_required
        self.funnel = funnel
        self.search = search  # the search criteria used (radius, lookback, ...), when known
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
        if self.search is not None:
            body["search"] = self.search
        return body


def _locate_subject(subject: ValuationRequest, geocoder: Geocoder | None) -> tuple[float, float]:
    if subject.latitude is not None and subject.longitude is not None:
        return subject.latitude, subject.longitude
    if geocoder is not None:
        found = geocoder.geocode(subject.address)
        return found["latitude"], found["longitude"]
    return DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE


def _log_funnel(
    funnel: quality.Funnel, confidence: str | None, status: str, minimum: int, search: dict
) -> None:
    """One structured line per valuation: key=value text, plus the same fields as log-record
    attributes (record.funnel, record.confidence, record.building_type, record.search, ...) for JSON
    or other structured handlers. building_type is all, home, condo or apartment; distances are
    miles. No address or other request detail is logged."""
    fields = funnel.as_dict()
    nearest = search["nearest_listing_miles"]
    logger.info(
        "valuation_funnel status=%s fetched=%d after_lookback=%d after_distance=%d after_attributes=%d "
        "after_outliers=%d used=%d minimum=%d confidence=%s building_type=%s radius_miles=%g "
        "lookback_days=%d nearest_miles=%s sqft=%s",
        status,
        fields["comparables_fetched"],
        fields["comparables_after_lookback_filter"],
        fields["comparables_after_distance_filter"],
        fields["comparables_after_attribute_filter"],
        fields["comparables_after_outlier_filter"],
        fields["comparables_used"],
        minimum,
        confidence or "none",
        search["property_type"],
        search["radius_miles"],
        search["lookback_days"],
        "none" if nearest is None else f"{nearest:.2f}",
        "given" if search["sqft_used"] else "not_given",
        extra={
            "valuation_status": status,
            "funnel": fields,
            "minimum_required": minimum,
            "confidence": confidence,
            "building_type": search["property_type"],
            "search": search,
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

    The search is the request's: listings from the last `lookback_days`, within
    `search_radius_miles` (miles), with bedrooms and bathrooms within 1 and, when `sqft` is given,
    size within 20%. Without `sqft` the size filter is skipped and confidence drops one level, with
    a note saying so. The result carries the criteria used (`search`) and the closest listing the
    source returned.
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
        search_radius_miles=subject.search_radius_miles,
        lookback_days=subject.lookback_days,
        property_type=subject.property_type,
    )
    candidates = source.get_comparables(located)
    fetched = len(candidates)
    nearest = comps.nearest_miles(candidates, latitude, longitude)  # closest listing the source returned
    # Each stage keeps what survived the one before; a comparable is counted as removed by the first
    # stage it fails. The search radius is the one the person chose, and the data source was asked for
    # the same radius.
    candidates = comps.filter_by_lookback(candidates, subject.lookback_days)
    after_lookback = len(candidates)
    candidates = comps.filter_by_distance(candidates, latitude, longitude, subject.search_radius_miles)
    after_distance = len(candidates)
    candidates = comps.filter_by_bedrooms(candidates, subject.beds)
    candidates = comps.filter_by_bathrooms(candidates, subject.baths)
    if subject.sqft is not None:  # without square feet, listings are not matched on size
        candidates = comps.filter_by_sqft(candidates, subject.sqft)
    after_attributes = len(candidates)

    rents = stats.remove_outliers([c["rent"] for c in candidates])
    funnel = quality.Funnel(
        comparables_fetched=fetched,
        comparables_after_distance_filter=after_distance,
        comparables_after_attribute_filter=after_attributes,
        comparables_after_outlier_filter=len(rents),
        comparables_used=len(rents),
        comparables_after_lookback_filter=after_lookback,
    )
    search = {
        "radius_miles": subject.search_radius_miles,
        "distance_units": "miles",
        "lookback_days": subject.lookback_days,
        "property_type": subject.property_type,
        "sqft_used": subject.sqft is not None,
        "minimum_comparables": minimum,
        "nearest_listing_miles": None if nearest is None else round(nearest, 2),
    }
    if len(rents) < minimum:
        _log_funnel(funnel, confidence=None, status="insufficient_data", minimum=minimum, search=search)
        raise InsufficientDataError(len(rents), minimum, funnel, search)
    percentiles = stats.calculate_percentiles(rents)
    median = round(percentiles["median"])
    confidence = quality.confidence_for(funnel.comparables_used)
    confidence_notes = []
    if subject.sqft is None:
        confidence = quality.lower_confidence(confidence)
        confidence_notes.append(
            "Square feet were not given, so listings were not matched on size and confidence was lowered one level."
        )
    result = {
        "comparable_count": len(rents),
        "p25": round(percentiles["p25"]),
        "median": median,
        "p75": round(percentiles["p75"]),
        "average": round(stats.calculate_average(rents)),
        "recommended_rent": median,
        "confidence": confidence,
        "confidence_notes": confidence_notes,
        "search": search,
        "funnel": funnel.as_dict(),
    }
    _log_funnel(funnel, confidence=confidence, status="ok", minimum=minimum, search=search)
    if repository is not None:
        try:
            repository.save_valuation_request(subject, result)
        except PersistenceError:
            logger.exception("Could not save the valuation; returning it anyway")
    return result
