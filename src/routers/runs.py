"""Analysis job status for an entity, so a frontend can poll a run it just queued."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_readable_entity
from database import get_db
from models import AnalysisRun, Entity, RunStatus
from schemas import RunOut

router = APIRouter(prefix="/entities/{entity_id}/runs", tags=["runs"])


@router.get("", response_model=list[RunOut])
async def list_runs(
    run_status: RunStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """The entity's runs, newest first."""
    query = select(AnalysisRun).where(AnalysisRun.entity_id == entity.id)
    if run_status is not None:
        query = query.where(AnalysisRun.status == run_status)
    runs = await db.scalars(query.order_by(AnalysisRun.id.desc()).limit(limit))
    return list(runs)


@router.get("/{run_id}", response_model=RunOut)
async def read_run(
    run_id: int,
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(AnalysisRun, run_id)
    if run is None or run.entity_id != entity.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    return run
