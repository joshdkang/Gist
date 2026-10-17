"""Theme discovery for one entity.

Samples the entity's reviews, asks the model for 5-15 recurring themes, validates the answer,
and records the run (tokens included) in analysis_runs. By default it only prints the themes and
saves a JSON report to src/themes/ so they can be checked by hand. With --save it also stores
them in the themes table. The functions take their database session and API client as
arguments so the job worker can reuse them.

Usage (from src/):
    python discover_themes.py <entity_id> [--runs 2] [--per-level 120] [--save]
"""
import argparse
import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session_maker, engine
from models import AnalysisRun, Entity, Review, RunStatus, RunType, Theme, ThemeKind

MODEL = "claude-sonnet-5-5"
PROMPT_VERSION = 6  # see docs/decisions.md
MIN_THEMES = 5  # loose limits: the reviews decide the count
MAX_THEMES = 15
# A theme must be raised by at least this share of the sampled reviews, but never by fewer than
# MIN_THEME_REVIEWS reviews, so a small sample doesn't make a handful of mentions look significant.
MIN_THEME_SHARE = 0.05
MIN_THEME_REVIEWS = 10
# Fewer reviews than this can't support MIN_THEMES themes at MIN_THEME_REVIEWS mentions each.
MIN_SAMPLE_SIZE = MIN_THEMES * MIN_THEME_REVIEWS
SAMPLE_PER_LEVEL = 120  # reviews per rating level, or total / 5 when there are no usable ratings
MAX_RATING_LEVELS = 10  # more distinct ratings than this is treated as unrated for sampling
MAX_REVIEW_CHARS = 1000
MAX_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 16000  # includes the model's adaptive thinking
OUTPUT_DIR = Path(__file__).parent / "themes"
RESERVED_THEME_NAMES = {"other", "general"}  # the system themes, created by code
# Names like "Other dishes" are residual buckets that also echo the system `other` theme.
RESIDUAL_NAME_PATTERN = re.compile(r"\b(other|others|misc|miscellaneous)\b", re.IGNORECASE)

# The rules every theme must follow. Rediscovery reuses them so new themes meet the same standard.
THEME_RULES = """A theme names a topic, never an opinion. Write "Checkout speed", not "Slow checkout".
Every theme must be able to hold both praise and complaints, so never create a pair such as
"Fast service" and "Slow service". Sentiment is assigned to reviews later, not to themes.

Make each theme specific enough to act on and broad enough to recur across many reviews.
"Customer support responsiveness" is right. "Support" is too broad, and "Great support" is an
opinion.

Give a single item, such as one dish or one product feature, its own theme only if it comes up
in a large share of reviews on its own. Otherwise, group related items into one coherent theme
("Main dishes" or "Desserts" for a restaurant, "Photo editing tools" for an app) rather than
listing items individually.

Each theme must be a single topic that fits in a short name. Do not join unrelated topics with
"and" to make one theme, and do not make leftover buckets such as "Other dishes" or
"Miscellaneous". If some items fit no coherent group, leave them out: reviews that fit no
theme are handled separately.

Themes must not overlap. Before answering, compare every pair of themes. If a typical review
could reasonably belong to both, combine them into one theme. Each description is one or two
sentences that define the theme's scope clearly enough for a reader to decide whether a given
review belongs to it.

Do not include catch-all themes such as "Other", "General", or "Overall experience". Reviews
that fit no theme are handled separately."""

SYSTEM_PROMPT_TEMPLATE = """You analyze customer reviews of {subject}.
Identify the themes that recur in a significant share of the reviews. A theme must be raised
by at least {min_reviews} of the {sample_size} reviews below ({min_share_percent}% of them).
Let the reviews decide how many themes there are, between 5 and 15. The goal is not the most
themes but the ones that matter, and fewer than 15 is the expected result. Together the themes
should cover the distinct aspects of the experience that customers discuss.

""" + THEME_RULES


def check_theme_names(names: list[str]) -> None:
    """Raise ValueError if names repeat, use a system theme's name, or look like a leftover bucket."""
    lowered = [name.strip().lower() for name in names]
    if len(set(lowered)) != len(lowered):
        raise ValueError("Theme names must be unique.")
    reserved_names = RESERVED_THEME_NAMES & set(lowered)
    if reserved_names:
        raise ValueError(f"Do not create catch-all themes: {', '.join(sorted(reserved_names))}.")
    residual_names = [name for name in names if RESIDUAL_NAME_PATTERN.search(name)]
    if residual_names:
        raise ValueError(
            f"Do not create leftover buckets (names with 'other' or 'miscellaneous'): "
            f"{', '.join(residual_names)}. Fold the items into a coherent theme or leave them out."
        )


class DiscoveredTheme(BaseModel):
    name: str = Field(description="Short topic name, 1-4 words, with no sentiment words")
    description: str = Field(
        description="One or two sentences defining which reviews belong, whether positive or negative"
    )


class DiscoveryResult(BaseModel):
    themes: list[DiscoveredTheme] = Field(min_length=MIN_THEMES, max_length=MAX_THEMES)

    @model_validator(mode="after")
    def check_names(self):
        check_theme_names([theme.name for theme in self.themes])
        return self


@dataclass
class SampledReview:
    id: int
    text: str
    rating: Decimal | None


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


def min_theme_reviews(sample_size: int) -> int:
    """How many sampled reviews must raise a topic before it counts as a theme."""
    return max(MIN_THEME_REVIEWS, round(MIN_THEME_SHARE * sample_size))


def build_system_prompt(
    entity_name: str, entity_description: str | None, sample_size: int
) -> str:
    subject = f"{entity_name} ({entity_description})" if entity_description else entity_name
    min_reviews = min_theme_reviews(sample_size)
    return SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject,
        min_reviews=min_reviews,
        sample_size=sample_size,
        min_share_percent=round(100 * min_reviews / sample_size),
    )


async def fetch_random_reviews(
    db: AsyncSession, entity_id: int, limit: int, *conditions
) -> list[SampledReview]:
    result = await db.execute(
        select(Review.id, Review.text, Review.rating)
        .where(Review.entity_id == entity_id, *conditions)
        .order_by(func.random())
        .limit(limit)
    )
    return [SampledReview(id=id_, text=text, rating=rating) for id_, text, rating in result.all()]


async def fetch_sample(
    db: AsyncSession, entity_id: int, per_level: int = SAMPLE_PER_LEVEL
) -> list[SampledReview]:
    """Equal random sample per rating level when ratings are usable, otherwise a plain random sample.

    Ratings are usable when there are 2 to MAX_RATING_LEVELS distinct values and at least half of
    the reviews have one. Stratifying keeps complaints from being drowned out by praise. The result
    is shuffled so the order doesn't hint at sentiment.
    """
    total_reviews = await db.scalar(
        select(func.count()).select_from(Review).where(Review.entity_id == entity_id)
    )
    rating_counts = (
        await db.execute(
            select(Review.rating, func.count())
            .where(Review.entity_id == entity_id, Review.rating.is_not(None))
            .group_by(Review.rating)
            .order_by(Review.rating)
        )
    ).all()
    rated_reviews = sum(count for _, count in rating_counts)
    ratings_usable = 2 <= len(rating_counts) <= MAX_RATING_LEVELS and rated_reviews * 2 >= total_reviews

    if ratings_usable:
        sample = []
        for rating, _ in rating_counts:
            sample += await fetch_random_reviews(db, entity_id, per_level, Review.rating == rating)
    else:
        sample = await fetch_random_reviews(db, entity_id, per_level * 5)
    random.shuffle(sample)
    return sample


async def request_themes(
    client: AsyncAnthropic, entity: Entity, sample: list[SampledReview], usage: TokenUsage
) -> DiscoveryResult:
    """Ask the model for themes as structured output, retrying once if the answer fails validation.

    Sonnet 5.5 rejects forced tool use, so the answer is constrained with `output_config.format`
    instead. `transform_schema` strips what the API's schema subset can't express (the 5-15 length
    limit), and Pydantic enforces that and the name rules after the fact.
    """
    numbered_reviews = "\n\n".join(
        f"[{number}] {review.text[:MAX_REVIEW_CHARS]}" for number, review in enumerate(sample, start=1)
    )
    user_message = f"Reviews of {entity.name}:\n\n{numbered_reviews}"
    last_error = None

    for _ in range(MAX_ATTEMPTS):
        content = user_message
        if last_error:
            content += f"\n\nYour previous answer was rejected: {last_error}\nTry again."
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=build_system_prompt(entity.name, entity.description, len(sample)),
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {"type": "json_schema", "schema": transform_schema(DiscoveryResult)}
            },
        )
        usage.input_tokens += response.usage.input_tokens
        usage.output_tokens += response.usage.output_tokens
        try:
            if response.stop_reason != "end_turn":
                raise ValueError(f"No complete answer (stop reason: {response.stop_reason}).")
            answer = next(block.text for block in response.content if block.type == "text")
            return DiscoveryResult.model_validate_json(answer)
        except (ValidationError, ValueError, StopIteration) as error:
            last_error = str(error)
    raise RuntimeError(f"No valid themes after {MAX_ATTEMPTS} attempts: {last_error}")


async def run_discovery(
    db: AsyncSession, client: AsyncAnthropic, entity: Entity, per_level: int = SAMPLE_PER_LEVEL
) -> tuple[AnalysisRun, DiscoveryResult, list[SampledReview]]:
    """Sample, ask the model, and record the run (status, tokens, errors) in analysis_runs.

    On failure the session is rolled back before the failed run is recorded, so objects loaded
    earlier (such as the entity) are expired and need refreshing before reuse.
    """
    run = AnalysisRun(
        entity_id=entity.id,
        type=RunType.DISCOVER,
        status=RunStatus.RUNNING,
        model=MODEL,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.commit()

    usage = TokenUsage()
    try:
        sample = await fetch_sample(db, entity.id, per_level)
        if len(sample) < MIN_SAMPLE_SIZE:
            raise RuntimeError(
                f"Too few reviews to find themes: {len(sample)} sampled, at least "
                f"{MIN_SAMPLE_SIZE} needed ({MIN_THEMES} themes x {MIN_THEME_REVIEWS} reviews each)."
            )
        result = await request_themes(client, entity, sample, usage)
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
    return run, result, sample


async def has_discovered_themes(db: AsyncSession, entity_id: int) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(Theme)
        .where(Theme.entity_id == entity_id, Theme.kind == ThemeKind.DISCOVERED)
    )
    return count > 0


async def save_discovered_themes(
    db: AsyncSession, entity_id: int, run_id: int, result: DiscoveryResult
) -> None:
    await db.execute(
        insert(Theme),
        [
            {
                "entity_id": entity_id,
                "kind": ThemeKind.DISCOVERED,
                "name": theme.name.strip(),
                "description": theme.description.strip(),
                "discovered_in_run_id": run_id,
            }
            for theme in result.themes
        ],
    )
    await db.commit()


def build_report(
    entity: Entity, run: AnalysisRun, result: DiscoveryResult, sample: list[SampledReview]
) -> dict:
    """A JSON-friendly summary of the run, for checking the themes by hand.

    Example quotes are not part of discovery: the model's citations were unreliable, so examples
    come from classification results instead.
    """
    themes = [{"name": theme.name, "description": theme.description} for theme in result.themes]
    return {
        "entity": {"id": entity.id, "name": entity.name, "description": entity.description},
        "run_id": run.id,
        "model": run.model,
        "prompt_version": PROMPT_VERSION,
        "sample_size": len(sample),
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "themes": themes,
    }


def print_report(report: dict) -> None:
    print(
        f"\nRun {report['run_id']}: {report['sample_size']} reviews, "
        f"{report['input_tokens']} input / {report['output_tokens']} output tokens"
    )
    for theme in report["themes"]:
        print(f"\n{theme['name']}\n  {theme['description']}")


async def main(entity_id: int, runs: int, per_level: int, save: bool) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise SystemExit(f"No entity with id {entity_id}")
        if save and await has_discovered_themes(db, entity_id):
            raise SystemExit("This entity already has discovered themes; rediscovery isn't built yet.")

        OUTPUT_DIR.mkdir(exist_ok=True)
        for run_number in range(1, runs + 1):
            print(f"\nDiscovery run {run_number} of {runs} for {entity.name}...")
            run, result, sample = await run_discovery(db, client, entity, per_level)
            report = build_report(entity, run, result, sample)
            print_report(report)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = OUTPUT_DIR / f"entity{entity_id}_{timestamp}_run{run.id}.json"
            output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"\nSaved report to {output_path}")

            if save:
                await save_discovered_themes(db, entity_id, run.id, result)
                print(f"Saved {len(result.themes)} themes to the database.")
    await engine.dispose()


async def save_report(report_path: Path) -> None:
    """Store the themes from an earlier run's JSON report, linked to that run."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entity_id, run_id = report["entity"]["id"], report["run_id"]
    result = DiscoveryResult.model_validate({"themes": report["themes"]})
    async with async_session_maker() as db:
        run = await db.get(AnalysisRun, run_id)
        if (
            run is None
            or run.entity_id != entity_id
            or run.type != RunType.DISCOVER
            or run.status != RunStatus.SUCCEEDED
        ):
            raise SystemExit(f"Run {run_id} isn't a succeeded discovery run for entity {entity_id}.")
        if await has_discovered_themes(db, entity_id):
            raise SystemExit("This entity already has discovered themes; rediscovery isn't built yet.")
        await save_discovered_themes(db, entity_id, run_id, result)
    await engine.dispose()
    print(f"Saved {len(result.themes)} themes for entity {entity_id} from run {run_id}.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Discover themes for one entity.")
    parser.add_argument("entity_id", type=int, nargs="?")
    parser.add_argument("--runs", type=int, default=1, help="independent runs, for the stability check")
    parser.add_argument("--per-level", type=int, default=SAMPLE_PER_LEVEL)
    parser.add_argument("--save", action="store_true", help="store the themes in the database")
    parser.add_argument(
        "--save-report", type=Path, help="store the themes from an earlier run's JSON report"
    )
    args = parser.parse_args()
    if args.save_report:
        asyncio.run(save_report(args.save_report))
    else:
        if args.entity_id is None:
            parser.error("entity_id is required unless --save-report is given.")
        if args.save and args.runs > 1:
            parser.error("--save can't be combined with --runs > 1; compare the runs first, then save one.")
        asyncio.run(main(args.entity_id, args.runs, args.per_level, args.save))
