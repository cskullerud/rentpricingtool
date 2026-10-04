from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ValuationRequest(BaseModel):
    address: str
    beds: int
    baths: float
    sqft: int
    # Optional location of the subject. Give both or neither; if omitted, a default mock
    # coordinate is used (see app/config.py).
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

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


class ValuationResponse(BaseModel):
    comparable_count: int
    recommended_rent: int
    p25: int
    median: int
    p75: int
    average: int
    # low: fewer than 5 comparables used (3-4 normally), medium: 5-9, high: 10 or more.
    confidence: Literal["low", "medium", "high"]
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
