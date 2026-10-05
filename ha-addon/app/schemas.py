from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.search_options import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PROPERTY_TYPE,
    DEFAULT_RADIUS_MILES,
    LOOKBACK_OPTIONS_DAYS,
    PROPERTY_TYPES,
    RADIUS_OPTIONS_MILES,
)


def _not_a_bool(value):
    if isinstance(value, bool):
        raise ValueError("must be a number, not true/false")
    return value


class ValuationRequest(BaseModel):
    address: str
    beds: int
    baths: float
    # Optional. Without it listings are not matched on size, and confidence is lowered one level.
    sqft: int | None = None
    # How wide and how far back to look. The defaults are the original behaviour: 1 mile, and (with
    # RentCast data) listings from the last 90 days. Distances are miles.
    search_radius_miles: float = DEFAULT_RADIUS_MILES
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    property_type: str = DEFAULT_PROPERTY_TYPE  # all, home, condo or apartment (RentCast data only)
    # Optional location of the subject. Give both or neither; if omitted, a default mock
    # coordinate is used (see app/config.py).
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("search_radius_miles", mode="before")
    @classmethod
    def _radius_is_an_offered_choice(cls, value):
        return _not_a_bool(value)

    @field_validator("search_radius_miles")
    @classmethod
    def _radius_choice(cls, value):
        if value not in RADIUS_OPTIONS_MILES:
            raise ValueError("search_radius_miles must be one of " + ", ".join(f"{m:g}" for m in RADIUS_OPTIONS_MILES) + " (miles)")
        return value

    @field_validator("lookback_days", mode="before")
    @classmethod
    def _lookback_is_not_a_bool(cls, value):
        return _not_a_bool(value)

    @field_validator("lookback_days")
    @classmethod
    def _lookback_choice(cls, value):
        if value not in LOOKBACK_OPTIONS_DAYS:
            raise ValueError("lookback_days must be one of " + ", ".join(str(d) for d in LOOKBACK_OPTIONS_DAYS))
        return value

    @field_validator("property_type")
    @classmethod
    def _property_type_choice(cls, value):
        if value not in PROPERTY_TYPES:
            raise ValueError("property_type must be one of " + ", ".join(PROPERTY_TYPES))
        return value

    @model_validator(mode="after")
    def _coordinates_come_as_a_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class FunnelStats(BaseModel):
    """How many comparables survived each stage of the pipeline."""

    comparables_fetched: int
    comparables_after_distance_filter: int
    comparables_after_attribute_filter: int
    comparables_after_outlier_filter: int
    comparables_used: int
    # Listings left after the lookback window. Absent in results from before the window existed.
    comparables_after_lookback_filter: int | None = None


class SearchCriteria(BaseModel):
    """The search a valuation used, so no assumption is hidden. Distances are miles."""

    radius_miles: float
    distance_units: Literal["miles"] = "miles"
    lookback_days: int
    property_type: str
    sqft_used: bool  # false when square feet were not given, so listings were not matched on size
    minimum_comparables: int
    nearest_listing_miles: float | None = None  # the closest listing the source returned, if any


class ValuationResponse(BaseModel):
    comparable_count: int
    recommended_rent: int
    p25: int
    median: int
    p75: int
    average: int
    # low: fewer than 5 comparables used (3-4 normally), medium: 5-9, high: 10 or more.
    confidence: Literal["low", "medium", "high"]
    # Why confidence is what it is, when something other than the comparable count lowered it.
    confidence_notes: list[str] = []
    search: SearchCriteria
    funnel: FunnelStats


class StatsResponse(BaseModel):
    total_valuations: int
    database_path: str
    database_size_kb: int


class ValuationHistoryItem(BaseModel):
    id: int
    created_at: str
    address: str | None
    beds: int | None
    baths: float | None
    sqft: int | None
    latitude: float | None
    longitude: float | None
    comparable_count: int | None
    p25: float | None
    median: float | None
    p75: float | None
    average: float | None
    recommended_rent: float | None
