"""Load five Android apps from the app reviews dataset as public sample entities, through the shared ingestion code.

    python load_app_reviews.py            (from src/)

The data is the sealuzh/app_reviews dataset on Hugging Face (288,065 Google Play reviews of 395
open-source apps, 2014 to 2017), saved as `app reviews.csv`: package_name, review, date ("October 12
2016"), star (1 to 5). The five apps were chosen from those with 1,000 to 9,999 reviews for long
reviews, a mix of ratings, and a long, even run of dates (docs/decisions.md). They are apps, not
restaurants or a product, so they test that the engine generalizes. NPR News has a burst of
complaints in August and September 2016 for the period comparison to find, and Reicast steady volume
with no spike, as a control.

Safe to re-run: an existing entity is reused (matched on metadata.package_name), and reviews already
stored for it are skipped as duplicates. Loading costs nothing; analysis is run separately by script.
"""
import asyncio
import csv
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from database import async_session_maker, engine
from entities import create_entity
from ingestion import ColumnMapping, NumericScale, ingest_rows
from models import Entity

REVIEW_FILE = Path(__file__).parent / "app reviews.csv"
SOURCE = "sealuzh/app_reviews"
# Package name -> name and description. The description goes into the prompts, so it says what the app is.
APPS = {
    "org.npr.android.news": ("NPR News", "An Android app for NPR news stories, podcasts and live public radio"),
    "com.reicast.emulator": ("Reicast", "A Sega Dreamcast emulator for Android"),
    "com.fsck.k9": ("K-9 Mail", "An open-source email app for Android"),
    "com.zegoggles.smssync": ("SMS Backup+", "An Android app that backs up text messages and call logs to Gmail"),
    "com.achep.acdisplay": ("AcDisplay", "An Android lock screen that shows notifications"),
}
REVIEW_MAPPING = ColumnMapping(text="review", rating="star", review_date="date", date_format="%B %d %Y")
REVIEW_RATING_SCALE = NumericScale(min=1, max=5)


def read_reviews() -> dict[str, list[dict]]:
    """The chosen apps' reviews, grouped by package name."""
    reviews_by_app = defaultdict(list)
    with open(REVIEW_FILE, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["package_name"] in APPS:
                reviews_by_app[row["package_name"]].append(row)
    return reviews_by_app


async def get_or_create_entity(package_name: str) -> Entity:
    name, description = APPS[package_name]
    async with async_session_maker() as db:
        existing_entity = await db.scalar(
            select(Entity).where(Entity.metadata_["package_name"].astext == package_name)
        )
        if existing_entity is not None:
            return existing_entity
        return await create_entity(
            db,
            name=name,
            description=description,
            is_public=True,
            metadata={"package_name": package_name, "source": SOURCE, "platform": "Android"},
        )


async def main():
    reviews_by_app = read_reviews()
    for package_name in APPS:
        entity = await get_or_create_entity(package_name)
        async with async_session_maker() as db:
            import_row = await ingest_rows(
                db,
                entity.id,
                reviews_by_app[package_name],
                REVIEW_MAPPING,
                REVIEW_RATING_SCALE,
                source=SOURCE,
                filename=REVIEW_FILE.name,
            )
        print(
            f"{entity.name} (entity {entity.id}): {import_row.rows_inserted} inserted, "
            f"{import_row.rows_skipped} skipped of {import_row.rows_received}"
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
