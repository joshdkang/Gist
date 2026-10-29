"""Decide what analysis an upload needs, and queue it.

A run classifies every review that has no tags when it starts, whichever upload added the review.
So a run that is still waiting already covers a later upload, and the upload reuses it instead of
queueing another (coalescing). A run that has started has fixed its set of reviews and can't absorb
new ones, so an upload while a classify run is running queues a fresh one behind it.

While the first discovery is queued or running the entity has no discovered themes yet, so no
second discover run is queued. The worker queues a classify run when discovery succeeds, and that
run covers these reviews.

At most one waiting run per entity and type is enforced by a partial unique index, so two uploads
arriving together can't both queue one: the loser catches the conflict and reuses the winner's run.
"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discover_themes import MIN_SAMPLE_SIZE
from models import AnalysisRun, Entity, Review, RunStatus, RunType, Theme, ThemeKind

ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


@dataclass
class QueueOutcome:
    run: AnalysisRun | None
    reused: bool = False
    message: str | None = None


async def count_runs_today(session: AsyncSession, user_id) -> int:
    """Runs this user requested in the last 24 hours, whatever their outcome."""
    return await session.scalar(
        select(func.count())
        .select_from(AnalysisRun)
        .where(
            AnalysisRun.requested_by == user_id,
            AnalysisRun.created_at > func.now() - timedelta(days=1),
        )
    )


async def find_run(
    session: AsyncSession, entity_id: int, run_type: RunType, statuses: tuple[RunStatus, ...]
) -> AnalysisRun | None:
    return await session.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.entity_id == entity_id,
            AnalysisRun.type == run_type,
            AnalysisRun.status.in_(statuses),
        )
        .order_by(AnalysisRun.id.desc())
        .limit(1)
    )


async def queue_run(
    session: AsyncSession, entity_id: int, user_id, run_type: RunType
) -> QueueOutcome:
    """Insert a queued run, or return the one that is already waiting if another upload got there first."""
    try:
        async with session.begin_nested():
            run = AnalysisRun(
                entity_id=entity_id, requested_by=user_id, type=run_type, status=RunStatus.QUEUED
            )
            session.add(run)
            await session.flush()
    except IntegrityError:
        waiting = await find_run(session, entity_id, run_type, (RunStatus.QUEUED,))
        if waiting is None:  # the other run was claimed in between; queue ours behind it
            return await queue_run(session, entity_id, user_id, run_type)
        return QueueOutcome(waiting, reused=True, message="Analysis was already queued for this entity.")
    return QueueOutcome(run)


async def queue_analysis(
    session: AsyncSession, entity: Entity, user_id, reviews_inserted: int
) -> QueueOutcome:
    """Queue the analysis an upload needs, and commit. Returns what happened and why."""
    if reviews_inserted == 0:
        return QueueOutcome(None, message="No new reviews were added, so there is nothing to analyze.")

    has_themes = await session.scalar(
        select(
            select(Theme.id)
            .where(Theme.entity_id == entity.id, Theme.kind == ThemeKind.DISCOVERED)
            .exists()
        )
    )

    if has_themes:
        outcome = await _queue_classify(session, entity.id, user_id)
    else:
        outcome = await _queue_discover(session, entity.id, user_id)
    await session.commit()
    return outcome


async def _queue_classify(session: AsyncSession, entity_id: int, user_id) -> QueueOutcome:
    waiting = await find_run(session, entity_id, RunType.CLASSIFY, (RunStatus.QUEUED,))
    if waiting is not None:
        return QueueOutcome(waiting, reused=True, message="Analysis was already queued for this entity.")
    return await queue_run(session, entity_id, user_id, RunType.CLASSIFY)


async def _queue_discover(session: AsyncSession, entity_id: int, user_id) -> QueueOutcome:
    active = await find_run(session, entity_id, RunType.DISCOVER, ACTIVE_STATUSES)
    if active is not None:
        return QueueOutcome(
            active,
            reused=True,
            message="Theme discovery is already in progress. These reviews will be classified when it finishes.",
        )
    review_count = await session.scalar(
        select(func.count()).select_from(Review).where(Review.entity_id == entity_id)
    )
    if review_count < MIN_SAMPLE_SIZE:
        return QueueOutcome(
            None,
            message=f"Themes can be discovered once the entity has {MIN_SAMPLE_SIZE} reviews; it has {review_count}.",
        )
    return await queue_run(session, entity_id, user_id, RunType.DISCOVER)
