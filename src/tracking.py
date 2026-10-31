"""Record the work a run does in `run_steps`, and keep a running run's heartbeat.

The analysis functions (discovery, classification, consolidation) each do one step. They take the
run they belong to: the worker passes the job it claimed, and a script passes nothing, so the
function makes a run of its own with that one step and finishes it. Either way the step records the
model, tokens, and outcome, and the run's totals add up its steps.

A running run has a heartbeat: a background task updates `heartbeat_at` every few seconds. If the
process dies, the heartbeat goes stale and the worker deals with the run (see worker.py), so an
entity is never stuck behind a run nobody is working on.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_maker
from models import AnalysisRun, RunStatus, RunStep, RunType, StepKind, StepStatus

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = timedelta(seconds=15)
STALE_AFTER = timedelta(minutes=2)  # several missed beats

# A script that does one step makes a run of the matching type.
RUN_TYPE_FOR_STEP = {
    StepKind.DISCOVER: RunType.DISCOVER,
    StepKind.CLASSIFY: RunType.CLASSIFY,
    StepKind.CONSOLIDATE: RunType.CONSOLIDATE,
    StepKind.PROMOTE: RunType.ANALYZE,
}

# Steps with these outcomes produced something to look at but changed nothing, so they don't count as
# an analysis of the entity.
UNSAVED_OUTCOMES = ("dry run", "report only", "proposed")

heartbeats_enabled = True  # tests that share one connection switch this off


class RunConflict(RuntimeError):
    """The entity already has a run going."""


def start_heartbeat(run_id: int) -> asyncio.Task | None:
    """Update the run's heartbeat on its own connection until the task is cancelled."""
    if not heartbeats_enabled:
        return None

    async def beat() -> None:
        while True:
            try:
                async with async_session_maker() as session:
                    await session.execute(
                        update(AnalysisRun).where(AnalysisRun.id == run_id).values(heartbeat_at=func.now())
                    )
                    await session.commit()
            except Exception:  # a missed beat is harmless; the next one catches up
                logger.warning("Heartbeat for run %s failed", run_id, exc_info=True)
            await asyncio.sleep(HEARTBEAT_INTERVAL.total_seconds())

    return asyncio.create_task(beat())


async def stop_heartbeat(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@dataclass
class TrackedStep:
    run: AnalysisRun
    step: RunStep
    owns_run: bool  # true when this step made the run, so finishing the step finishes the run
    heartbeat: asyncio.Task | None = None

    async def progress(self, db: AsyncSession, reviews_processed: int) -> None:
        """Let a poller see how far the step has got."""
        await db.execute(
            update(RunStep).where(RunStep.id == self.step.id).values(reviews_processed=reviews_processed)
        )
        await db.commit()

    async def succeed(
        self,
        db: AsyncSession,
        *,
        reviews_processed: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        outcome: str | None = None,
        warnings: str | None = None,
    ) -> None:
        """Finish the step. `warnings` are notes on a step that worked, kept in `error`."""
        now = datetime.now(UTC)
        self.step.status = StepStatus.SUCCEEDED
        self.step.outcome = outcome
        self.step.reviews_processed = reviews_processed
        self.step.input_tokens = input_tokens
        self.step.output_tokens = output_tokens
        self.step.error = warnings
        self.step.completed_at = now
        self.run.reviews_processed += reviews_processed
        self.run.input_tokens += input_tokens
        self.run.output_tokens += output_tokens
        if self.owns_run:
            self.run.status = RunStatus.SUCCEEDED
            self.run.error = warnings
            self.run.completed_at = now
        await stop_heartbeat(self.heartbeat)
        await db.commit()

    async def fail(
        self,
        db: AsyncSession,
        error: BaseException | str,
        *,
        reviews_processed: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Roll back whatever the step left half done and record the failure.

        The session is rolled back first, so objects loaded earlier (such as the entity) are expired
        and need refreshing before reuse.
        """
        await db.rollback()
        await db.refresh(self.step)
        await db.refresh(self.run)
        now = datetime.now(UTC)
        self.step.status = StepStatus.FAILED
        self.step.error = str(error)
        self.step.reviews_processed = reviews_processed
        self.step.input_tokens = input_tokens
        self.step.output_tokens = output_tokens
        self.step.completed_at = now
        self.run.reviews_processed += reviews_processed
        self.run.input_tokens += input_tokens
        self.run.output_tokens += output_tokens
        if self.owns_run:
            self.run.status = RunStatus.FAILED
            self.run.error = str(error)
            self.run.completed_at = now
        await stop_heartbeat(self.heartbeat)
        await db.commit()


async def begin_step(
    db: AsyncSession,
    entity_id: int,
    kind: StepKind,
    model: str | None,
    run: AnalysisRun | None = None,
) -> TrackedStep:
    """Start a step inside `run`, or inside a new run of its own when none is given.

    A new run goes straight to `running`, so it conflicts (RunConflict) with a run already going
    for the entity: two runs must not work on one entity at once.
    """
    owns_run = run is None
    if run is None:
        now = datetime.now(UTC)
        run = AnalysisRun(
            entity_id=entity_id,
            type=RUN_TYPE_FOR_STEP[kind],
            status=RunStatus.RUNNING,
            model=model,
            started_at=now,
            heartbeat_at=now,
        )
        db.add(run)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise RunConflict(
                f"Entity {entity_id} already has a run in progress. Wait for it to finish."
            ) from None
    step = RunStep(
        run_id=run.id, kind=kind, status=StepStatus.RUNNING, model=model, started_at=datetime.now(UTC)
    )
    db.add(step)
    await db.commit()
    heartbeat = start_heartbeat(run.id) if owns_run else None
    return TrackedStep(run=run, step=step, owns_run=owns_run, heartbeat=heartbeat)


async def set_step_outcome(db: AsyncSession, run_id: int, kind: StepKind, outcome: str) -> None:
    """Change what a finished step did with its result, for example when a saved report is applied
    later. The caller commits."""
    await db.execute(
        update(RunStep).where(RunStep.run_id == run_id, RunStep.kind == kind).values(outcome=outcome)
    )
