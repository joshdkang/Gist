"""Load the top Yelp restaurants as public sample entities through the shared ingestion code.

Safe to re-run: an existing entity is reused (matched on metadata.yelp_business_id), and
reviews already stored for it are skipped as duplicates.
"""
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from database import async_session_maker, engine
from entities import create_entity
from ingestion import ColumnMapping, NumericScale, ingest_rows
from models import Entity

DATA_DIR = Path(__file__).parent
BUSINESS_FILE = DATA_DIR / "yelp_academic_dataset_business.json"
REVIEW_FILE = DATA_DIR / "yelp_academic_dataset_review.json"
TOP_N = 10

# Yelp review fields, named as the ingestion code expects them.
REVIEW_COLUMNS = ["review_id", "stars", "date", "text", "useful", "funny", "cool"]
REVIEW_MAPPING = ColumnMapping(
    text="text",
    rating="stars",
    review_date="date",
    external_id="review_id",
    metadata_columns=["useful", "funny", "cool"],
)
REVIEW_RATING_SCALE = NumericScale(min=1, max=5)


def pick_businesses() -> list[dict]:
    candidates = []
    with open(BUSINESS_FILE, encoding="utf-8") as f:
        for line in f:
            business = json.loads(line)
            if business.get("categories") and "Restaurants" in business["categories"]:
                candidates.append(business)

    candidates.sort(key=lambda business: business["review_count"], reverse=True)
    return candidates[:TOP_N]


def read_reviews(business_ids: set[str]) -> dict[str, list[dict]]:
    """One pass over the large review file, grouping the wanted reviews by business."""
    reviews_by_business = defaultdict(list)
    with open(REVIEW_FILE, encoding="utf-8") as f:
        for line in f:
            review = json.loads(line)
            if review["business_id"] in business_ids:
                reviews_by_business[review["business_id"]].append(
                    {column: review.get(column) for column in REVIEW_COLUMNS}
                )
    return reviews_by_business


def describe(business: dict) -> str:
    location = ", ".join(part for part in (business.get("city"), business.get("state")) if part)
    return f"A restaurant in {location}" if location else "A restaurant"


async def get_or_create_entity(business: dict) -> Entity:
    async with async_session_maker() as db:
        existing_entity = await db.scalar(
            select(Entity).where(
                Entity.metadata_["yelp_business_id"].astext == business["business_id"]
            )
        )
        if existing_entity is not None:
            return existing_entity
        return await create_entity(
            db,
            name=business["name"],
            description=describe(business),
            is_public=True,
            metadata={
                "yelp_business_id": business["business_id"],
                "city": business.get("city"),
                "state": business.get("state"),
                "categories": business.get("categories"),
                "stars": business.get("stars"),
                "review_count": business.get("review_count"),
            },
        )


async def main():
    businesses = pick_businesses()
    print(f"Selected {len(businesses)} businesses; reading reviews...")
    reviews_by_business = read_reviews({business["business_id"] for business in businesses})

    for business in businesses:
        entity = await get_or_create_entity(business)
        reviews = reviews_by_business[business["business_id"]]
        async with async_session_maker() as db:
            import_row = await ingest_rows(
                db,
                entity.id,
                reviews,
                REVIEW_MAPPING,
                REVIEW_RATING_SCALE,
                source="yelp",
                filename=REVIEW_FILE.name,
            )
        print(
            f"{entity.name} (entity {entity.id}): {import_row.rows_inserted} inserted, "
            f"{import_row.rows_skipped} skipped of {import_row.rows_received}"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
