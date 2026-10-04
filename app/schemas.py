from pydantic import BaseModel


class ValuationRequest(BaseModel):
    address: str
    beds: int
    baths: float
    sqft: int


class ValuationResponse(BaseModel):
    recommended_rent: int
    p25: int
    median: int
    p75: int
    confidence: int
