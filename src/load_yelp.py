import asyncio
import json
from datetime import datetime

from sqlalchemy import insert

from database import engine, async_session_maker, Base
from models import Business, Feedback

BUSINESS_FILE = "src/yelp_academic_dataset_business.json"
REVIEW_FILE = "src/yelp_academic_dataset_review.json"
TOP_N = 10
BATCH_SIZE = 2000


def pick_businesses() -> list[dict]:
    candidates = []
    with open(BUSINESS_FILE, encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            if b.get("categories") and "Restaurants" in b["categories"]:
                candidates.append(b)

    candidates.sort(key=lambda b: b["review_count"], reverse=True)
    return [
        {
            "business_id": b["business_id"],
            "name": b["name"],
            "city": b.get("city"),
            "state": b.get("state"),
            "categories": b.get("categories"),
            "stars": b.get("stars"),
            "review_count": b.get("review_count"),
        }
        for b in candidates[:TOP_N]
    ]


def stream_reviews(keep: set[str]):
    with open(REVIEW_FILE, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["business_id"] in keep and r.get("text"):
                yield {
                    "business_id": r["business_id"],
                    "text": r["text"],
                    "rating": int(r["stars"]),
                    "review_date": datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S"),
                    "source": "yelp_open",
                }


async def main():
    businesses = pick_businesses()
    keep = {b["business_id"] for b in businesses}
    print(f"Selected {len(businesses)} businesses")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as db:
        await db.execute(insert(Business), businesses)
        await db.commit()

        batch, total = [], 0
        for row in stream_reviews(keep):
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                await db.execute(insert(Feedback), batch)
                await db.commit()
                total += len(batch)
                batch.clear()
                print(f"{total} rows inserted")
        if batch:
            await db.execute(insert(Feedback), batch)
            await db.commit()
            total += len(batch)

    await engine.dispose()
    print(f"Done: {total} reviews")


asyncio.run(main())