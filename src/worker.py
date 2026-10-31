"""The background worker: claims queued runs and analyzes the entity each one is for.

    python worker.py [--once]

Every job is the same kind, `analyze`. When it runs, it looks at the entity's state and does
whatever is needed, in order, one recorded step each (see run_steps):

1. discover: if the entity has no discovered themes yet (needs at least 50 reviews);
2. classify: if any review has no tags;
3. consolidate: if enough new candidate themes have piled up;
4. promote: if any candidate is raised by enough reviews.

Because the job reads the state when it runs, it never acts on a stale decision: an upload only has to
make sure a job is waiting (queueing.py), and a job that was interrupted does the same inspection
again and carries on from there. The API never calls the model, so requests never wait on it.

An entity has at most one running and one waiting run, so a claim skips an entity that is already
being worked on. A step that failed is not tried again for an hour, so a step that keeps failing
doesn't cost money on every upload; the job records it as skipped and does the steps after it.
"""
import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from anthropic import AsyncAnthropic
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

import classify_reviews as classify
from config import settings
from consolidate_themes import (
    ApplySummary,
    apply_consolidation,
    describe_and_promote,
    run_consolidation,
    trigger_status,
)
from database import async_session_maker, engine
from describe_themes import REFINE_MODEL
from discover_themes import (
    MIN_SAMPLE_SIZE,
    has_discovered_themes,
    run_discovery,
    save_discovered_themes,
)
from models import (
    AnalysisRun,
    Entity,
    Review,
    ReviewTheme,
    RunStatus,
    RunStep,
    RunType,
    StepKind,
    StepStatus,
)
from tracking import STALE_AFTER, begin_step, start_heartbeat, stop_heartbeat

logger = logging.getLogger("worker")

POLL_SECONDS = 5
FAILED_STEP_COOLDOWN = timedelta(hours=1)


class JobFailed(Exception):
    """A step of the job failed; the step has recorded why."""


async def claim_next_run(db: AsyncSession) -> AnalysisRun | None:
    """Take the oldest queued run whose entity has no run going, and mark it running.

    SKIP LOCKED lets several workers claim at once without waiting for each other or taking the
    same row. The unique index on running runs is the backstop: if two workers race to start runs
    for one entity, the second one's claim is refused and it moves on.
    """
    running = aliased(AnalysisRun)
    run = await db.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.status == RunStatus.QUEUED,
            ~exists().where(running.entity_id == AnalysisRun.entity_id, running.status == RunStatus.RUNNING),
        )
        .order_by(AnalysisRun.created_at, AnalysisRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if run is None:
        await db.rollback()
        return None
    now = datetime.now(UTC)
    run.status = RunStatus.RUNNING
    run.started_at = now
    run.heartbeat_at = now
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None
    return run


async def recover_stale_runs(db: AsyncSession) -> int:
    """Deal with running runs whose worker stopped (their heartbeat has gone stale).

    A worker job goes back in the queue, because running it again does the same inspection and
    carries on. If the entity has a run waiting by then, that one will cover the work, so the stale
    run is just marked failed. A script's run is marked failed, since a script can't be re-run for it.
    """
    now = datetime.now(UTC)
    cutoff = now - STALE_AFTER
    stale = list(
        await db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.status == RunStatus.RUNNING,
                or_(
                    AnalysisRun.heartbeat_at < cutoff,
                    (AnalysisRun.heartbeat_at.is_(None)) & (AnalysisRun.started_at < cutoff),
                ),
            )
            .with_for_update(skip_locked=True)
        )
    )
    for run in stale:
        await db.execute(
            update(RunStep)
            .where(RunStep.run_id == run.id, RunStep.status == StepStatus.RUNNING)
            .values(status=StepStatus.FAILED, error="The worker stopped.", completed_at=now)
        )
        has_waiting_run = await db.scalar(
            select(exists().where(AnalysisRun.entity_id == run.entity_id, AnalysisRun.status == RunStatus.QUEUED))
        )
        if run.type == RunType.ANALYZE and not has_waiting_run:
            run.status = RunStatus.QUEUED
            run.started_at = None
            run.heartbeat_at = None
            logger.warning("Run %s lost its worker; queued again.", run.id)
        else:
            run.status = RunStatus.FAILED
            run.error = "The worker stopped before the run finished."
            run.completed_at = now
            logger.warning("Run %s lost its worker; marked failed.", run.id)
    await db.commit()
    return len(stale)


async def recent_failure(db: AsyncSession, entity_id: int, kind: StepKind) -> RunStep | None:
    """The entity's latest step of this kind, if it failed less than the cooldown ago."""
    latest = await db.scalar(
        select(RunStep)
        .join(AnalysisRun, AnalysisRun.id == RunStep.run_id)
        .where(
            AnalysisRun.entity_id == entity_id,
            RunStep.kind == kind,
            RunStep.status.in_((StepStatus.SUCCEEDED, StepStatus.FAILED)),
        )
        .order_by(RunStep.id.desc())
        .limit(1)
    )
    if (
        latest is not None
        and latest.status == StepStatus.FAILED
        and latest.completed_at is not None
        and latest.completed_at > datetime.now(UTC) - FAILED_STEP_COOLDOWN
    ):
        return latest
    return None


async def mark_step_failed(db: AsyncSession, run_id: int, kind: StepKind, error: BaseException) -> None:
    """A step that finished its model work can still fail afterwards (saving or applying the result)."""
    await db.rollback()
    await db.execute(
        update(RunStep)
        .where(RunStep.run_id == run_id, RunStep.kind == kind, RunStep.status == StepStatus.SUCCEEDED)
        .values(status=StepStatus.FAILED, outcome=None, error=str(error))
    )
    await db.commit()


async def attempt(
    db: AsyncSession, run: AnalysisRun, kind: StepKind, step: Callable[[], Awaitable[None]]
) -> list[str]:
    """Run one step unless it failed within the cooldown. Returns notes for the run.

    Raises JobFailed when the step fails: the steps after it depend on it, or would only fail the
    same way. A skipped step doesn't stop the job.
    """
    earlier = await recent_failure(db, run.entity_id, kind)
    if earlier is not None:
        retry_at = earlier.completed_at + FAILED_STEP_COOLDOWN
        note = f"Skipped {kind.value}: it failed at {earlier.completed_at:%H:%M} UTC; trying again after {retry_at:%H:%M}."
        db.add(RunStep(run_id=run.id, kind=kind, status=StepStatus.SKIPPED, error=note))
        await db.commit()
        return [note]
    try:
        await step()
    except Exception as error:
        await db.rollback()
        await db.refresh(run)
        raise JobFailed(f"{kind.value} failed: {error}") from error
    return []


async def count_unclassified_reviews(db: AsyncSession, entity_id: int) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(Review)
        .where(
            Review.entity_id == entity_id,
            ~exists().where(ReviewTheme.review_id == Review.id),
        )
    )


async def analyze(db: AsyncSession, client: AsyncAnthropic, run: AnalysisRun) -> list[str]:
    """Look at the entity and do whatever it needs. Returns notes for the run; raises JobFailed."""
    entity_id = run.entity_id
    entity = await db.get(Entity, entity_id)
    notes: list[str] = []

    async def discover_step() -> None:
        await db.refresh(entity)
        _, result, _ = await run_discovery(db, client, entity, run=run)
        try:
            await save_discovered_themes(db, entity_id, run.id, result)
        except Exception as error:
            await mark_step_failed(db, run.id, StepKind.DISCOVER, error)
            raise

    async def classify_step() -> None:
        await db.refresh(entity)
        await classify.run_classification(db, client, entity, run=run)

    async def consolidate_step() -> None:
        await db.refresh(entity)
        outcome = await run_consolidation(db, client, entity, run=run)
        try:
            await apply_consolidation(db, run.id, outcome.result)
        except Exception as error:
            await mark_step_failed(db, run.id, StepKind.CONSOLIDATE, error)
            raise

    async def promote_step() -> None:
        tracked = await begin_step(db, entity_id, StepKind.PROMOTE, REFINE_MODEL, run)
        summary = ApplySummary()
        try:
            await describe_and_promote(db, client, entity_id, summary, True)
        except Exception as error:
            await tracked.fail(db, error, input_tokens=summary.input_tokens, output_tokens=summary.output_tokens)
            raise
        await tracked.succeed(
            db,
            input_tokens=summary.input_tokens,
            output_tokens=summary.output_tokens,
            outcome="promoted" if summary.promoted else "nothing promoted",
            warnings=f"Promoted {', '.join(summary.promoted)}." if summary.promoted else None,
        )

    if not await has_discovered_themes(db, entity_id):
        review_count = await db.scalar(
            select(func.count()).select_from(Review).where(Review.entity_id == entity_id)
        )
        if review_count < MIN_SAMPLE_SIZE:
            return [f"Waiting for {MIN_SAMPLE_SIZE} reviews to find themes; the entity has {review_count}."]
        notes += await attempt(db, run, StepKind.DISCOVER, discover_step)
        if not await has_discovered_themes(db, entity_id):
            return notes  # discovery was skipped; nothing below can run without themes

    if await count_unclassified_reviews(db, entity_id):
        notes += await attempt(db, run, StepKind.CLASSIFY, classify_step)

    status = await trigger_status(db, entity_id)
    if status.should_consolidate:
        notes += await attempt(db, run, StepKind.CONSOLIDATE, consolidate_step)
        status = await trigger_status(db, entity_id)
    if status.should_promote:
        notes += await attempt(db, run, StepKind.PROMOTE, promote_step)
    return notes


async def process_run(db: AsyncSession, client: AsyncAnthropic, run: AnalysisRun) -> None:
    """Analyze the entity for a claimed run and record how it ended."""
    heartbeat = start_heartbeat(run.id)
    run_id = run.id
    try:
        notes = await analyze(db, client, run)
        status, error = RunStatus.SUCCEEDED, "; ".join(notes) or None
    except JobFailed as failure:
        status, error = RunStatus.FAILED, str(failure)
    except Exception as failure:  # a bug or an outage outside any step
        logger.exception("Run %s failed", run_id)
        await db.rollback()
        status, error = RunStatus.FAILED, f"Unexpected error: {failure}"
    finally:
        await stop_heartbeat(heartbeat)
    await db.refresh(run)
    run.status = status
    run.error = error
    run.completed_at = datetime.now(UTC)
    await db.commit()


async def run_worker(client: AsyncAnthropic, *, once: bool = False, poll_seconds: float = POLL_SECONDS) -> None:
    """Claim and process runs until stopped. With `once`, stop when the queue is empty."""
    while True:
        async with async_session_maker() as db:
            await recover_stale_runs(db)
            run = await claim_next_run(db)
            if run is not None:
                logger.info("Run %s: analyzing entity %s", run.id, run.entity_id)
                await process_run(db, client, run)
                continue
        if once:
            return
        await asyncio.sleep(poll_seconds)


async def main(once: bool) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        await run_worker(client, once=once)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Process queued analysis runs.")
    parser.add_argument("--once", action="store_true", help="stop when the queue is empty")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.once))
    except KeyboardInterrupt:
        pass
