from fastapi import APIRouter, Depends, HTTPException

from app.persistence.repositories import PersistenceError, ValuationRepository, get_repository
from app.schemas import StatsResponse

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
def get_stats(repository: ValuationRepository = Depends(get_repository)) -> StatsResponse:
    try:
        total = repository.count_valuations()
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return StatsResponse(
        total_valuations=total,
        database_path=repository.database_path,
        database_size_kb=repository.database_size_bytes() // 1024,
    )
