"""Classification: tag an entity's reviews with its themes, and a sentiment for each tag.

Every classified review gets at least one review_themes row, so "unprocessed" means "has no rows".
The model sees the entity's themes (discovered plus the `other` and `general` system themes) and
must return at least one theme per review, with theme IDs restricted to an enum. Reviews it fails
to classify validly after a retry, and reviews with almost no text, are assigned to `general`
by code. The functions take their session and API client as arguments so the job worker can reuse them.

Usage (from src/):
    python classify_reviews.py <entity_id> [--limit 100] [--seed A] [--model M] [--dry-run]

--limit takes a reproducible pseudo-random sample (change --seed for a different one), which is
how to compare prompts or models on identical reviews. --dry-run records the run but saves no tags.
"""
import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

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

MODEL = "claude-haiku-5-5"  # the cheapest model; compare against Sonnet with --model
PROMPT_VERSION = 3  # see docs/decisions.md
CHUNK_SIZE = 20  # reviews per request
CONCURRENCY = 5  # requests in flight at once
MAX_REVIEW_CHARS = 1000
MAX_OUTPUT_TOKENS = 8000  # includes the model's adaptive thinking
MAX_ATTEMPTS = 2
MIN_LETTERS = 4  # reviews with fewer letters skip the model and go straight to `general`
OUTPUT_DIR = Path(__file__).parent / "classifications"

SYSTEM_PROMPT_TEMPLATE = """You classify customer reviews of {subject}.
For each review, decide which of the themes below it discusses, and how the reviewer feels
about each one.

Themes:
{theme_list}

Rules:
- Assign a review to every theme whose topic it reasonably discusses, whatever its sentiment.
  Most reviews discuss one to three themes. Do not stretch a theme to cover unrelated feedback.
- Sentiment is the reviewer's feeling about that theme's topic only, not about the whole visit:
  positive, negative, mixed (both within that topic), or neutral (mentioned without a clear
  feeling).
- Use "Other" (id {other_id}) only for a specific point that no other theme's scope covers, and
  only for that point. It can accompany real themes. For "Other", also fill in `theme_title`
  with a short, opinion-free title for that point (for example "Short ribs" or "Parking", not
  "Great short ribs"). Use the reviewer's own words for the item or topic they name, and do not
  add words the review doesn't use, such as "entree", "dish", "special", or "item". Leave
  `theme_title` null for every other theme.
- Use "General" (id {general_id}) only when a review has no discernible topic, such as "Great!"
  or "meh". Never combine it with another theme.
- Every review gets at least one theme."""


class ThemeAssignment(BaseModel):
    theme_id: int
    sentiment: Sentiment
    theme_title: str | None = Field(
        description='Only for the "Other" theme: a short, opinion-free title for the point, in the '
        "reviewer's own words. Otherwise null."
    )


class ReviewClassification(BaseModel):
    review_number: int = Field(description="The number shown in brackets before the review")
    assignments: list[ThemeAssignment] = Field(min_length=1)


class ChunkClassification(BaseModel):
    reviews: list[ReviewClassification]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ThemeSet:
    """The entity's themes, split by role."""
    discovered: list[Theme]
    other: Theme
    general: Theme

    @property
    def all_themes(self) -> list[Theme]:
        return [*self.discovered, self.other, self.general]


@dataclass
class ClassifiedReview:
    review_id: int
    text: str
    assignments: list[tuple[int, Sentiment | None, str | None]]  # theme id, sentiment, theme title
    fallback: str | None = None  # why code assigned `general`, if it did


@dataclass
class ClassificationOutcome:
    classified: list[ClassifiedReview] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def fallbacks(self) -> list[ClassifiedReview]:
        return [review for review in self.classified if review.fallback]


async def load_theme_set(db: AsyncSession, entity_id: int) -> ThemeSet:
    themes = (
        await db.scalars(select(Theme).where(Theme.entity_id == entity_id).order_by(Theme.id))
    ).all()
    discovered = [theme for theme in themes if theme.kind == ThemeKind.DISCOVERED]
    other = next((theme for theme in themes if theme.kind == ThemeKind.OTHER), None)
    general = next((theme for theme in themes if theme.kind == ThemeKind.GENERAL), None)
    if not discovered:
        raise RuntimeError("This entity has no discovered themes yet; run discovery first.")
    if other is None or general is None:
        raise RuntimeError("This entity is missing its `other` or `general` theme.")
    return ThemeSet(discovered=discovered, other=other, general=general)


def build_system_prompt(entity: Entity, themes: ThemeSet) -> str:
    subject = f"{entity.name} ({entity.description})" if entity.description else entity.name
    theme_list = "\n".join(
        f"- id {theme.id}, {theme.name}: {theme.description}" for theme in themes.all_themes
    )
    return SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject, theme_list=theme_list, other_id=themes.other.id, general_id=themes.general.id
    )


def build_output_schema(themes: ThemeSet) -> dict[str, Any]:
    """The API schema for a chunk's answer, with theme IDs restricted to this entity's themes."""
    schema = transform_schema(ChunkClassification)
    schema["$defs"]["ThemeAssignment"]["properties"]["theme_id"]["enum"] = [
        theme.id for theme in themes.all_themes
    ]
    return schema


def has_enough_text(text: str) -> bool:
    return len(re.findall(r"[^\W\d_]", text)) >= MIN_LETTERS


def clean_assignments(
    assignments: list[ThemeAssignment], themes: ThemeSet
) -> list[tuple[int, Sentiment | None, str | None]] | None:
    """One entry per valid theme, with `general` dropped when real themes are present.

    Returns None if no valid theme is left.
    """
    allowed_ids = {theme.id for theme in themes.all_themes}
    cleaned: dict[int, tuple[Sentiment, str | None]] = {}
    for assignment in assignments:
        if assignment.theme_id in allowed_ids and assignment.theme_id not in cleaned:
            theme_title = (assignment.theme_title or "").strip() or None
            cleaned[assignment.theme_id] = (
                assignment.sentiment,
                theme_title if assignment.theme_id == themes.other.id else None,
            )
    if len(cleaned) > 1:
        cleaned.pop(themes.general.id, None)
    return [
        (theme_id, sentiment, theme_title) for theme_id, (sentiment, theme_title) in cleaned.items()
    ] or None


async def classify_chunk(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    schema: dict[str, Any],
    themes: ThemeSet,
    chunk: list[tuple[int, str]],
    usage: TokenUsage,
) -> tuple[dict[int, list[tuple[int, Sentiment | None, str | None]]], dict[int, str]]:
    """Classify numbered reviews. Returns (assignments by number, fallback reasons by number).

    Reviews that come back missing or invalid are retried once, then assigned to `general`
    with no sentiment.
    """
    texts = dict(chunk)
    results: dict[int, list[tuple[int, Sentiment | None, str | None]]] = {}
    pending = list(texts)
    reason = "no valid answer"

    for _ in range(MAX_ATTEMPTS):
        numbered_reviews = "\n\n".join(
            f"[{number}] {texts[number][:MAX_REVIEW_CHARS]}" for number in pending
        )
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Reviews:\n\n{numbered_reviews}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        usage.input_tokens += response.usage.input_tokens
        usage.output_tokens += response.usage.output_tokens
        try:
            answer = next(block.text for block in response.content if block.type == "text")
            parsed = ChunkClassification.model_validate_json(answer)
        except (ValidationError, StopIteration) as error:
            reason = f"unparseable answer ({type(error).__name__})"
            continue
        for item in parsed.reviews:
            if item.review_number in pending:
                assignments = clean_assignments(item.assignments, themes)
                if assignments:
                    results[item.review_number] = assignments
                    pending.remove(item.review_number)
        if not pending:
            break
        reason = "missing or invalid in the model's answer"

    fallbacks = {number: reason for number in pending}
    for number in pending:
        results[number] = [(themes.general.id, None, None)]
    return results, fallbacks


async def fetch_unprocessed(
    db: AsyncSession, entity_id: int, limit: int | None, seed: str
) -> list[tuple[int, str]]:
    """Reviews with no review_themes rows. With a limit: a reproducible pseudo-random sample."""
    already_tagged = (
        select(ReviewTheme.review_id).where(ReviewTheme.review_id == Review.id).exists()
    )
    query = select(Review.id, Review.text).where(Review.entity_id == entity_id, ~already_tagged)
    if limit:
        query = query.order_by(func.md5(cast(Review.id, String) + seed)).limit(limit)
    else:
        query = query.order_by(Review.id)
    return [(review_id, text) for review_id, text in (await db.execute(query)).all()]


async def save_classifications(
    db: AsyncSession, run_id: int, classified: list[ClassifiedReview]
) -> None:
    rows = [
        {
            "review_id": review.review_id,
            "theme_id": theme_id,
            "sentiment": sentiment,
            "theme_title": theme_title,
            "run_id": run_id,
        }
        for review in classified
        for theme_id, sentiment, theme_title in review.assignments
    ]
    if rows:
        await db.execute(postgres_insert(ReviewTheme).on_conflict_do_nothing(), rows)
    await db.commit()


async def run_classification(
    db: AsyncSession,
    client: AsyncAnthropic,
    entity: Entity,
    *,
    model: str = MODEL,
    limit: int | None = None,
    seed: str = "0",
    dry_run: bool = False,
) -> tuple[AnalysisRun, ClassificationOutcome, ThemeSet]:
    """Classify the entity's unprocessed reviews, saving each chunk as it finishes.

    Because chunks are committed one by one, a failed or interrupted run keeps its progress and a
    re-run picks up where it stopped. On failure the session is rolled back before the failed run
    is recorded, so objects loaded earlier (such as the entity) are expired.
    """
    entity_id = entity.id
    themes = await load_theme_set(db, entity_id)
    system_prompt = build_system_prompt(entity, themes)
    schema = build_output_schema(themes)

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

    outcome = ClassificationOutcome()
    try:
        reviews = await fetch_unprocessed(db, entity_id, limit, seed)

        # Reviews with almost no text skip the model and go straight to `general`.
        model_reviews = []
        for review_id, text in reviews:
            if has_enough_text(text):
                model_reviews.append((review_id, text))
            else:
                outcome.classified.append(
                    ClassifiedReview(review_id, text, [(themes.general.id, None, None)], "too little text")
                )
        if outcome.classified and not dry_run:
            await save_classifications(db, run_id, outcome.classified)

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def classify_one_chunk(chunk_reviews: list[tuple[int, str]]):
            numbered = list(enumerate(chunk_reviews, start=1))
            async with semaphore:
                results, fallbacks = await classify_chunk(
                    client, model, system_prompt, schema, themes,
                    [(number, text) for number, (_, text) in numbered], outcome.usage,
                )
            return [
                ClassifiedReview(review_id, text, results[number], fallbacks.get(number))
                for number, (review_id, text) in numbered
            ]

        chunks = [model_reviews[i : i + CHUNK_SIZE] for i in range(0, len(model_reviews), CHUNK_SIZE)]
        tasks = [asyncio.create_task(classify_one_chunk(chunk)) for chunk in chunks]
        try:
            for finished in asyncio.as_completed(tasks):
                chunk_result = await finished
                outcome.classified.extend(chunk_result)
                if not dry_run:
                    await save_classifications(db, run_id, chunk_result)
        except BaseException:
            for task in tasks:
                task.cancel()
            raise
    except Exception as error:
        await db.rollback()
        run.status = RunStatus.FAILED
        run.error = str(error)
        run.reviews_processed = len(outcome.classified)
        run.input_tokens = outcome.usage.input_tokens
        run.output_tokens = outcome.usage.output_tokens
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise

    warnings = []
    if outcome.fallbacks:
        counts = Counter(review.fallback for review in outcome.fallbacks)
        warnings.append("Assigned to `general` by code: " + ", ".join(f"{n} ({why})" for why, n in counts.items()))
    if dry_run:
        warnings.append("Dry run: tags were not saved.")
    run.status = RunStatus.SUCCEEDED
    run.reviews_processed = len(outcome.classified)
    run.input_tokens = outcome.usage.input_tokens
    run.output_tokens = outcome.usage.output_tokens
    run.error = "; ".join(warnings) or None  # warnings only; a succeeded run has no error
    run.completed_at = datetime.now(UTC)
    await db.commit()
    return run, outcome, themes


def build_report(
    entity: Entity, run: AnalysisRun, outcome: ClassificationOutcome, themes: ThemeSet
) -> dict[str, Any]:
    """Counts per theme and sentiment plus example reviews, for checking the tags by hand."""
    names = {theme.id: theme.name for theme in themes.all_themes}
    total = len(outcome.classified) or 1
    theme_counts: Counter[int] = Counter()
    sentiment_counts: dict[int, Counter] = {}
    for review in outcome.classified:
        for theme_id, sentiment, _ in review.assignments:
            theme_counts[theme_id] += 1
            sentiment_counts.setdefault(theme_id, Counter())[sentiment.value if sentiment else None] += 1
    tag_count = sum(theme_counts.values())
    return {
        "entity": {"id": entity.id, "name": entity.name},
        "run_id": run.id,
        "model": run.model,
        "prompt_version": PROMPT_VERSION,
        "reviews": len(outcome.classified),
        "tags_per_review": round(tag_count / total, 2),
        "fallbacks": len(outcome.fallbacks),
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "themes": [
            {
                "theme_id": theme.id,
                "name": theme.name,
                "kind": theme.kind.value,
                "reviews": theme_counts[theme.id],
                "share_percent": round(100 * theme_counts[theme.id] / total, 1),
                "sentiment": dict(sentiment_counts.get(theme.id, {})),
            }
            for theme in themes.all_themes
        ],
        "examples": [
            {
                "review_id": review.review_id,
                "text": review.text[:300],
                "tags": [
                    {
                        "theme": names[theme_id],
                        "sentiment": sentiment.value if sentiment else None,
                        **({"theme_title": theme_title} if theme_title else {}),
                    }
                    for theme_id, sentiment, theme_title in review.assignments
                ],
                "fallback": review.fallback,
            }
            for review in outcome.classified[:200]
        ],
    }


def print_report(report: dict[str, Any]) -> None:
    print(
        f"\nRun {report['run_id']} ({report['model']}): {report['reviews']} reviews, "
        f"{report['tags_per_review']} tags per review, {report['fallbacks']} fallbacks, "
        f"{report['input_tokens']} input / {report['output_tokens']} output tokens\n"
    )
    for theme in sorted(report["themes"], key=lambda item: -item["reviews"]):
        sentiments = ", ".join(f"{name}: {count}" for name, count in theme["sentiment"].items())
        print(f"{theme['share_percent']:5}%  {theme['reviews']:5}  {theme['name']}  [{sentiments}]")


async def main(
    entity_id: int, model: str, limit: int | None, seed: str, dry_run: bool
) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise SystemExit(f"No entity with id {entity_id}")
        run, outcome, themes = await run_classification(
            db, client, entity, model=model, limit=limit, seed=seed, dry_run=dry_run
        )
        report = build_report(entity, run, outcome, themes)
    print_report(report)

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"entity{entity_id}_{timestamp}_run{run.id}.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved report to {output_path}")
    await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Classify an entity's unprocessed reviews.")
    parser.add_argument("entity_id", type=int)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--limit", type=int, help="classify only a reproducible sample of this size")
    parser.add_argument("--seed", default="0", help="changes which sample --limit takes")
    parser.add_argument("--dry-run", action="store_true", help="don't save tags")
    args = parser.parse_args()
    asyncio.run(main(args.entity_id, args.model, args.limit, args.seed, args.dry_run))
