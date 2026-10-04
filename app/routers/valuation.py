from fastapi import APIRouter, Depends, HTTPException

from app.persistence.repositories import ValuationRepository, get_repository
from app.schemas import ValuationRequest, ValuationResponse
from app.services.data_sources import ComparableDataSource, MockComparableSource
from app.services.valuation_engine import NoComparablesError, run_valuation

router = APIRouter()


def get_comparable_source() -> ComparableDataSource:
    """Choose the data source. This is the one place that names a concrete provider."""
    return MockComparableSource()


@router.post("/valuation", response_model=ValuationResponse, response_model_exclude_none=True)
def create_valuation(
    request: ValuationRequest,
    source: ComparableDataSource = Depends(get_comparable_source),
    repository: ValuationRepository = Depends(get_repository),
) -> ValuationResponse:
    try:
        result = run_valuation(request, source, repository)
    except NoComparablesError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValuationResponse(**result)
