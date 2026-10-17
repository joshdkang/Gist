"""Rediscovery: find new themes in the reviews the current themes don't cover.

Themes persist and accumulate, so trend lines continue across rediscoveries. Nothing is renamed,
merged, or retired. The flow:

1. Check the share of classified reviews tagged `other`. Rediscovery runs when it passes
   OTHER_SHARE_THRESHOLD, or when forced.
2. Sample reviews, mostly ones tagged `other` (each shown with the title the classifier gave its
   uncovered point), give the model the existing themes, and ask only for new themes.
3. Save the new themes, then reclassify the reviews tagged `other` against the full theme set.
   A review keeps every tag it has. It gains tags for the new themes, and loses its `other` tag
   only if the classifier no longer needs it and the review has another tag left.

The functions take their session and API client as arguments so the job worker can reuse them.

Usage (from src/):
    python rediscover_themes.py <entity_id> [--force] [--dry-run] [--no-reclassify]
"""
import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

import classify_reviews as classify
import discover_themes as discover
from config import settings
from database import async_session_maker, engine
from models import (
    AnalysisRun,
    Entity,
    Review,
    ReviewTheme,
    RunStatus,
    RunType,
    Sentiment,
    Theme,
    ThemeKind,
)

PROMPT_VERSION = 1  # see docs/decisions.md
OTHER_SHARE_THRESHOLD = 0.15  # rediscover when this share of classified reviews is tagged `other`
SAMPLE_SIZE = 600
OTHER_SAMPLE_FRACTION = 0.8  # the rest are reviews the existing themes cover, shown for context
# A new theme must be raised by at least this share of the sampled `other` reviews, and never by
# fewer than discover.MIN_THEME_REVIEWS. The share is of the `other` reviews, not of all reviews,
# because the existing themes already cover most of the rest.
MIN_NEW_THEME_SHARE = 0.05
MIN_OTHER_REVIEWS = discover.MIN_THEME_REVIEWS  # fewer `other` reviews can't support even one theme
MAX_NEW_THEMES = 10
OUTPUT_DIR = Path(__file__).parent / "themes"

SYSTEM_PROMPT_TEMPLATE = (
    """You are extending the theme list for customer reviews of {subject}.

These themes already exist. Do not repeat, rename, or split them:
{existing_themes}

Most of the reviews below were tagged "Other": each makes a specific point that none of the
existing themes covers, and the label after its number says what the point is. A few reviews
have no label. They are included only so you can see what the existing themes already cover.

Find new themes: topics that recur across the labelled reviews and that the existing themes do
not cover. A new theme must be raised by at least {min_reviews} of the {other_count} labelled
reviews ({min_share_percent}% of them). Return only new themes, at most {max_themes}. Returning
few, or none, is the right answer when no further topic recurs, so do not pad the list.

The labels are specific (for example "Brisket" or "Short ribs"). Group labels that belong
together into one coherent theme and name the group, rather than creating one theme per label.

"""
    + discover.THEME_RULES
    + """

A new theme must also not overlap an existing theme. If a typical review could reasonably belong
to both, leave the new theme out."""
)


class RediscoveryResult(BaseModel):
    """New themes only. An empty list is a valid answer."""
    themes: list[discover.DiscoveredTheme] = Field(max_length=MAX_NEW_THEMES)

    @model_validator(mode="after")
    def check_names(self):
        discover.check_theme_names([theme.name for theme in self.themes])
        return self


@dataclass
class RediscoveryReview:
    id: int
    text: str
    is_other: bool
    point: str | None  # the title the classifier gave the uncovered point


@dataclass
class RediscoveryOutcome:
    other_count: int
    tagged_count: int
    skipped: str | None = None
    run: AnalysisRun | None = None
    result: RediscoveryResult | None = None
    sample: list[RediscoveryReview] = field(default_factory=list)

    @property
    def other_share(self) -> float:
        return self.other_count / self.tagged_count if self.tagged_count else 0.0


@dataclass
class ReviewPlan:
    add: list[tuple[int, Sentiment | None, str | None]]
    remove_other: bool
    update_other: tuple[Sentiment | None, str | None] | None


@dataclass
class ReclassifySummary:
    run: AnalysisRun
    reviews: int = 0
    skipped_after_failure: int = 0
    other_kept: int = 0
    other_removed: int = 0
    new_tags: Counter = field(default_factory=Counter)  # theme name -> tags added


async def other_share(db: AsyncSession, entity_id: int, other_theme_id: int) -> tuple[int, int]:
    """(reviews tagged `other`, reviews with any tag) for the entity."""
    other_count = await db.scalar(
        select(func.count())
        .select_from(ReviewTheme)
        .join(Review, Review.id == ReviewTheme.review_id)
        .where(Review.entity_id == entity_id, ReviewTheme.theme_id == other_theme_id)
    )
    tagged_count = await db.scalar(
        select(func.count(func.distinct(ReviewTheme.review_id)))
        .join(Review, Review.id == ReviewTheme.review_id)
        .where(Review.entity_id == entity_id)
    )
    return other_count, tagged_count


async def fetch_rediscovery_sample(
    db: AsyncSession, entity_id: int, other_theme_id: int, size: int = SAMPLE_SIZE
) -> list[RediscoveryReview]:
    """Mostly reviews tagged `other` (with their point titles), plus some covered reviews."""
    other_target = round(size * OTHER_SAMPLE_FRACTION)
    other_rows = (
        await db.execute(
            select(Review.id, Review.text, ReviewTheme.theme_title)
            .join(ReviewTheme, ReviewTheme.review_id == Review.id)
            .where(Review.entity_id == entity_id, ReviewTheme.theme_id == other_theme_id)
            .order_by(func.random())
            .limit(other_target)
        )
    ).all()
    is_tagged_other = (
        select(ReviewTheme.review_id)
        .where(ReviewTheme.review_id == Review.id, ReviewTheme.theme_id == other_theme_id)
        .exists()
    )
    context_rows = (
        await db.execute(
            select(Review.id, Review.text)
            .where(Review.entity_id == entity_id, ~is_tagged_other)
            .order_by(func.random())
            .limit(size - other_target)
        )
    ).all()
    sample = [RediscoveryReview(id_, text, True, title) for id_, text, title in other_rows]
    sample += [RediscoveryReview(id_, text, False, None) for id_, text in context_rows]
    random.shuffle(sample)
    return sample


def min_new_theme_reviews(other_in_sample: int) -> int:
    return max(discover.MIN_THEME_REVIEWS, round(MIN_NEW_THEME_SHARE * other_in_sample))


def build_system_prompt(
    entity: Entity, themes: classify.ThemeSet, other_in_sample: int
) -> str:
    subject = f"{entity.name} ({entity.description})" if entity.description else entity.name
    min_reviews = min_new_theme_reviews(other_in_sample)
    return SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject,
        existing_themes="\n".join(f"- {theme.name}: {theme.description}" for theme in themes.discovered),
        min_reviews=min_reviews,
        other_count=other_in_sample,
        min_share_percent=round(100 * min_reviews / other_in_sample),
        max_themes=MAX_NEW_THEMES,
    )


def format_sample(sample: list[RediscoveryReview]) -> str:
    lines = []
    for number, review in enumerate(sample, start=1):
        if review.is_other:
            label = f" (not covered: {review.point})" if review.point else " (not covered)"
        else:
            label = ""
        lines.append(f"[{number}]{label} {review.text[: classify.MAX_REVIEW_CHARS]}")
    return "\n\n".join(lines)


async def request_new_themes(
    client: AsyncAnthropic,
    entity: Entity,
    themes: classify.ThemeSet,
    sample: list[RediscoveryReview],
    usage: discover.TokenUsage,
) -> RediscoveryResult:
    """Ask for new themes only, retrying once if the answer is invalid or repeats an existing name."""
    other_in_sample = sum(review.is_other for review in sample)
    system_prompt = build_system_prompt(entity, themes, other_in_sample)
    user_message = f"Reviews of {entity.name}:\n\n{format_sample(sample)}"
    existing_names = {theme.name.strip().lower() for theme in themes.discovered}
    last_error = None

    for _ in range(discover.MAX_ATTEMPTS):
        content = user_message
        if last_error:
            content += f"\n\nYour previous answer was rejected: {last_error}\nTry again."
        response = await client.messages.create(
            model=discover.MODEL,
            max_tokens=discover.MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {"type": "json_schema", "schema": transform_schema(RediscoveryResult)}
            },
        )
        usage.input_tokens += response.usage.input_tokens
        usage.output_tokens += response.usage.output_tokens
        try:
            if response.stop_reason != "end_turn":
                raise ValueError(f"No complete answer (stop reason: {response.stop_reason}).")
            answer = next(block.text for block in response.content if block.type == "text")
            result = RediscoveryResult.model_validate_json(answer)
            repeated = [t.name for t in result.themes if t.name.strip().lower() in existing_names]
            if repeated:
                raise ValueError(f"These themes already exist: {', '.join(repeated)}.")
            return result
        except (ValidationError, ValueError, StopIteration) as error:
            last_error = str(error)
    raise RuntimeError(f"No valid new themes after {discover.MAX_ATTEMPTS} attempts: {last_error}")


async def run_rediscovery(
    db: AsyncSession, client: AsyncAnthropic, entity: Entity, *, force: bool = False
) -> RediscoveryOutcome:
    """Decide whether to rediscover, and if so ask the model for new themes (nothing is saved).

    On failure the session is rolled back before the failed run is recorded, so objects loaded
    earlier (such as the entity) are expired and need refreshing before reuse.
    """
    entity_id = entity.id
    themes = await classify.load_theme_set(db, entity_id)
    other_count, tagged_count = await other_share(db, entity_id, themes.other.id)
    outcome = RediscoveryOutcome(other_count=other_count, tagged_count=tagged_count)
    if tagged_count == 0:
        raise RuntimeError("No reviews are classified yet; classify them first.")
    if not force and outcome.other_share < OTHER_SHARE_THRESHOLD:
        outcome.skipped = (
            f"`other` is {outcome.other_share:.1%} of classified reviews, under the "
            f"{OTHER_SHARE_THRESHOLD:.0%} threshold. Use --force to rediscover anyway."
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
        sample = await fetch_rediscovery_sample(db, entity_id, themes.other.id)
        if sum(review.is_other for review in sample) < MIN_OTHER_REVIEWS:
            raise RuntimeError(
                f"Too few `other` reviews to find a theme: {other_count} "
                f"(at least {MIN_OTHER_REVIEWS} needed)."
            )
        result = await request_new_themes(client, entity, themes, sample, usage)
    except Exception as error:
        await db.rollback()
        run.status = RunStatus.FAILED
        run.error = str(error)
        run.input_tokens = usage.input_tokens
        run.output_tokens = usage.output_tokens
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise

    run.status = RunStatus.SUCCEEDED
    run.reviews_processed = len(sample)
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.completed_at = datetime.now(UTC)
    await db.commit()
    outcome.run, outcome.result, outcome.sample = run, result, sample
    return outcome


async def save_new_themes(
    db: AsyncSession, entity_id: int, run_id: int, result: RediscoveryResult
) -> set[int]:
    """Insert the new themes, linked to the run that found them. Returns their IDs."""
    await discover.save_discovered_themes(db, entity_id, run_id, result)
    ids = await db.scalars(select(Theme.id).where(Theme.discovered_in_run_id == run_id))
    return set(ids)


def plan_review_changes(
    existing_theme_ids: set[int],
    model_tags: list[tuple[int, Sentiment | None, str | None]],
    themes: classify.ThemeSet,
    new_theme_ids: set[int],
) -> ReviewPlan:
    """What to change for one reviewed `other`-tagged review.

    Existing tags are never touched except `other`. The review gains tags for new themes the
    classifier assigns, and loses `other` only if the classifier dropped it and another tag remains.
    """
    other_id = themes.other.id
    by_theme = {theme_id: (sentiment, title) for theme_id, sentiment, title in model_tags}
    add = [
        (theme_id, sentiment, title)
        for theme_id, (sentiment, title) in by_theme.items()
        if theme_id in new_theme_ids and theme_id not in existing_theme_ids
    ]
    remaining = (existing_theme_ids - {other_id}) | {theme_id for theme_id, _, _ in add}
    if other_id in by_theme or not remaining:
        return ReviewPlan(add=add, remove_other=False, update_other=by_theme.get(other_id))
    return ReviewPlan(add=add, remove_other=True, update_other=None)


async def reclassify_other_reviews(
    db: AsyncSession,
    client: AsyncAnthropic,
    entity: Entity,
    new_theme_ids: set[int],
    *,
    model: str = classify.MODEL,
) -> ReclassifySummary:
    """Reclassify the entity's `other`-tagged reviews against the full theme set, keeping tags."""
    entity_id = entity.id
    themes = await classify.load_theme_set(db, entity_id)
    names = {theme.id: theme.name for theme in themes.all_themes}
    system_prompt = classify.build_system_prompt(entity, themes)
    schema = classify.build_output_schema(themes)

    run = AnalysisRun(
        entity_id=entity_id,
        type=RunType.CLASSIFY,
        status=RunStatus.RUNNING,
        model=model,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.commit()
    run_id = run.id
    summary = ReclassifySummary(run=run)
    usage = classify.TokenUsage()

    try:
        reviews = (
            await db.execute(
                select(Review.id, Review.text)
                .join(ReviewTheme, ReviewTheme.review_id == Review.id)
                .where(Review.entity_id == entity_id, ReviewTheme.theme_id == themes.other.id)
                .order_by(Review.id)
            )
        ).all()
        existing: dict[int, set[int]] = {review_id: set() for review_id, _ in reviews}
        for review_id, theme_id in (
            await db.execute(
                select(ReviewTheme.review_id, ReviewTheme.theme_id).where(
                    ReviewTheme.review_id.in_(list(existing))
                )
            )
        ).all():
            existing[review_id].add(theme_id)

        semaphore = asyncio.Semaphore(classify.CONCURRENCY)

        async def classify_one_chunk(chunk: list[tuple[int, str]]):
            numbered = list(enumerate(chunk, start=1))
            async with semaphore:
                results, fallbacks = await classify.classify_chunk(
                    client, model, system_prompt, schema, themes,
                    [(number, text) for number, (_, text) in numbered], usage,
                )
            return [(review_id, results[number], number in fallbacks) for number, (review_id, _) in numbered]

        chunks = [reviews[i : i + classify.CHUNK_SIZE] for i in range(0, len(reviews), classify.CHUNK_SIZE)]
        tasks = [asyncio.create_task(classify_one_chunk(list(chunk))) for chunk in chunks]
        try:
            for finished in asyncio.as_completed(tasks):
                for review_id, model_tags, failed in await finished:
                    if failed:  # the classifier gave no valid answer, so leave the review alone
                        summary.skipped_after_failure += 1
                        continue
                    plan = plan_review_changes(existing[review_id], model_tags, themes, new_theme_ids)
                    summary.reviews += 1
                    if plan.add:
                        await db.execute(
                            postgres_insert(ReviewTheme).on_conflict_do_nothing(),
                            [
                                {"review_id": review_id, "theme_id": theme_id, "sentiment": sentiment,
                                 "theme_title": None, "run_id": run_id}
                                for theme_id, sentiment, _ in plan.add
                            ],
                        )
                        summary.new_tags.update(names[theme_id] for theme_id, _, _ in plan.add)
                    other_row = (ReviewTheme.review_id == review_id) & (ReviewTheme.theme_id == themes.other.id)
                    if plan.remove_other:
                        await db.execute(delete(ReviewTheme).where(other_row))
                        summary.other_removed += 1
                    else:
                        summary.other_kept += 1
                        if plan.update_other:
                            sentiment, title = plan.update_other
                            await db.execute(
                                update(ReviewTheme)
                                .where(other_row)
                                .values(sentiment=sentiment, theme_title=title, run_id=run_id)
                            )
                await db.commit()  # one chunk at a time, so an interrupted run keeps its progress
        except BaseException:
            for task in tasks:
                task.cancel()
            raise
    except Exception as error:
        await db.rollback()
        run.status = RunStatus.FAILED
        run.error = str(error)
        run.reviews_processed = summary.reviews
        run.input_tokens, run.output_tokens = usage.input_tokens, usage.output_tokens
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise

    run.status = RunStatus.SUCCEEDED
    run.reviews_processed = summary.reviews
    run.input_tokens, run.output_tokens = usage.input_tokens, usage.output_tokens
    run.error = (
        f"Left {summary.skipped_after_failure} reviews unchanged after classification failed."
        if summary.skipped_after_failure
        else None
    )
    run.completed_at = datetime.now(UTC)
    await db.commit()
    return summary


def build_report(entity: Entity, outcome: RediscoveryOutcome) -> dict[str, Any]:
    return {
        "entity": {"id": entity.id, "name": entity.name},
        "run_id": outcome.run.id if outcome.run else None,
        "model": outcome.run.model if outcome.run else None,
        "prompt_version": PROMPT_VERSION,
        "other_reviews": outcome.other_count,
        "classified_reviews": outcome.tagged_count,
        "other_share_percent": round(100 * outcome.other_share, 1),
        "sample_size": len(outcome.sample),
        "input_tokens": outcome.run.input_tokens if outcome.run else 0,
        "output_tokens": outcome.run.output_tokens if outcome.run else 0,
        "new_themes": [
            {"name": theme.name, "description": theme.description}
            for theme in (outcome.result.themes if outcome.result else [])
        ],
    }


async def main(
    entity_id: int, force: bool, dry_run: bool, reclassify: bool, classify_model: str
) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise SystemExit(f"No entity with id {entity_id}")
        outcome = await run_rediscovery(db, client, entity, force=force)
        print(
            f"`other` tags: {outcome.other_count} of {outcome.tagged_count} classified reviews "
            f"({outcome.other_share:.1%}); threshold {OTHER_SHARE_THRESHOLD:.0%}."
        )
        if outcome.skipped:
            raise SystemExit(outcome.skipped)

        report = build_report(entity, outcome)
        print(
            f"\nRun {outcome.run.id}: {len(outcome.sample)} reviews, "
            f"{outcome.run.input_tokens} input / {outcome.run.output_tokens} output tokens"
        )
        for theme in outcome.result.themes:
            print(f"\nNew theme: {theme.name}\n  {theme.description}")
        OUTPUT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"rediscovery_entity{entity_id}_{timestamp}_run{outcome.run.id}.json"
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nSaved report to {output_path}")

        if not outcome.result.themes:
            raise SystemExit("The model found no new themes; nothing to save.")
        if dry_run:
            raise SystemExit("Dry run: the new themes were not saved.")

        await apply_new_themes(
            db, client, entity_id, outcome.run.id, outcome.result, reclassify, classify_model
        )
    await engine.dispose()


async def apply_new_themes(
    db: AsyncSession,
    client: AsyncAnthropic,
    entity_id: int,
    run_id: int,
    result: RediscoveryResult,
    reclassify: bool,
    classify_model: str,
) -> None:
    """Save the new themes, then reclassify the `other` reviews against them and print the effect."""
    entity = await db.get(Entity, entity_id)
    new_theme_ids = await save_new_themes(db, entity_id, run_id, result)
    print(f"Saved {len(new_theme_ids)} new themes.")
    if not reclassify:
        raise SystemExit("Reviews were not reclassified (--no-reclassify).")

    summary = await reclassify_other_reviews(db, client, entity, new_theme_ids, model=classify_model)
    themes = await classify.load_theme_set(db, entity_id)
    other_after, tagged_after = await other_share(db, entity_id, themes.other.id)
    print(
        f"\nReclassified {summary.reviews} reviews (run {summary.run.id}, "
        f"{summary.run.input_tokens} input / {summary.run.output_tokens} output tokens); "
        f"{summary.skipped_after_failure} left unchanged after a failure."
    )
    print(f"`other` kept on {summary.other_kept} reviews, removed from {summary.other_removed}.")
    for name, count in summary.new_tags.most_common():
        print(f"  +{count:5}  {name}")
    print(f"`other` is now {other_after} of {tagged_after} ({other_after / tagged_after:.1%}).")


async def apply_report(
    report_path: Path, reclassify: bool, classify_model: str
) -> None:
    """Save the new themes from an earlier --dry-run report, then reclassify, without asking the model again."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entity_id, run_id = report["entity"]["id"], report["run_id"]
    result = RediscoveryResult.model_validate({"themes": report["new_themes"]})
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        run = await db.get(AnalysisRun, run_id)
        if (
            run is None
            or run.entity_id != entity_id
            or run.type != RunType.DISCOVER
            or run.status != RunStatus.SUCCEEDED
        ):
            raise SystemExit(f"Run {run_id} isn't a succeeded discovery run for entity {entity_id}.")
        already_saved = await db.scalar(
            select(func.count()).select_from(Theme).where(Theme.discovered_in_run_id == run_id)
        )
        if already_saved:
            raise SystemExit(f"Run {run_id}'s themes are already saved.")
        taken = {
            name.lower()
            for name in await db.scalars(select(Theme.name).where(Theme.entity_id == entity_id))
        }
        repeated = [theme.name for theme in result.themes if theme.name.lower() in taken]
        if repeated:
            raise SystemExit(f"These themes already exist: {', '.join(repeated)}.")
        await apply_new_themes(db, client, entity_id, run_id, result, reclassify, classify_model)
    await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Find new themes for an entity and reclassify.")
    parser.add_argument("entity_id", type=int, nargs="?")
    parser.add_argument("--force", action="store_true", help="rediscover even if `other` is under the threshold")
    parser.add_argument("--dry-run", action="store_true", help="propose themes but save nothing")
    parser.add_argument("--no-reclassify", action="store_true", help="save themes but skip reclassifying")
    parser.add_argument("--classify-model", default=classify.MODEL)
    parser.add_argument(
        "--from-report", type=Path, help="save the themes from an earlier --dry-run report and reclassify"
    )
    args = parser.parse_args()
    if args.from_report:
        asyncio.run(apply_report(args.from_report, not args.no_reclassify, args.classify_model))
    else:
        if args.entity_id is None:
            parser.error("entity_id is required unless --from-report is given.")
        asyncio.run(main(args.entity_id, args.force, args.dry_run, not args.no_reclassify, args.classify_model))
