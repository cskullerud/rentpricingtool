from pydantic import BaseModel


class ValuationRequest(BaseModel):
    address: str
    beds: int
    baths: float
    sqft: int


class ValuationResponse(BaseModel):
    comparable_count: int
    recommended_rent: int
    p25: int
    median: int
    p75: int
    average: int
    # Not produced by the engine yet; omitted from responses until it is.
    confidence: int | None = None
