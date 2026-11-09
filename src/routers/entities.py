"""Create, list, read, edit, and delete entities."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_owned_entity, get_readable_entity
from auth import get_current_user
from config import settings
from database import get_db
from entities import create_entity
from models import AnalysisRun, Entity, Import, Review, Theme, ThemeKind, User
from queueing import ACTIVE_STATUSES, is_analysis_run
from routers.runs import run_detail
from schemas import EntityCreate, EntityDetail, EntityOut, EntityUpdate

router = APIRouter(prefix="/entities", tags=["entities"])


def review_count_of(entity_id):
    return select(func.count()).select_from(Review).where(Review.entity_id == entity_id).scalar_subquery()


def theme_count_of(entity_id):
    """Established themes, so a list can tell an analyzed entity from one that isn't yet."""
    return (
        select(func.count())
        .select_from(Theme)
        .where(Theme.entity_id == entity_id, Theme.kind == ThemeKind.DISCOVERED, Theme.merged_into_id.is_(None))
        .scalar_subquery()
    )


async def build_out(db: AsyncSession, entity: Entity, user: User) -> EntityOut:
    review_count, theme_count = (
        await db.execute(select(review_count_of(entity.id), theme_count_of(entity.id)))
    ).one()
    return EntityOut.build(entity, user.id, review_count, theme_count)


@router.post("", response_model=EntityOut, status_code=status.HTTP_201_CREATED)
async def create(
    body: EntityCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    owned_count = await db.scalar(
        select(func.count()).select_from(Entity).where(Entity.owner_id == user.id)
    )
    if owned_count >= settings.max_entities_per_user:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You can have at most {settings.max_entities_per_user} entities. Delete one to add another.",
        )
    entity = await create_entity(
        db, name=body.name, description=body.description, owner_id=user.id, metadata=body.metadata
    )
    return EntityOut.build(entity, user.id, review_count=0, theme_count=0)


@router.get("", response_model=list[EntityOut])
async def list_entities(
    scope: Literal["all", "mine", "public"] = "all",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    visible = {
        "all": or_(Entity.owner_id == user.id, Entity.is_public),
        "mine": Entity.owner_id == user.id,
        "public": Entity.is_public,
    }[scope]
    rows = await db.execute(
        select(Entity, review_count_of(Entity.id), theme_count_of(Entity.id))
        .where(visible)
        .order_by(Entity.created_at.desc(), Entity.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [EntityOut.build(entity, user.id, reviews, themes) for entity, reviews, themes in rows.all()]


@router.get("/{entity_id}", response_model=EntityDetail)
async def read(
    entity: Entity = Depends(get_readable_entity),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import_count = await db.scalar(
        select(func.count()).select_from(Import).where(Import.entity_id == entity.id)
    )
    latest_run = await db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.entity_id == entity.id, is_analysis_run())
        .order_by(AnalysisRun.id.desc())
        .limit(1)
    )
    base = await build_out(db, entity, user)
    return EntityDetail(
        **base.model_dump(),
        import_count=import_count,
        latest_run=await run_detail(db, latest_run) if latest_run else None,
    )


@router.patch("/{entity_id}", response_model=EntityOut)
async def update(
    body: EntityUpdate,
    entity: Entity = Depends(get_owned_entity),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        entity.name = changes["name"]
    if "description" in changes:  # null clears it
        entity.description = changes["description"]
    if changes.get("metadata") is not None:
        entity.metadata_ = changes["metadata"]
    await db.commit()
    return await build_out(db, entity, user)


@router.delete("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(entity: Entity = Depends(get_owned_entity), db: AsyncSession = Depends(get_db)):
    """Delete the entity. Its imports, reviews, themes, and runs go with it (foreign keys cascade)."""
    active_run = await db.scalar(
        select(AnalysisRun.id)
        .where(AnalysisRun.entity_id == entity.id, AnalysisRun.status.in_(ACTIVE_STATUSES))
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An analysis is queued or running for this entity. Try again when it finishes."
        )
    await db.delete(entity)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
