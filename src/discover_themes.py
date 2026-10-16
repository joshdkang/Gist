import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from config import settings
from database import async_session_maker, engine
from models import Business, Feedback

MODEL = "claude-sonnet-5-5"
SAMPLE_PER_RATING = 120  # 5 ratings x 120 = up to 600 reviews
MAX_REVIEW_CHARS = 1000
OUTPUT_DIR = Path("themes")

SYSTEM_PROMPT = """You analyze customer reviews for a single restaurant.
Identify 8-12 distinct, recurring themes that are specific to this business:
particular dishes, service, wait times, price and value, atmosphere, cleanliness,
and so on. Avoid generic themes like "good experience" or "bad experience".
Themes must be distinguishable from each other, and each description should make
clear which reviews belong in it.
Assign each theme to exactly one category from the allowed list. Use "Other"
only when a theme genuinely fits none of the other categories.
Cite the numbers of a few reviews that illustrate each theme."""

Category = Literal[
    "Food Quality",
    "Service",
    "Wait Times",
    "Price & Value",
    "Atmosphere",
    "Cleanliness",
    "Location & Parking",
    "Drinks",
    "Other",
]


class Theme(BaseModel):
    name: str = Field(description="Short theme name, 2-4 words")
    category: Category
    description: str = Field(description="One sentence defining what belongs in this theme")
    example_review_numbers: list[int]


class ThemeList(BaseModel):
    themes: list[Theme]


async def fetch_sample(business_id: str) -> list[str]:
    """Take an equal random sample from each star rating so complaints aren't drowned out."""
    reviews = []
    async with async_session_maker() as db:
        for rating in range(1, 6):
            result = await db.execute(
                select(Feedback.text)
                .where(Feedback.business_id == business_id, Feedback.rating == rating)
                .order_by(func.random())
                .limit(SAMPLE_PER_RATING)
            )
            reviews.extend(review_text[:MAX_REVIEW_CHARS] for review_text in result.scalars())
    return reviews


async def discover_themes(business_name: str, reviews: list[str]) -> ThemeList:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    numbered_reviews = "\n\n".join(
        f"[{number}] {review_text}" for number, review_text in enumerate(reviews, start=1)
    )
    response = await client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": "report_themes",
                "description": "Report the recurring themes found in the reviews.",
                "input_schema": ThemeList.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": "report_themes"},
        messages=[
            {"role": "user", "content": f"Reviews for {business_name}:\n\n{numbered_reviews}"}
        ],
    )
    tool_call = next(block for block in response.content if block.type == "tool_use")
    print(
        f"Tokens used: {response.usage.input_tokens} input, "
        f"{response.usage.output_tokens} output"
    )
    return ThemeList.model_validate(tool_call.input)


async def main(business_id: str):
    async with async_session_maker() as db:
        business = await db.get(Business, business_id)
    if business is None:
        raise SystemExit(f"No business with id {business_id}")

    reviews = await fetch_sample(business_id)
    print(f"Analyzing {len(reviews)} sampled reviews for {business.name}...")

    theme_list = await discover_themes(business.name, reviews)
    for theme in theme_list.themes:
        print(
            f"\n{theme.name} [{theme.category}]\n"
            f"  {theme.description}\n"
            f"  examples: {theme.example_review_numbers}"
        )

    # Timestamped filenames so repeated runs (e.g. the stability check) don't overwrite each other
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"{business_id}_{timestamp}.json"
    output_path.write_text(theme_list.model_dump_json(indent=2))
    print(f"\nSaved to {output_path}")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python discover_themes.py <business_id>")
    asyncio.run(main(sys.argv[1]))