from fastapi import APIRouter

from app.schemas import ValuationRequest, ValuationResponse

router = APIRouter()


@router.post("/valuation", response_model=ValuationResponse)
def create_valuation(request: ValuationRequest) -> ValuationResponse:
    # Mocked result: real comps and pricing logic come later.
    return ValuationResponse(
        recommended_rent=2500,
        p25=2300,
        median=2500,
        p75=2700,
        confidence=85,
    )
