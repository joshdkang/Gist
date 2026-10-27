"""Write descriptions for candidate themes that only have a placeholder.

The classifier creates a candidate from a short title, so its description starts as a placeholder.
This writes a real one for each, in the same style as the discovered themes' descriptions: one or
two sentences defining which reviews belong. The model sees the candidate's name and the names of
the candidates merged into it, and nothing else, so it can't invent items. It is cheap enough to run
over every candidate, merged ones included.

classify_reviews.py runs it for the candidates a classification run created, and consolidate_themes.py
runs it after merging and before promotion (a candidate needs a real description to be promoted).

When a candidate is promoted to an established theme, refine_description rewrites its description
from evidence instead: reviews that raised it and the neighbouring themes, so it states its scope
and where it stops. That is the description the classifier will use to decide what belongs.

Usage (from src/):
    python describe_themes.py <entity_id> [--model M]
    python describe_themes.py <entity_id> --refine NAME [NAME ...]   (rewrite established themes)
"""
import argparse
import asyncio
import re
import sys
from collections import defaultdict
from dataclasses import dataclass

from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import classify_reviews as classify
import discover_themes as discover
from config import settings
from database import async_session_maker, engine
from models import Entity, Review, ReviewTheme, Theme, ThemeKind

MODEL = classify.MODEL  # descriptions are short and well-defined, so the cheapest model will do
CHUNK_SIZE = 60  # candidates per request
CONCURRENCY = 5
MAX_MEMBERS_SHOWN = 12
MAX_DESCRIPTION_CHARS = 400
MAX_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 8000  # includes the model's adaptive thinking

PROMPT_TEMPLATE = """You write short descriptions for candidate themes in customer reviews of {subject}.

A candidate theme is a specific point that some reviews raised. Some candidates list members:
the spellings and related items that were merged into them.

For each candidate, write a description: one or two sentences defining which reviews belong to
it, whether they praise it or complain about it. Name the specific items from the candidate's name
and members, and do not add facts or items that are not there. Write them like these descriptions
of established themes:
{examples}"""


class CandidateDescription(BaseModel):
    id: int
    description: str


class DescriptionBatch(BaseModel):
    descriptions: list[CandidateDescription]


@dataclass
class DescribeSummary:
    described: int = 0
    missing: int = 0  # candidates still on a placeholder after the retry
    input_tokens: int = 0
    output_tokens: int = 0


def clean_description(description: str) -> str | None:
    """A usable description, or None."""
    cleaned = " ".join(description.split())
    if 0 < len(cleaned) <= MAX_DESCRIPTION_CHARS and not classify.is_placeholder(cleaned):
        return cleaned
    return None


def format_candidate(theme: Theme, members: list[str]) -> str:
    line = f"- id {theme.id}, {theme.name}"
    if members:
        shown = ", ".join(sorted(members)[:MAX_MEMBERS_SHOWN])
        more = len(members) - MAX_MEMBERS_SHOWN
        line += f"; members: {shown}" + (f" and {more} more" if more > 0 else "")
    return line


async def describe_chunk(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    chunk: list[tuple[Theme, list[str]]],
    summary: DescribeSummary,
) -> dict[int, str]:
    """Descriptions by candidate id. Candidates the model skips or answers badly are retried once."""
    by_id = {theme.id: members for theme, members in chunk}
    names = {theme.id: theme for theme, _ in chunk}
    results: dict[int, str] = {}
    pending = list(by_id)
    for _ in range(MAX_ATTEMPTS):
        listing = "\n".join(format_candidate(names[i], by_id[i]) for i in pending)
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Candidates:\n{listing}"}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": transform_schema(DescriptionBatch)},
            },
        )
        summary.input_tokens += response.usage.input_tokens
        summary.output_tokens += response.usage.output_tokens
        try:
            answer = next(block.text for block in response.content if block.type == "text")
            batch = DescriptionBatch.model_validate_json(answer)
        except (ValidationError, StopIteration):
            continue
        for item in batch.descriptions:
            description = clean_description(item.description)
            if item.id in pending and description:
                results[item.id] = description
                pending.remove(item.id)
        if not pending:
            break
    return results


async def describe_candidates(
    db: AsyncSession, client: AsyncAnthropic, entity_id: int, *, model: str = MODEL
) -> DescribeSummary:
    """Write a description for every candidate of the entity that still has a placeholder."""
    summary = DescribeSummary()
    entity = await db.get(Entity, entity_id)
    themes = (await db.scalars(select(Theme).where(Theme.entity_id == entity_id).order_by(Theme.id))).all()
    members: dict[int, list[str]] = defaultdict(list)
    for theme in themes:
        if theme.merged_into_id:
            members[theme.merged_into_id].append(theme.name)
    todo = [
        (theme, members[theme.id])
        for theme in themes
        if theme.kind == ThemeKind.CANDIDATE and classify.is_placeholder(theme.description)
    ]
    if not todo:
        return summary

    examples = [
        theme for theme in themes
        if theme.kind == ThemeKind.DISCOVERED and not classify.is_placeholder(theme.description)
    ][:3]
    subject = f"{entity.name} ({entity.description})" if entity.description else entity.name
    system_prompt = PROMPT_TEMPLATE.format(
        subject=subject,
        examples="\n".join(f"- {theme.name}: {theme.description}" for theme in examples) or "(none yet)",
    )

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def run_chunk(chunk):
        async with semaphore:
            return await describe_chunk(client, model, system_prompt, chunk, summary)

    chunks = [todo[i : i + CHUNK_SIZE] for i in range(0, len(todo), CHUNK_SIZE)]
    tasks = [asyncio.create_task(run_chunk(chunk)) for chunk in chunks]
    try:
        for finished in asyncio.as_completed(tasks):
            for theme_id, description in (await finished).items():
                await db.execute(update(Theme).where(Theme.id == theme_id).values(description=description))
                summary.described += 1
            await db.commit()
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    summary.missing = len(todo) - summary.described
    return summary


REFINE_MODEL = discover.MODEL  # runs once per promoted theme, so the stronger model is affordable
REFINE_SAMPLE_SIZE = 40  # reviews shown
SNIPPET_BEFORE = 200  # characters of a review shown before the first mention of the theme
SNIPPET_AFTER = 300
MAX_REFINED_CHARS = 500
KEYWORD_MIN_LENGTH = 4
IGNORED_KEYWORDS = {"with", "dish", "dishes", "special", "specials", "item", "items", "menu"}

REFINE_PROMPT_TEMPLATE = """You write the scope description for one theme in customer reviews of {subject}.

The theme is "{name}". Reviews raised it under these spellings and related items: {members}.

These are the other established themes (a review can belong to several themes):
{neighbours}

Below are reviews that raised the theme, each cut to the part around its mention.

Write the description: one or two sentences defining which reviews belong to this theme, whether
they praise it or complain about it. Base it on what the reviews actually discuss and name the
specific items that appear in them. Describe only what the theme covers: never say what it
excludes or refer to other themes, and leave out any item that belongs to one of the themes
above. Do not mention reviews, numbers, or counts."""


# A description says what a theme covers, never what it leaves out or how it differs from another.
EXCLUSION_PATTERN = re.compile(
    r"\b(exclud\w*|except\w*|other than|apart from|separate from|rather than|not including)\b|\bnon-\w+",
    re.IGNORECASE,
)


class RefinedDescription(BaseModel):
    description: str


def mentions_exclusion(description: str) -> str | None:
    """The offending phrase if the description talks about what the theme excludes, else None."""
    match = EXCLUSION_PATTERN.search(description)
    return match.group(0) if match else None


def theme_keywords(names: list[str]) -> list[str]:
    words = {
        word
        for name in names
        for word in "".join(ch if ch.isalnum() else " " for ch in name.lower()).split()
        if len(word) >= KEYWORD_MIN_LENGTH and word not in IGNORED_KEYWORDS
    }
    return sorted(words)


def snippet(text: str, keywords: list[str]) -> str:
    """The part of a review around the first mention of the theme, or its start if none is found."""
    lowered = text.lower()
    found = [position for position in (lowered.find(keyword) for keyword in keywords) if position != -1]
    start = max(0, min(found) - SNIPPET_BEFORE) if found else 0
    return text[start : start + SNIPPET_BEFORE + SNIPPET_AFTER].strip()


async def refine_description(
    db: AsyncSession,
    client: AsyncAnthropic,
    entity_id: int,
    theme_id: int,
    *,
    model: str = REFINE_MODEL,
    summary: DescribeSummary | None = None,
) -> str | None:
    """Rewrite a theme's description from review evidence and its neighbouring themes.

    Returns the new description after saving it, or None (leaving the old one) if the model
    gave nothing usable.
    """
    entity = await db.get(Entity, entity_id)
    theme = await db.get(Theme, theme_id)
    merged_members = (await db.scalars(select(Theme).where(Theme.merged_into_id == theme_id))).all()
    family_ids = [theme_id, *(member.id for member in merged_members)]
    neighbours = (
        await db.scalars(
            select(Theme)
            .where(
                Theme.entity_id == entity_id,
                Theme.kind == ThemeKind.DISCOVERED,
                Theme.id != theme_id,
                Theme.merged_into_id.is_(None),
            )
            .order_by(Theme.id)
        )
    ).all()
    tagged_reviews = select(ReviewTheme.review_id).where(ReviewTheme.theme_id.in_(family_ids)).distinct()
    texts = (
        await db.scalars(
            select(Review.text)
            .where(Review.id.in_(tagged_reviews))
            .order_by(func.random())
            .limit(REFINE_SAMPLE_SIZE)
        )
    ).all()
    if not texts:
        return None

    keywords = theme_keywords([theme.name, *(member.name for member in merged_members)])
    subject = f"{entity.name} ({entity.description})" if entity.description else entity.name
    members = ", ".join(sorted(member.name for member in merged_members)[:30]) or theme.name
    system_prompt = REFINE_PROMPT_TEMPLATE.format(
        subject=subject,
        name=theme.name,
        members=members,
        neighbours="\n".join(f"- {other.name}: {other.description}" for other in neighbours) or "(none)",
    )
    reviews = "\n\n".join(f"[{number}] {snippet(text, keywords)}" for number, text in enumerate(texts, 1))

    feedback = ""
    for _ in range(MAX_ATTEMPTS):
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Reviews:\n\n{reviews}{feedback}"}],
            output_config={
                "format": {"type": "json_schema", "schema": transform_schema(RefinedDescription)}
            },
        )
        if summary:
            summary.input_tokens += response.usage.input_tokens
            summary.output_tokens += response.usage.output_tokens
        try:
            answer = next(block.text for block in response.content if block.type == "text")
            description = " ".join(RefinedDescription.model_validate_json(answer).description.split())
        except (ValidationError, StopIteration):
            continue
        phrase = mentions_exclusion(description)
        if phrase:
            feedback = (
                f'\n\nYour previous answer used "{phrase}". Describe only what the theme covers, '
                "without saying what it excludes or how it differs from another theme."
            )
            continue
        if 0 < len(description) <= MAX_REFINED_CHARS and not classify.is_placeholder(description):
            await db.execute(update(Theme).where(Theme.id == theme_id).values(description=description))
            await db.commit()
            return description
    return None


async def main(entity_id: int, model: str, refine_names: list[str] | None) -> None:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with async_session_maker() as db:
        if await db.get(Entity, entity_id) is None:
            raise SystemExit(f"No entity with id {entity_id}")
        if refine_names:
            summary = DescribeSummary()
            for name in refine_names:
                theme = await db.scalar(
                    select(Theme).where(
                        Theme.entity_id == entity_id,
                        Theme.kind == ThemeKind.DISCOVERED,
                        func.lower(Theme.name) == name.lower(),
                    )
                )
                if theme is None:
                    print(f"No established theme named '{name}'.")
                    continue
                old_description = theme.description
                new_description = await refine_description(db, client, entity_id, theme.id, summary=summary)
                print(f"\n{theme.name}\n  before: {old_description}\n  after:  {new_description or '(unchanged)'}")
            print(f"\n{summary.input_tokens} input / {summary.output_tokens} output tokens.")
            await engine.dispose()
            return
        summary = await describe_candidates(db, client, entity_id, model=model)
    await engine.dispose()
    print(
        f"Described {summary.described} candidates ({summary.missing} still on a placeholder); "
        f"{summary.input_tokens} input / {summary.output_tokens} output tokens."
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Write descriptions for placeholder candidate themes.")
    parser.add_argument("entity_id", type=int)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--refine", nargs="+", metavar="NAME", help="rewrite these established themes' descriptions from reviews"
    )
    args = parser.parse_args()
    asyncio.run(main(args.entity_id, args.model, args.refine))
