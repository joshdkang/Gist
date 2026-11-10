"""An entity's ratings: count, average and distribution. Readable like the entity itself."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_readable_entity
from database import get_db
from models import Entity
from rating_stats import rating_summary
from schemas import RatingSummary

router = APIRouter(prefix="/entities/{entity_id}/ratings", tags=["ratings"])


@router.get("", response_model=RatingSummary)
async def read_ratings(entity: Entity = Depends(get_readable_entity), db: AsyncSession = Depends(get_db)):
    """One summary per rating scale the entity's imports used, most ratings first.

    Reviews without a rating are counted in `total_reviews` only. Nothing in the analysis uses
    ratings; this is for showing them alongside the results.
    """
    return await rating_summary(db, entity.id)
