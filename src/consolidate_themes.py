"""Consolidation and promotion of candidate themes (this is rediscovery).

The classifier creates a candidate theme for every uncovered point it finds, one review at a time,
so candidates include spelling variants ("Brisket", "Beef brisket") and closely related items
("Catfish", "Redfish"). This script:

1. Consolidation runs when enough new candidates have piled up since the last one, or when forced.
   Promotion is checked separately and needs no model call to decide: any unmerged candidate that
   enough reviews raise is promotable (see trigger_status).
2. Consolidates: asks the model to group overlapping candidates. Each group's other members are
   merged into one canonical candidate by setting merged_into_id and merged_in_run_id. Nothing is
   deleted or retagged, counts follow the mapping, and a merge is undone by clearing those columns.
3. Promotes: a candidate that enough reviews raise, counting its merged members, becomes a
   `discovered` theme. Its tags already exist, so no reclassification is needed.

The functions take their session and API client as arguments so the job worker can reuse them.

Usage (from src/):
    python consolidate_themes.py <entity_id> --status                 (what the triggers say; changes nothing)
    python consolidate_themes.py <entity_id> [--force] [--dry-run] [--no-promote]
    python consolidate_themes.py <entity_id> --promote-only           (promote without consolidating)
    python consolidate_themes.py --from-report PATH [--no-promote]    (apply a dry run's report)
    python consolidate_themes.py --undo RUN_ID
"""
import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

import classify_reviews as classify
import discover_themes as discover
from config import settings
from database import async_session_maker, engine
from describe_themes import describe_candidates, refine_description
from models import AnalysisRun, Entity, ReviewTheme, RunStatus, RunType, Theme, ThemeKind

PROMPT_VERSION = 2  # see docs/decisions.md
# A candidate becomes a theme when this share of classified reviews raise it, but never fewer
# than PROMOTE_MIN_REVIEWS, so a small review set doesn't promote a topic a handful of reviews mention.
PROMOTE_MIN_SHARE = 0.03
PROMOTE_MIN_REVIEWS = 25
# Consolidate once this many unmerged candidates have been created since the last applied
# consolidation: 1% of the classified reviews, but at least 15, so a small review set still gets
# consolidated and a large one is not run for a handful of new titles. A run costs about the
# same however many candidates are new, so waiting for a batch saves money.
CONSOLIDATE_MIN_NEW_SHARE = 0.01
CONSOLIDATE_MIN_NEW_CANDIDATES = 15
# Markers kept in analysis_runs.error so the last applied consolidation can be found later.
PROPOSED_NOTE = "Consolidation proposed, not applied."
APPLIED_NOTE = "Consolidation applied."
MAX_CANDIDATES_SHOWN = 1500  # the most common ones; a longer tail is left for a later pass
OUTPUT_DIR = Path(__file__).parent / "themes"

PROMPT_TEMPLATE = (
    """You are consolidating candidate themes for customer reviews of {subject}.

A candidate is a specific point that some reviews raised and that no established theme covers.
Candidates were created one review at a time, so many are duplicates, spelling variants, or
closely related items.

Established themes (for reference only, do not repeat them):
{established}

Candidates, each with the number of reviews that raised it:
{candidates}

Group candidates that belong together into one theme: the same point written differently
("Brisket", "Beef brisket", "Smoked beef brisket"), and related items that make one coherent
theme (for example several kinds of fish as one fish theme). For each group give a name, a one
or two sentence description, and the ids of its members. A group needs at least two members,
and a candidate can be in only one group. If one member already has the best name for the group,
use exactly that name. Leave a candidate out of every group when it is a distinct point that
belongs with no other candidate.

Groups must not overlap each other either. If one group's topic is a kind of another group's
topic (a specific fish dish next to a general fish group, or short ribs next to a beef group),
put them in one group, unless the specific one is raised by a large share of reviews on its own.

A candidate raised by at least {promote_reviews} reviews, or a group that reaches that number
together, can become an established theme. For every such candidate that you did not put in a
group, add an entry to "standalone" with its id and a one or two sentence description of its
scope.

"""
    + discover.THEME_RULES
)


class ThemeGroup(BaseModel):
    name: str = Field(description="Short topic name for the group, with no sentiment words")
    description: str = Field(
        description="One or two sentences defining which reviews belong, whether positive or negative"
    )
    member_ids: list[int] = Field(min_length=2, description="Ids of the candidates in this group")


class StandaloneDescription(BaseModel):
    id: int
    description: str


class ConsolidationResult(BaseModel):
    groups: list[ThemeGroup]
    standalone: list[StandaloneDescription]

    @model_validator(mode="after")
    def check_groups(self):
        discover.check_theme_names([group.name for group in self.groups])
        member_ids = [member_id for group in self.groups for member_id in group.member_ids]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("A candidate can be in only one group.")
        return self


@dataclass
class CandidateInfo:
    id: int
    name: str
    description: str
    reviews: int  # distinct reviews that raised it, counting candidates merged into it


@dataclass
class TriggerStatus:
    """What the triggers say about an entity right now."""
    classified_reviews: int
    new_candidates: int  # unmerged candidates created since the last applied consolidation
    new_candidate_reviews: int  # distinct reviews that raised them
    consolidate_threshold: int
    last_consolidation_run_id: int | None
    promotable: list[str]  # unmerged candidates that already pass the promotion threshold
    promote_threshold: int

    @property
    def should_consolidate(self) -> bool:
        return self.new_candidates >= self.consolidate_threshold

    @property
    def should_promote(self) -> bool:
        return bool(self.promotable)


@dataclass
class ConsolidationOutcome:
    status: TriggerStatus
    skipped: str | None = None
    run: AnalysisRun | None = None
    result: ConsolidationResult | None = None
    candidates: list[CandidateInfo] = field(default_factory=list)


def promote_min_reviews(classified_reviews: int) -> int:
    return max(PROMOTE_MIN_REVIEWS, round(PROMOTE_MIN_SHARE * classified_reviews))


async def candidate_share(db: AsyncSession, entity_id: int) -> tuple[int, int]:
    """(reviews with a candidate tag, reviews with any tag) for the entity."""
    candidate_reviews = await db.scalar(
        select(func.count(func.distinct(ReviewTheme.review_id)))
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id, Theme.kind == ThemeKind.CANDIDATE)
    )
    classified_reviews = await db.scalar(
        select(func.count(func.distinct(ReviewTheme.review_id)))
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id)
    )
    return candidate_reviews, classified_reviews


async def last_applied_consolidation(db: AsyncSession, entity_id: int) -> AnalysisRun | None:
    return await db.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.entity_id == entity_id,
            AnalysisRun.type == RunType.DISCOVER,
            AnalysisRun.status == RunStatus.SUCCEEDED,
            AnalysisRun.error == APPLIED_NOTE,
        )
        .order_by(AnalysisRun.id.desc())
        .limit(1)
    )


async def trigger_status(db: AsyncSession, entity_id: int) -> TriggerStatus:
    """Count what has piled up since the last applied consolidation, and what can be promoted.

    "New" means an unmerged candidate created by a run that started after the last applied
    consolidation finished (all of them if there has been none). The candidates a consolidation
    itself creates are not new.
    """
    _, classified_reviews = await candidate_share(db, entity_id)
    last = await last_applied_consolidation(db, entity_id)
    created_by = aliased(AnalysisRun)
    query = (
        select(Theme.id)
        .join(created_by, created_by.id == Theme.discovered_in_run_id)
        .where(
            Theme.entity_id == entity_id,
            Theme.kind == ThemeKind.CANDIDATE,
            Theme.merged_into_id.is_(None),
        )
    )
    if last is not None:
        query = query.where(created_by.started_at > last.completed_at)
    new_ids = list(await db.scalars(query))
    new_candidate_reviews = 0
    if new_ids:
        new_candidate_reviews = await db.scalar(
            select(func.count(func.distinct(ReviewTheme.review_id))).where(ReviewTheme.theme_id.in_(new_ids))
        )
    promote_threshold = promote_min_reviews(classified_reviews)
    candidates = await list_candidates(db, entity_id)
    return TriggerStatus(
        classified_reviews=classified_reviews,
        new_candidates=len(new_ids),
        new_candidate_reviews=new_candidate_reviews,
        consolidate_threshold=max(CONSOLIDATE_MIN_NEW_CANDIDATES, round(CONSOLIDATE_MIN_NEW_SHARE * classified_reviews)),
        last_consolidation_run_id=last.id if last else None,
        promotable=[candidate.name for candidate in candidates if candidate.reviews >= promote_threshold],
        promote_threshold=promote_threshold,
    )


def describe_status(status: TriggerStatus) -> str:
    since = f"run {status.last_consolidation_run_id}" if status.last_consolidation_run_id else "the start"
    lines = [
        f"{status.new_candidates} new candidates since {since} (raised by {status.new_candidate_reviews} of "
        f"{status.classified_reviews} classified reviews); consolidation needs {status.consolidate_threshold}: "
        + ("TRIGGERED" if status.should_consolidate else "not triggered"),
        f"{len(status.promotable)} candidates pass the promotion threshold of {status.promote_threshold} reviews"
        + (f": {', '.join(status.promotable)}" if status.promotable else ""),
    ]
    return "\n".join(lines)


async def list_candidates(db: AsyncSession, entity_id: int) -> list[CandidateInfo]:
    """Unmerged candidates, most common first."""
    counts = await classify.candidate_review_counts(db, entity_id)
    themes = await db.scalars(
        select(Theme).where(
            Theme.entity_id == entity_id, Theme.kind == ThemeKind.CANDIDATE, Theme.merged_into_id.is_(None)
        )
    )
    candidates = [CandidateInfo(t.id, t.name, t.description, counts.get(t.id, 0)) for t in themes]
    candidates.sort(key=lambda candidate: (-candidate.reviews, candidate.name.lower()))
    return candidates


async def distinct_reviews(db: AsyncSession, root_ids: list[int]) -> int:
    """Distinct reviews that raised any of these candidates or anything merged into them."""
    family = select(Theme.id).where(Theme.id.in_(root_ids) | Theme.merged_into_id.in_(root_ids))
    return await db.scalar(
        select(func.count(func.distinct(ReviewTheme.review_id))).where(ReviewTheme.theme_id.in_(family))
    )


def build_prompt(
    entity: Entity,
    established: list[Theme],
    candidates: list[CandidateInfo],
    promote_reviews: int,
) -> str:
    subject = f"{entity.name} ({entity.description})" if entity.description else entity.name
    return PROMPT_TEMPLATE.format(
        subject=subject,
        established="\n".join(f"- {theme.name}: {theme.description}" for theme in established),
        candidates="\n".join(
            f"- id {candidate.id}, {candidate.name}: {candidate.reviews}" for candidate in candidates
        ),
        promote_reviews=promote_reviews,
    )


def validate_result(
    result: ConsolidationResult, candidates: list[CandidateInfo], established_names: set[str]
) -> None:
    """Check the answer against the actual candidates. Raises ValueError with a message to send back.

    A group named like a candidate that is not among its members takes that candidate in: the same
    name means the same theme.
    """
    by_id = {candidate.id: candidate for candidate in candidates}
    by_name = {candidate.name.lower(): candidate for candidate in candidates}
    unknown = [i for group in result.groups for i in group.member_ids if i not in by_id]
    unknown += [item.id for item in result.standalone if item.id not in by_id]
    if unknown:
        raise ValueError(f"These ids are not candidates in the list: {sorted(set(unknown))}.")
    grouped = {i for group in result.groups for i in group.member_ids}
    if any(item.id in grouped for item in result.standalone):
        raise ValueError("A candidate in a group must not also be in standalone.")
    for group in result.groups:
        name = group.name.strip().lower()
        if name in established_names:
            raise ValueError(f"The group name '{group.name}' is already used by an established theme.")
        same_name = by_name.get(name)
        if same_name and same_name.id not in group.member_ids:
            if same_name.id in grouped:
                raise ValueError(f"The group name '{group.name}' belongs to a candidate in another group.")
            group.member_ids.append(same_name.id)
            grouped.add(same_name.id)


async def request_consolidation(
    client: AsyncAnthropic,
    entity: Entity,
    established: list[Theme],
    candidates: list[CandidateInfo],
    promote_reviews: int,
    usage: discover.TokenUsage,
) -> ConsolidationResult:
    """Ask for groups, retrying once if the answer is invalid."""
    system_prompt = build_prompt(entity, established, candidates, promote_reviews)
    established_names = {t.name.lower() for t in established}
    last_error = None

    for _ in range(discover.MAX_ATTEMPTS):
        content = "Consolidate the candidates."
        if last_error:
            content += f"\n\nYour previous answer was rejected: {last_error}\nTry again."
        response = await client.messages.create(
            model=discover.MODEL,
            max_tokens=discover.MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {"type": "json_schema", "schema": transform_schema(ConsolidationResult)}
            },
        )
        usage.input_tokens += response.usage.input_tokens
        usage.output_tokens += response.usage.output_tokens
        try:
            if response.stop_reason != "end_turn":
                raise ValueError(f"No complete answer (stop reason: {response.stop_reason}).")
            answer = next(block.text for block in response.content if block.type == "text")
            result = ConsolidationResult.model_validate_json(answer)
            validate_result(result, candidates, established_names)
            return result
        except (ValidationError, ValueError, StopIteration) as error:
            last_error = str(error)
    raise RuntimeError(f"No valid consolidation after {discover.MAX_ATTEMPTS} attempts: {last_error}")


async def run_consolidation(
    db: AsyncSession, client: AsyncAnthropic, entity: Entity, *, force: bool = False
) -> ConsolidationOutcome:
    """Decide whether to consolidate and, if so, ask the model for groups (nothing is changed yet).

    On failure the session is rolled back before the failed run is recorded, so objects loaded
    earlier (such as the entity) are expired and need refreshing before reuse.
    """
    entity_id = entity.id
    status = await trigger_status(db, entity_id)
    outcome = ConsolidationOutcome(status)
    classified_reviews = status.classified_reviews
    if classified_reviews == 0:
        raise RuntimeError("No reviews are classified yet; classify them first.")
    if not force and not status.should_consolidate:
        outcome.skipped = (
            f"{status.new_candidates} new candidates since the last consolidation, under the threshold "
            f"of {status.consolidate_threshold}. Use --force to consolidate anyway."
        )
        return outcome

    run = AnalysisRun(
        entity_id=entity_id,
        type=RunType.DISCOVER,
        status=RunStatus.RUNNING,
        model=discover.MODEL,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.commit()

    usage = discover.TokenUsage()
    try:
        established = list(
            await db.scalars(
                select(Theme).where(
                    Theme.entity_id == entity_id,
                    Theme.kind == ThemeKind.DISCOVERED,
                    Theme.merged_into_id.is_(None),
                )
            )
        )
        candidates = [c for c in await list_candidates(db, entity_id) if c.reviews > 0]
        if len(candidates) < 2:
            raise RuntimeError("Fewer than two candidates have tags; nothing to consolidate.")
        candidates = candidates[:MAX_CANDIDATES_SHOWN]
        result = await request_consolidation(
            client, entity, established, candidates, promote_min_reviews(classified_reviews), usage
        )
    except Exception as error:
        await db.rollback()
        run.status = RunStatus.FAILED
        run.error = str(error)
        run.input_tokens, run.output_tokens = usage.input_tokens, usage.output_tokens
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise

    run.status = RunStatus.SUCCEEDED
    run.reviews_processed = len(candidates)
    run.input_tokens, run.output_tokens = usage.input_tokens, usage.output_tokens
    run.error = PROPOSED_NOTE
    run.completed_at = datetime.now(UTC)
    await db.commit()
    outcome.run, outcome.result, outcome.candidates = run, result, candidates
    return outcome


@dataclass
class ApplySummary:
    groups: int = 0
    merged: int = 0
    created: int = 0
    described: int = 0
    promoted: list[str] = field(default_factory=list)
    refined: list[str] = field(default_factory=list)  # promoted themes whose description was rewritten
    refine_failed: list[str] = field(default_factory=list)  # promoted themes that kept their old description
    not_promoted_without_description: list[str] = field(default_factory=list)


async def apply_consolidation(
    db: AsyncSession, run_id: int, result: ConsolidationResult
) -> ApplySummary:
    """Merge each group into one canonical candidate. Tags are untouched; counts follow merged_into_id."""
    summary = ApplySummary()
    for group in result.groups:
        members = list(
            await db.scalars(
                select(Theme).where(
                    Theme.id.in_(group.member_ids),
                    Theme.kind == ThemeKind.CANDIDATE,
                    Theme.merged_into_id.is_(None),
                )
            )
        )
        if len(members) != len(group.member_ids):
            raise RuntimeError(f"Group '{group.name}' has members that are no longer unmerged candidates.")
        entity_id = members[0].entity_id
        counts = await classify.candidate_review_counts(db, entity_id)
        same_name = [m for m in members if m.name.strip().lower() == group.name.strip().lower()]
        if same_name:
            canonical = max(same_name, key=lambda m: counts.get(m.id, 0))
            canonical.description = group.description.strip()
            summary.described += 1
        else:
            canonical = Theme(
                entity_id=entity_id,
                kind=ThemeKind.CANDIDATE,
                name=group.name.strip(),
                description=group.description.strip(),
                discovered_in_run_id=run_id,
            )
            db.add(canonical)
            await db.flush()
            summary.created += 1
        others = [m.id for m in members if m.id != canonical.id]
        await db.execute(
            update(Theme).where(Theme.id.in_(others)).values(merged_into_id=canonical.id, merged_in_run_id=run_id)
        )
        # Keep every mapping one level deep: anything that was merged into a merged member
        # now points at the canonical candidate.
        await db.execute(
            update(Theme).where(Theme.merged_into_id.in_(others)).values(merged_into_id=canonical.id)
        )
        summary.groups += 1
        summary.merged += len(others)
    for item in result.standalone:
        theme = await db.get(Theme, item.id)
        if theme and theme.kind == ThemeKind.CANDIDATE and theme.merged_into_id is None:
            theme.description = item.description.strip()
            summary.described += 1
    await db.execute(update(AnalysisRun).where(AnalysisRun.id == run_id).values(error=APPLIED_NOTE))
    await db.commit()
    return summary


async def promote_candidates(
    db: AsyncSession, entity_id: int, summary: ApplySummary, client: AsyncAnthropic | None = None
) -> None:
    """Make every candidate that enough reviews raise (merged ones included) a discovered theme.

    With a client, each promoted theme's description is then rewritten from review evidence and
    its neighbouring themes (describe_themes.refine_description). All promotions are saved first,
    so the new themes appear among each other's neighbours. A theme whose rewrite fails keeps its
    description.
    """
    _, classified_reviews = await candidate_share(db, entity_id)
    threshold = promote_min_reviews(classified_reviews)
    counts = await classify.candidate_review_counts(db, entity_id)
    promoted: list[tuple[int, str]] = []
    for candidate in await list_candidates(db, entity_id):
        if counts.get(candidate.id, 0) < threshold:
            continue
        if classify.is_placeholder(candidate.description):
            summary.not_promoted_without_description.append(candidate.name)
            continue
        await db.execute(update(Theme).where(Theme.id == candidate.id).values(kind=ThemeKind.DISCOVERED))
        summary.promoted.append(candidate.name)
        promoted.append((candidate.id, candidate.name))
    await db.commit()
    if client is None:
        return
    for theme_id, name in promoted:
        try:
            refined = await refine_description(db, client, entity_id, theme_id)
        except Exception:
            await db.rollback()
            refined = None
        (summary.refined if refined else summary.refine_failed).append(name)


async def describe_and_promote(
    db: AsyncSession, client: AsyncAnthropic, entity_id: int, summary: ApplySummary, promote: bool
) -> None:
    """Give the candidates real descriptions, then promote (promotion needs a real description)."""
    described = await describe_candidates(db, client, entity_id)
    summary.described += described.described
    if promote:
        await promote_candidates(db, entity_id, summary, client)


async def undo_consolidation(db: AsyncSession, run_id: int) -> int:
    """Clear the merges a consolidation run made. Exact when it is the latest consolidation run.

    Canonical candidates the run created are deleted if nothing is tagged with them or merged into
    them any more. Returns the number of merges undone. Promotions are not undone.
    """
    result = await db.execute(
        update(Theme).where(Theme.merged_in_run_id == run_id).values(merged_into_id=None, merged_in_run_id=None)
    )
    child = aliased(Theme)
    created = select(Theme.id).where(
        Theme.discovered_in_run_id == run_id,
        Theme.kind == ThemeKind.CANDIDATE,
        ~select(ReviewTheme.review_id).where(ReviewTheme.theme_id == Theme.id).exists(),
        ~select(child.id).where(child.merged_into_id == Theme.id).exists(),
    )
    await db.execute(delete(Theme).where(Theme.id.in_(created)))
    await db.commit()
    return result.rowcount


def build_report(entity: Entity, outcome: ConsolidationOutcome, previews: list[dict]) -> dict[str, Any]:
    return {
        "entity": {"id": entity.id, "name": entity.name},
        "run_id": outcome.run.id if outcome.run else None,
        "model": outcome.run.model if outcome.run else None,
        "prompt_version": PROMPT_VERSION,
        "classified_reviews": outcome.status.classified_reviews,
        "new_candidates": outcome.status.new_candidates,
        "consolidate_threshold": outcome.status.consolidate_threshold,
        "candidates_shown": len(outcome.candidates),
        "input_tokens": outcome.run.input_tokens if outcome.run else 0,
        "output_tokens": outcome.run.output_tokens if outcome.run else 0,
        "groups": [group.model_dump() for group in (outcome.result.groups if outcome.result else [])],
        "standalone": [item.model_dump() for item in (outcome.result.standalone if outcome.result else [])],
        "previews": previews,
    }


async def preview_groups(
    db: AsyncSession, outcome: ConsolidationOutcome, classified_reviews: int
) -> list[dict]:
    """For a dry run: reviews each group would cover and whether it would be promoted."""
    names = {candidate.id: candidate.name for candidate in outcome.candidates}
    threshold = promote_min_reviews(classified_reviews)
    previews = []
    for group in outcome.result.groups:
        reviews = await distinct_reviews(db, group.member_ids)
        previews.append(
            {
                "name": group.name,
                "reviews": reviews,
                "would_promote": reviews >= threshold,
                "members": [names[i] for i in group.member_ids],
            }
        )
    by_id = {candidate.id: candidate for candidate in outcome.candidates}
    for item in outcome.result.standalone:
        reviews = by_id[item.id].reviews
        previews.append(
            {"name": by_id[item.id].name, "reviews": reviews, "would_promote": reviews >= threshold, "members": []}
        )
    return sorted(previews, key=lambda preview: -preview["reviews"])


def print_summary(summary: ApplySummary) -> None:
    print(
        f"Merged {summary.merged} candidates into {summary.groups} groups "
        f"({summary.created} new canonical candidates, {summary.described} descriptions written)."
    )
    print(f"Promoted {len(summary.promoted)} to established themes: {', '.join(summary.promoted) or 'none'}.")
    if summary.promoted:
        print(f"Descriptions rewritten from reviews for {len(summary.refined)} of them.")
    if summary.refine_failed:
        print("Kept the old description for: " + ", ".join(summary.refine_failed))
    if summary.not_promoted_without_description:
        print(
            "Not promoted because the model gave no description: "
            + ", ".join(summary.not_promoted_without_description)
        )


async def main(entity_id: int, force: bool, dry_run: bool, promote: bool) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise SystemExit(f"No entity with id {entity_id}")
        outcome = await run_consolidation(db, client, entity, force=force)
        print(describe_status(outcome.status))
        if outcome.skipped:
            if outcome.status.should_promote:
                print("Promotion is separate: run with --promote-only to promote the ones listed above.")
            raise SystemExit(outcome.skipped)

        previews = await preview_groups(db, outcome, outcome.status.classified_reviews)
        report = build_report(entity, outcome, previews)
        print(
            f"\nRun {outcome.run.id}: {len(outcome.candidates)} candidates, "
            f"{outcome.run.input_tokens} input / {outcome.run.output_tokens} output tokens, "
            f"{len(outcome.result.groups)} groups, promotion needs "
            f"{promote_min_reviews(outcome.status.classified_reviews)} reviews.\n"
        )
        for preview in previews[:40]:
            flag = "PROMOTE" if preview["would_promote"] else "       "
            members = f"  <- {', '.join(preview['members'][:6])}" + (" ..." if len(preview["members"]) > 6 else "") if preview["members"] else ""
            print(f"{flag} {preview['reviews']:5}  {preview['name']}{members}")
        OUTPUT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"consolidation_entity{entity_id}_{timestamp}_run{outcome.run.id}.json"
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nSaved report to {output_path}")
        if dry_run:
            raise SystemExit("Dry run: nothing was merged or promoted.")

        summary = await apply_consolidation(db, outcome.run.id, outcome.result)
        await describe_and_promote(db, client, entity_id, summary, promote)
        print_summary(summary)
    await engine.dispose()


async def show_status(entity_id: int) -> None:
    async with async_session_maker() as db:
        if await db.get(Entity, entity_id) is None:
            raise SystemExit(f"No entity with id {entity_id}")
        print(describe_status(await trigger_status(db, entity_id)))
    await engine.dispose()


async def promote_only(entity_id: int) -> None:
    """Promote candidates that pass the threshold, without consolidating (no grouping call)."""
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        if await db.get(Entity, entity_id) is None:
            raise SystemExit(f"No entity with id {entity_id}")
        status = await trigger_status(db, entity_id)
        print(describe_status(status))
        if not status.should_promote:
            raise SystemExit("Nothing to promote.")
        summary = ApplySummary()
        await describe_and_promote(db, client, entity_id, summary, True)
        print_summary(summary)
    await engine.dispose()


async def apply_report(report_path: Path, promote: bool) -> None:
    """Apply the groups from a dry run's report without asking the model again."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entity_id, run_id = report["entity"]["id"], report["run_id"]
    result = ConsolidationResult.model_validate({"groups": report["groups"], "standalone": report["standalone"]})
    async with async_session_maker() as db:
        run = await db.get(AnalysisRun, run_id)
        if run is None or run.entity_id != entity_id or run.status != RunStatus.SUCCEEDED:
            raise SystemExit(f"Run {run_id} isn't a succeeded run for entity {entity_id}.")
        already = await db.scalar(
            select(func.count()).select_from(Theme).where(Theme.merged_in_run_id == run_id)
        )
        if already:
            raise SystemExit(f"Run {run_id} has already been applied.")
        candidates = await list_candidates(db, entity_id)
        established = await db.scalars(
            select(Theme.name).where(Theme.entity_id == entity_id, Theme.kind != ThemeKind.CANDIDATE)
        )
        validate_result(result, candidates, {name.lower() for name in established})
        summary = await apply_consolidation(db, run_id, result)
        await describe_and_promote(
            db, AsyncAnthropic(api_key=settings.anthropic_api_key), entity_id, summary, promote
        )
        print_summary(summary)
    await engine.dispose()


async def undo(run_id: int) -> None:
    async with async_session_maker() as db:
        undone = await undo_consolidation(db, run_id)
    await engine.dispose()
    print(f"Undid {undone} merges from run {run_id}.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Consolidate and promote an entity's candidate themes.")
    parser.add_argument("entity_id", type=int, nargs="?")
    parser.add_argument("--force", action="store_true", help="consolidate even if the trigger is not met")
    parser.add_argument("--status", action="store_true", help="show what the triggers say and change nothing")
    parser.add_argument("--promote-only", action="store_true", help="promote candidates that pass the threshold, without consolidating")
    parser.add_argument("--dry-run", action="store_true", help="propose groups but change nothing")
    parser.add_argument("--no-promote", action="store_true", help="merge but do not promote")
    parser.add_argument("--from-report", type=Path, help="apply the groups from an earlier --dry-run report")
    parser.add_argument("--undo", type=int, metavar="RUN_ID", help="undo the merges a consolidation run made")
    args = parser.parse_args()
    if args.undo:
        asyncio.run(undo(args.undo))
    elif args.from_report:
        asyncio.run(apply_report(args.from_report, not args.no_promote))
    else:
        if args.entity_id is None:
            parser.error("entity_id is required unless --from-report or --undo is given.")
        if args.status:
            asyncio.run(show_status(args.entity_id))
        elif args.promote_only:
            asyncio.run(promote_only(args.entity_id))
        else:
            asyncio.run(main(args.entity_id, args.force, args.dry_run, not args.no_promote))
