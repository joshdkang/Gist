"""Queue the analysis an upload needs.

Every job is the same kind, `analyze`: when the worker runs it, it looks at the entity and does
whatever is needed (see worker.py). So an upload only has to make sure one job is waiting. A job
that is still waiting will see the reviews of every upload made before it starts, so a later upload
reuses it instead of queueing another. A job that has started has already looked at the entity, so
an upload while one is running queues a new job behind it.

An entity has at most one waiting and one running run, enforced by partial unique indexes. That is
what makes reuse safe when two uploads arrive together: the second insert fails and the endpoint
returns the first upload's run.
"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discover_themes import MIN_SAMPLE_SIZE
from models import AnalysisRun, Entity, Review, RunStatus, RunStep, RunType, Theme, ThemeKind
from tracking import UNSAVED_OUTCOMES

ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


def is_analysis_run():
    """A worker job, or a script's classify run or discovery that saved its result.

    The scripts also make runs that change nothing (a classify dry run, a discovery that only wrote
    a report, a consolidation proposal) and runs of their own for consolidation. Those are not
    what a user thinks of as the entity's latest analysis. A worker job always counts: it saves as
    it goes, so a failure still tells the user something.
    """
    unsaved_step = exists().where(RunStep.run_id == AnalysisRun.id, RunStep.outcome.in_(UNSAVED_OUTCOMES))
    return or_(
        AnalysisRun.type == RunType.ANALYZE,
        and_(AnalysisRun.type != RunType.CONSOLIDATE, not_(unsaved_step)),
    )


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


async def find_queued_run(session: AsyncSession, entity_id: int) -> AnalysisRun | None:
    return await session.scalar(
        select(AnalysisRun).where(AnalysisRun.entity_id == entity_id, AnalysisRun.status == RunStatus.QUEUED)
    )


async def queue_run(session: AsyncSession, entity_id: int, user_id) -> QueueOutcome:
    """Insert a queued job, or return the one already waiting if another upload got there first."""
    try:
        async with session.begin_nested():
            run = AnalysisRun(
                entity_id=entity_id, requested_by=user_id, type=RunType.ANALYZE, status=RunStatus.QUEUED
            )
            session.add(run)
            await session.flush()
    except IntegrityError:
        waiting = await find_queued_run(session, entity_id)
        if waiting is None:  # it was claimed in between; queue ours behind it
            return await queue_run(session, entity_id, user_id)
        return QueueOutcome(waiting, reused=True, message="Analysis was already queued for this entity.")
    return QueueOutcome(run)


async def queue_analysis(
    session: AsyncSession, entity: Entity, user_id, reviews_inserted: int
) -> QueueOutcome:
    """Make sure a job is waiting to analyze the upload, and commit. Returns what happened and why."""
    if reviews_inserted == 0:
        return QueueOutcome(None, message="No new reviews were added, so there is nothing to analyze.")

    has_themes = await session.scalar(
        select(
            select(Theme.id)
            .where(Theme.entity_id == entity.id, Theme.kind == ThemeKind.DISCOVERED)
            .exists()
        )
    )
    if not has_themes:
        review_count = await session.scalar(
            select(func.count()).select_from(Review).where(Review.entity_id == entity.id)
        )
        if review_count < MIN_SAMPLE_SIZE:
            return QueueOutcome(
                None,
                message=f"Themes can be discovered once the entity has {MIN_SAMPLE_SIZE} reviews; it has {review_count}.",
            )

    waiting = await find_queued_run(session, entity.id)
    outcome = (
        QueueOutcome(waiting, reused=True, message="Analysis was already queued for this entity.")
        if waiting is not None
        else await queue_run(session, entity.id, user_id)
    )
    await session.commit()
    return outcome
