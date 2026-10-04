from fastapi import APIRouter, Depends, HTTPException

from app.persistence.repositories import ValuationRepository, get_repository
from app.schemas import ValuationRequest, ValuationResponse
from app.services.data_sources import ComparableDataSource, get_provider
from app.services.geocoding import AddressNotFoundError, Geocoder, MockGeocoder
from app.services.valuation_engine import NoComparablesError, run_valuation

router = APIRouter()


def get_comparable_source() -> ComparableDataSource:
    """Choose the data source. Delegates to the provider registry (DATA_PROVIDER, default MOCK)."""
    return get_provider()


def get_geocoder() -> Geocoder:
    """Choose the geocoder. Like get_comparable_source(), the one place naming a provider."""
    return MockGeocoder()


@router.post("/valuation", response_model=ValuationResponse, response_model_exclude_none=True)
def create_valuation(
    request: ValuationRequest,
    source: ComparableDataSource = Depends(get_comparable_source),
    repository: ValuationRepository = Depends(get_repository),
    geocoder: Geocoder = Depends(get_geocoder),
) -> ValuationResponse:
    try:
        result = run_valuation(request, source, repository, geocoder)
    except (AddressNotFoundError, NoComparablesError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValuationResponse(**result)
