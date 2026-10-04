from fastapi import APIRouter, HTTPException

from app.schemas import ValuationRequest, ValuationResponse
from app.services.valuation_engine import NoComparablesError, run_valuation

router = APIRouter()


@router.post("/valuation", response_model=ValuationResponse, response_model_exclude_none=True)
def create_valuation(request: ValuationRequest) -> ValuationResponse:
    try:
        result = run_valuation(request)
    except NoComparablesError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValuationResponse(**result)
