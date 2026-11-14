"""Choosing the reviews a classification run takes, over the data seeded in conftest.py."""
from classify_reviews import fetch_unprocessed


async def test_review_ids_take_exactly_those_reviews_tagged_or_not(db, seeded):
    chosen = [seeded.review_ids[5], seeded.review_ids[2], seeded.review_ids[30]]
    reviews = await fetch_unprocessed(db, seeded.entity_id, None, "0", review_ids=chosen)
    # Every seeded review is already tagged, and the listed ones come back anyway, in ID order.
    assert [review_id for review_id, _ in reviews] == sorted(chosen)
    assert await fetch_unprocessed(db, seeded.entity_id, None, "0") == []


async def test_review_ids_of_another_entity_are_left_out(db, seeded, call):
    other = (await call("POST", "/entities", json={"name": "Other"})).json()
    reviews = await fetch_unprocessed(db, other["id"], None, "0", review_ids=seeded.review_ids[:3])
    assert reviews == []
