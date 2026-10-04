from fastapi import APIRouter, Depends, HTTPException

from app.persistence.repositories import PersistenceError, ValuationRepository, get_repository
from app.schemas import ValuationHistoryItem

router = APIRouter()

HISTORY_LIMIT = 25


@router.get("/history", response_model=list[ValuationHistoryItem])
def get_history(repository: ValuationRepository = Depends(get_repository)) -> list[dict]:
    """The latest valuations, newest first."""
    try:
        return repository.get_recent_valuations(limit=HISTORY_LIMIT)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
