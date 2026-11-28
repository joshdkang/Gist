"""Embed the reviews that have no embedding yet, for one entity or every public one.

    python embed_reviews.py <entity_id> [<entity_id> ...]
    python embed_reviews.py --public

The worker embeds an entity's new reviews as the first step of each analysis; this script is for
the public sample entities, which scripts load and analyse, and for embedding again after a change
of model. Each entity's work is recorded as an `embed` run with its tokens, so it is in the cost log.
Re-running it is safe: reviews that already have a vector from the configured model are skipped.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from database import async_session_maker, engine
from embeddings import configured_embedder, count_missing_embeddings, embed_missing_reviews
from models import Entity, StepKind
from tracking import RunConflict, begin_step


async def embed_entity(entity_id: int) -> None:
    embedder = configured_embedder()
    async with async_session_maker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            print(f"No entity {entity_id}.")
            return
        missing = await count_missing_embeddings(db, entity_id, embedder.model)
        if not missing:
            print(f"{entity.name} ({entity_id}): every review is embedded.")
            return
        print(f"{entity.name} ({entity_id}): embedding {missing} reviews with {embedder.model}...")
        try:
            tracked = await begin_step(db, entity_id, StepKind.EMBED, embedder.model)
        except RunConflict as conflict:
            print(f"  {conflict}")
            return
        try:
            embedded, tokens = await embed_missing_reviews(db, embedder, entity_id)
        except Exception as error:
            await tracked.fail(db, error)
            raise
        await tracked.succeed(db, reviews_processed=embedded, input_tokens=tokens, outcome="saved")
        print(f"  embedded {embedded} reviews, {tokens} tokens (run {tracked.run.id}).")


async def main(entity_ids: list[int], public: bool) -> None:
    if configured_embedder() is None:
        raise SystemExit("Set VOYAGE_API_KEY in .env first.")
    try:
        if public:
            async with async_session_maker() as db:
                entity_ids = list(await db.scalars(select(Entity.id).where(Entity.is_public).order_by(Entity.id)))
        for entity_id in entity_ids:
            await embed_entity(entity_id)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Embed reviews that have no embedding yet.")
    parser.add_argument("entity_ids", type=int, nargs="*")
    parser.add_argument("--public", action="store_true", help="every public entity")
    args = parser.parse_args()
    if not args.entity_ids and not args.public:
        parser.error("give entity IDs or --public")
    asyncio.run(main(args.entity_ids, args.public))
