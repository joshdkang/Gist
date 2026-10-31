"""Classification: tag an entity's reviews with its themes, and a sentiment for each tag.

Every classified review gets at least one review_themes row, so "unprocessed" means "has no rows".
The model sees the entity's established (discovered) themes, then the emerging candidate themes
that enough reviews have raised, then the `general` theme. For each review it returns assignments
that name a listed theme by ID, or give a short title for a specific point no listed theme covers.
Code turns each new title into a candidate theme (reusing a candidate with the same normalized
title), so a review can carry several uncovered points. Reviews the model fails on after a retry,
and reviews with almost no text, are assigned to `general` by code.

Candidates are later merged by consolidation and promoted by consolidate_themes.py.

The functions take their session and API client as arguments so the job worker can reuse them.

Usage (from src/):
    python classify_reviews.py <entity_id> [--limit 100] [--seed A] [--model M] [--dry-run]

--limit takes a reproducible pseudo-random sample (change --seed for a different one), which is
how to compare prompts or models on identical reviews. --dry-run records the run but saves no
tags and creates no candidates.
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

import discover_themes as discover
from config import settings
from database import async_session_maker, engine
from models import (
    AnalysisRun,
    Entity,
    Review,
    ReviewTheme,
    Sentiment,
    StepKind,
    Theme,
    ThemeKind,
)
from tracking import begin_step

MODEL = "claude-haiku-5-5"  # the cheapest model; compare against Sonnet with --model
PROMPT_VERSION = 4  # see docs/decisions.md
CHUNK_SIZE = 20  # reviews per request
CONCURRENCY = 5  # requests in flight at once
MAX_REVIEW_CHARS = 1000
MAX_OUTPUT_TOKENS = 8000  # includes the model's adaptive thinking
MAX_ATTEMPTS = 2
MIN_LETTERS = 4  # reviews with fewer letters skip the model and go straight to `general`
MAX_TITLE_CHARS = 80
# A candidate is shown to the classifier only once at least this many reviews have raised it, so
# the prompt lists the recurring ones and not hundreds of one-off titles.
CANDIDATE_MIN_REVIEWS = 10
OUTPUT_DIR = Path(__file__).parent / "classifications"
# A new candidate starts with this description until describe_themes.py writes a real one.
PLACEHOLDER_DESCRIPTION = 'Candidate theme from an uncovered point titled "{title}".'
PLACEHOLDER_PREFIX = "Candidate theme from"

SYSTEM_PROMPT_TEMPLATE = """You classify customer reviews of {subject}.
For each review, decide which of the themes below it discusses, and how the reviewer feels
about each one.

Established themes:
{discovered_list}
{candidate_section}
Rules:
- Assign a review to every established theme whose topic it reasonably discusses, whatever its
  sentiment. Most reviews discuss one to three themes. Do not stretch a theme to cover unrelated
  feedback.
- Sentiment is the reviewer's feeling about that theme's topic only, not about the whole visit:
  positive, negative, mixed (both within that topic), or neutral (mentioned without a clear
  feeling).
- If a review makes a specific point that no established theme covers, add one more assignment
  for that point. If it matches an emerging theme from the list (when there is one), use that
  theme's id. Otherwise set theme_id to null and give new_theme_title: a short, opinion-free
  title for the point (for example "Short ribs" or "Parking", not "Great short ribs"). Use the
  reviewer's own words for the item or topic they name, and do not add words the review doesn't
  use, such as "entree", "dish", "special", or "item". Use the same wording each time the same
  point comes up. Add one assignment per distinct point.
- Use "General" (id {general_id}) only when a review has no discernible topic, such as "Great!"
  or "meh". Never combine it with another theme.
- Every review gets at least one assignment."""

CANDIDATE_SECTION_TEMPLATE = """
Emerging themes (specific points that other reviews raised and no established theme covers; use
one only when the review clearly raises that point):
{candidate_list}
"""


class ThemeAssignment(BaseModel):
    theme_id: int | None = Field(
        description="The id of a listed theme, or null for a point no listed theme covers."
    )
    new_theme_title: str | None = Field(
        description="Only when theme_id is null: a short, opinion-free title for the point. "
        "Otherwise null."
    )
    sentiment: Sentiment


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
    """The themes the classifier is shown."""
    discovered: list[Theme]
    candidates: list[Theme]  # unmerged candidates raised by enough reviews, most common first
    general: Theme

    @property
    def all_themes(self) -> list[Theme]:
        return [*self.discovered, *self.candidates, self.general]


@dataclass(frozen=True)
class Assignment:
    """A tag: an existing theme, or (theme_id None) a new candidate to create from new_title."""
    theme_id: int | None
    sentiment: Sentiment | None
    new_title: str | None = None


@dataclass
class ClassifiedReview:
    review_id: int
    text: str
    assignments: list[Assignment]
    fallback: str | None = None  # why code assigned `general`, if it did


@dataclass
class ClassificationOutcome:
    classified: list[ClassifiedReview] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    theme_info: dict[int, tuple[str, ThemeKind]] = field(default_factory=dict)  # id -> name, kind

    @property
    def fallbacks(self) -> list[ClassifiedReview]:
        return [review for review in self.classified if review.fallback]


def normalize_title(title: str) -> str:
    """A matching key: lower case, single spaces, and the last word without a plural 's'."""
    words = re.sub(r"[^\w\s/&'-]", "", title.lower()).split()
    if words:
        last = words[-1]
        if len(last) > 4 and last.endswith("ies"):
            last = last[:-3] + "y"
        elif len(last) > 4 and last.endswith(("ches", "shes", "xes", "zes", "sses")):
            last = last[:-2]
        elif len(last) > 3 and last.endswith("s") and not last.endswith("ss"):
            last = last[:-1]
        words[-1] = last
    return " ".join(words)


def is_placeholder(description: str) -> bool:
    return description.startswith(PLACEHOLDER_PREFIX)


def is_valid_title(title: str) -> bool:
    return (
        0 < len(title) <= MAX_TITLE_CHARS
        and not discover.RESIDUAL_NAME_PATTERN.search(title)
        and normalize_title(title) not in discover.RESERVED_THEME_NAMES
    )


async def candidate_review_counts(db: AsyncSession, entity_id: int) -> dict[int, int]:
    """Reviews that raised each candidate, counting a merged candidate toward the theme it merged into."""
    root_id = func.coalesce(Theme.merged_into_id, Theme.id)
    rows = await db.execute(
        select(root_id, func.count(func.distinct(ReviewTheme.review_id)))
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id, Theme.kind == ThemeKind.CANDIDATE)
        .group_by(root_id)
    )
    return {theme_id: count for theme_id, count in rows.all()}


async def load_theme_set(
    db: AsyncSession, entity_id: int, min_candidate_reviews: int = CANDIDATE_MIN_REVIEWS
) -> ThemeSet:
    themes = (
        await db.scalars(select(Theme).where(Theme.entity_id == entity_id).order_by(Theme.id))
    ).all()
    discovered = [t for t in themes if t.kind == ThemeKind.DISCOVERED and t.merged_into_id is None]
    general = next((t for t in themes if t.kind == ThemeKind.GENERAL), None)
    if not discovered:
        raise RuntimeError("This entity has no discovered themes yet; run discovery first.")
    if general is None:
        raise RuntimeError("This entity is missing its `general` theme.")
    counts = await candidate_review_counts(db, entity_id)
    candidates = [
        t for t in themes
        if t.kind == ThemeKind.CANDIDATE
        and t.merged_into_id is None
        and counts.get(t.id, 0) >= min_candidate_reviews
    ]
    candidates.sort(key=lambda t: -counts[t.id])
    return ThemeSet(discovered=discovered, candidates=candidates, general=general)


def build_system_prompt(entity: Entity, themes: ThemeSet) -> str:
    subject = f"{entity.name} ({entity.description})" if entity.description else entity.name
    discovered_list = "\n".join(
        f"- id {theme.id}, {theme.name}: {theme.description}" for theme in themes.discovered
    )
    candidate_section = ""
    if themes.candidates:
        candidate_list = "\n".join(f"- id {theme.id}, {theme.name}" for theme in themes.candidates)
        candidate_section = CANDIDATE_SECTION_TEMPLATE.format(candidate_list=candidate_list)
    return SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject,
        discovered_list=discovered_list,
        candidate_section=candidate_section,
        general_id=themes.general.id,
    )


def build_output_schema(themes: ThemeSet) -> dict[str, Any]:
    """The API schema for a chunk's answer, with theme IDs restricted to the listed themes."""
    schema = transform_schema(ChunkClassification)
    theme_id_options = schema["$defs"]["ThemeAssignment"]["properties"]["theme_id"]["anyOf"]
    integer_option = next(option for option in theme_id_options if option["type"] == "integer")
    integer_option["enum"] = [theme.id for theme in themes.all_themes]
    return schema


def has_enough_text(text: str) -> bool:
    return len(re.findall(r"[^\W\d_]", text)) >= MIN_LETTERS


def clean_assignments(
    assignments: list[ThemeAssignment], themes: ThemeSet
) -> list[Assignment] | None:
    """Valid, distinct assignments, with `general` dropped when real themes are present.

    Returns None if nothing valid is left.
    """
    allowed_ids = {theme.id for theme in themes.all_themes}
    seen_ids: set[int] = set()
    seen_titles: set[str] = set()
    cleaned: list[Assignment] = []
    for assignment in assignments:
        if assignment.theme_id is not None:
            if assignment.theme_id in allowed_ids and assignment.theme_id not in seen_ids:
                seen_ids.add(assignment.theme_id)
                cleaned.append(Assignment(assignment.theme_id, assignment.sentiment))
        elif assignment.new_theme_title:
            title = " ".join(assignment.new_theme_title.split())
            key = normalize_title(title)
            if is_valid_title(title) and key not in seen_titles:
                seen_titles.add(key)
                cleaned.append(Assignment(None, assignment.sentiment, title))
    if len(cleaned) > 1:
        cleaned = [item for item in cleaned if item.theme_id != themes.general.id]
    return cleaned or None


async def classify_chunk(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    schema: dict[str, Any],
    themes: ThemeSet,
    chunk: list[tuple[int, str]],
    usage: TokenUsage,
) -> tuple[dict[int, list[Assignment]], dict[int, str]]:
    """Classify numbered reviews. Returns (assignments by number, fallback reasons by number).

    Reviews that come back missing or invalid are retried once, then assigned to `general`
    with no sentiment.
    """
    texts = dict(chunk)
    results: dict[int, list[Assignment]] = {}
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
        results[number] = [Assignment(themes.general.id, None)]
    return results, fallbacks


class ThemeResolver:
    """Turns a new title into a theme ID, creating a candidate theme when nothing matches.

    A title matches any existing theme of the entity (any kind, merged ones through the theme they
    merged into) with the same normalized name. Creation happens one title at a time on the single
    writing session, and the unique index on (entity, lower(name)) backs it up.
    """

    def __init__(self, db: AsyncSession, entity_id: int, run_id: int):
        self.db, self.entity_id, self.run_id = db, entity_id, run_id
        self.index: dict[str, int] = {}

    async def load(self) -> None:
        rows = await self.db.execute(
            select(Theme.id, Theme.name, Theme.merged_into_id).where(Theme.entity_id == self.entity_id)
        )
        self.index = {normalize_title(name): merged_into_id or theme_id for theme_id, name, merged_into_id in rows.all()}

    async def resolve(self, title: str) -> int:
        key = normalize_title(title)
        if key in self.index:
            return self.index[key]
        theme_id = (
            await self.db.execute(
                postgres_insert(Theme)
                .values(
                    entity_id=self.entity_id,
                    kind=ThemeKind.CANDIDATE,
                    name=title,
                    description=PLACEHOLDER_DESCRIPTION.format(title=title),
                    discovered_in_run_id=self.run_id,
                )
                .on_conflict_do_nothing()
                .returning(Theme.id)
            )
        ).scalar_one_or_none()
        if theme_id is None:  # the same name in another spelling of the key already exists
            theme_id = await self.db.scalar(
                select(func.coalesce(Theme.merged_into_id, Theme.id)).where(
                    Theme.entity_id == self.entity_id, func.lower(Theme.name) == title.lower()
                )
            )
        self.index[key] = theme_id
        return theme_id


async def resolve_review(review: ClassifiedReview, resolver: ThemeResolver) -> ClassifiedReview:
    """Replace the review's new titles with theme IDs, creating candidates as needed."""
    resolved: dict[int, Assignment] = {}
    for assignment in review.assignments:
        theme_id = assignment.theme_id
        if theme_id is None:
            theme_id = await resolver.resolve(assignment.new_title)
        resolved.setdefault(theme_id, Assignment(theme_id, assignment.sentiment))
    return ClassifiedReview(review.review_id, review.text, list(resolved.values()), review.fallback)


async def fetch_unprocessed(
    db: AsyncSession, entity_id: int, limit: int | None, seed: str, include_processed: bool = False
) -> list[tuple[int, str]]:
    """Reviews with no review_themes rows. With a limit: a reproducible pseudo-random sample.

    include_processed also returns already tagged reviews, for dry-run trials of a new prompt.
    """
    already_tagged = (
        select(ReviewTheme.review_id).where(ReviewTheme.review_id == Review.id).exists()
    )
    query = select(Review.id, Review.text).where(Review.entity_id == entity_id)
    if not include_processed:
        query = query.where(~already_tagged)
    if limit:
        query = query.order_by(func.md5(cast(Review.id, String) + seed)).limit(limit)
    else:
        query = query.order_by(Review.id)
    return [(review_id, text) for review_id, text in (await db.execute(query)).all()]


async def save_classifications(
    db: AsyncSession, run_id: int, classified: list[ClassifiedReview]
) -> None:
    """Save resolved tags (every assignment must have a theme_id)."""
    rows = [
        {
            "review_id": review.review_id,
            "theme_id": assignment.theme_id,
            "sentiment": assignment.sentiment,
            "run_id": run_id,
        }
        for review in classified
        for assignment in review.assignments
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
    include_processed: bool = False,
    run: AnalysisRun | None = None,
) -> tuple[AnalysisRun, ClassificationOutcome, ThemeSet]:
    """Classify the entity's unprocessed reviews, saving each chunk as it finishes.

    Inside a worker job, pass the job as `run`. Without one, the step gets a run of its own.
    Because chunks are committed one by one, a failed or interrupted run keeps its progress and a
    re-run picks up where it stopped. On failure the session is rolled back before the failed step
    is recorded, so objects loaded earlier (such as the entity) are expired.
    """
    if include_processed and not dry_run:
        raise ValueError("include_processed is only for dry runs: it would tag reviews twice.")
    entity_id = entity.id
    themes = await load_theme_set(db, entity_id)
    system_prompt = build_system_prompt(entity, themes)
    schema = build_output_schema(themes)

    tracked = await begin_step(db, entity_id, StepKind.CLASSIFY, model, run)
    run_id = tracked.run.id
    general_id = themes.general.id

    outcome = ClassificationOutcome()
    resolver = ThemeResolver(db, entity_id, run_id)
    try:
        await resolver.load()
        reviews = await fetch_unprocessed(db, entity_id, limit, seed, include_processed)

        # Reviews with almost no text skip the model and go straight to `general`.
        model_reviews = []
        for review_id, text in reviews:
            if has_enough_text(text):
                model_reviews.append((review_id, text))
            else:
                outcome.classified.append(
                    ClassifiedReview(review_id, text, [Assignment(general_id, None)], "too little text")
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
                if not dry_run:
                    chunk_result = [await resolve_review(review, resolver) for review in chunk_result]
                    await save_classifications(db, run_id, chunk_result)
                    await tracked.progress(db, len(outcome.classified) + len(chunk_result))
                outcome.classified.extend(chunk_result)
        except BaseException:
            for task in tasks:
                task.cancel()
            raise
        if not dry_run:
            theme_ids = {a.theme_id for review in outcome.classified for a in review.assignments}
            rows = await db.execute(
                select(Theme.id, Theme.name, Theme.kind).where(Theme.id.in_(theme_ids))
            )
            outcome.theme_info = {theme_id: (name, kind) for theme_id, name, kind in rows.all()}
    except Exception as error:
        await tracked.fail(
            db,
            error,
            reviews_processed=len(outcome.classified),
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
        )
        raise

    warnings = []
    if outcome.fallbacks:
        counts = Counter(review.fallback for review in outcome.fallbacks)
        warnings.append("Assigned to `general` by code: " + ", ".join(f"{n} ({why})" for why, n in counts.items()))
    if not dry_run:
        # Give the candidates this run created real descriptions. A failure here is only a warning.
        from describe_themes import describe_candidates  # imported here: it imports this module

        try:
            described = await describe_candidates(db, client, entity_id)
            outcome.usage.input_tokens += described.input_tokens
            outcome.usage.output_tokens += described.output_tokens
            if described.missing:
                warnings.append(f"{described.missing} candidates still have a placeholder description")
        except Exception as error:
            await db.rollback()
            await db.refresh(tracked.run)
            await db.refresh(tracked.step)
            warnings.append(f"Candidate descriptions failed: {error}")
    await tracked.succeed(
        db,
        reviews_processed=len(outcome.classified),
        input_tokens=outcome.usage.input_tokens,
        output_tokens=outcome.usage.output_tokens,
        outcome="dry run" if dry_run else "saved",
        warnings="; ".join(warnings) or None,  # notes on a step that worked; a succeeded step has no error
    )
    return tracked.run, outcome, themes


def build_report(
    entity: Entity, run: AnalysisRun, outcome: ClassificationOutcome, themes: ThemeSet
) -> dict[str, Any]:
    """Counts per theme and sentiment plus example reviews, for checking the tags by hand.

    Tags that name a new candidate (a dry run, which creates nothing) are reported by title.
    """
    names: dict[int, str] = {}
    kinds: dict[int, str] = {}
    for theme in themes.all_themes:
        names[theme.id] = theme.name
        kinds[theme.id] = theme.kind.value
    for theme_id, (name, kind) in outcome.theme_info.items():
        names.setdefault(theme_id, name)
        kinds.setdefault(theme_id, kind.value)

    def label(assignment: Assignment) -> str:
        return names.get(assignment.theme_id, f"theme {assignment.theme_id}") if assignment.theme_id else f"(new) {assignment.new_title}"

    total = len(outcome.classified) or 1
    theme_counts: Counter[str] = Counter()
    sentiment_counts: dict[str, Counter] = {}
    for review in outcome.classified:
        for assignment in review.assignments:
            theme_counts[label(assignment)] += 1
            sentiment = assignment.sentiment.value if assignment.sentiment else None
            sentiment_counts.setdefault(label(assignment), Counter())[sentiment] += 1
    tag_count = sum(theme_counts.values())
    kind_by_label = {names[theme_id]: kind for theme_id, kind in kinds.items()}

    theme_rows = [
        {
            "name": name,
            "kind": kind_by_label.get(name, "candidate"),
            "reviews": count,
            "share_percent": round(100 * count / total, 1),
            "sentiment": dict(sentiment_counts[name]),
        }
        for name, count in theme_counts.most_common()
    ]
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
        "candidates_shown": [theme.name for theme in themes.candidates],
        "themes": theme_rows[:80],
        "examples": [
            {
                "review_id": review.review_id,
                "text": review.text[:300],
                "tags": [
                    {"theme": label(a), "sentiment": a.sentiment.value if a.sentiment else None}
                    for a in review.assignments
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
        f"{report['input_tokens']} input / {report['output_tokens']} output tokens, "
        f"{len(report['candidates_shown'])} candidates shown to the classifier\n"
    )
    for theme in report["themes"][:30]:
        sentiments = ", ".join(f"{name}: {count}" for name, count in theme["sentiment"].items())
        print(f"{theme['share_percent']:5}%  {theme['reviews']:5}  {theme['name']}  [{sentiments}]")
    if len(report["themes"]) > 30:
        print(f"... and {len(report['themes']) - 30} more in the report")


async def main(
    entity_id: int, model: str, limit: int | None, seed: str, dry_run: bool, include_processed: bool
) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise SystemExit(f"No entity with id {entity_id}")
        run, outcome, themes = await run_classification(
            db, client, entity, model=model, limit=limit, seed=seed, dry_run=dry_run,
            include_processed=include_processed,
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
    parser.add_argument("--dry-run", action="store_true", help="don't save tags or create candidates")
    parser.add_argument(
        "--include-processed", action="store_true", help="with --dry-run, also take already tagged reviews"
    )
    args = parser.parse_args()
    if args.include_processed and not args.dry_run:
        parser.error("--include-processed needs --dry-run.")
    asyncio.run(main(args.entity_id, args.model, args.limit, args.seed, args.dry_run, args.include_processed))
