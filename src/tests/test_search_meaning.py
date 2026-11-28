"""Search by meaning: the keywords, or reviews whose embedding is close to the search's."""
import csv
import io
import json

import pytest
from sqlalchemy import update

from embeddings import embed_missing_reviews, get_embedder
from main import app
from models import Review, ReviewEmbedding
from search import MEANING_MIN_SIMILARITY, MEANING_Z


@pytest.fixture
def use_embedder(fake_embedder):
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    yield fake_embedder
    app.dependency_overrides.pop(get_embedder, None)


async def valet_reviews(db, seeded, embedder):
    """Review 0 says "valet"; review 1 says it in other words and is given the query's own vector,
    so only meaning finds it. The other reviews ("Seed review N") are far from both."""
    texts = {0: "The valet parked our car quickly", 1: "An attendant took the vehicle round the back"}
    for index, text in texts.items():
        await db.execute(update(Review).where(Review.id == seeded.review_ids[index]).values(text=text))
    await db.commit()
    await embed_missing_reviews(db, embedder, seeded.entity_id)
    await db.execute(
        update(ReviewEmbedding)
        .where(ReviewEmbedding.review_id == seeded.review_ids[1])
        .values(embedding=embedder.vector("valet"))
    )
    await db.commit()


def meaning(**params):
    return {"q": "valet", "search_mode": "meaning", **params}


async def test_meaning_adds_reviews_that_say_it_otherwise(call, db, seeded, use_embedder):
    await valet_reviews(db, seeded, use_embedder)
    path = f"/entities/{seeded.entity_id}/themes"
    assert (await call("GET", path, params={"q": "valet"})).json()["total_reviews"] == 1
    assert not [kind for kind, _ in use_embedder.calls if kind == "query"]  # words mode needs no vector
    body = (await call("GET", path, params=meaning())).json()
    assert body["total_reviews"] == 2
    assert {theme["name"]: theme["review_count"] for theme in body["themes"]} == {"Service": 2, "Price": 2}
    # It combines with the other filters: review 0 is rated 1, review 1 is rated 2.
    scale = json.dumps({"type": "numeric", "min": 1, "max": 5})
    assert (await call("GET", path, params=meaning(rating="2", rating_scale=scale))).json()["total_reviews"] == 1


async def test_meaning_filters_a_themes_reviews_trend_and_the_review_list(call, db, seeded, use_embedder):
    await valet_reviews(db, seeded, use_embedder)
    theme_path = f"/entities/{seeded.entity_id}/themes/{seeded.service.id}"
    reviews = (await call("GET", f"{theme_path}/reviews", params=meaning(limit=100))).json()
    assert {review["id"] for review in reviews} == set(seeded.review_ids[:2])
    trend = (await call("GET", f"{theme_path}/trend", params=meaning(interval="year"))).json()
    assert sum(point["sentiment"]["total"] for point in trend["points"]) == 2
    assert sum(point["period_reviews"] for point in trend["points"]) == 2

    listed = (await call("GET", f"/entities/{seeded.entity_id}/reviews", params=meaning(limit=100))).json()
    assert {review["id"] for review in listed} == set(seeded.review_ids[:2])
    facets = (await call("GET", f"/entities/{seeded.entity_id}/reviews/facets", params=meaning())).json()
    assert facets["total"] == 2


async def test_the_exports_follow_the_meaning_search(call, db, seeded, use_embedder):
    await valet_reviews(db, seeded, use_embedder)
    response = await call("GET", f"/entities/{seeded.entity_id}/exports/reviews.csv", params=meaning())
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert {int(row["review_id"]) for row in rows} == set(seeded.review_ids[:2])
    themes = await call("GET", f"/entities/{seeded.entity_id}/exports/themes.csv", params=meaning())
    service = next(row for row in csv.DictReader(io.StringIO(themes.content.decode("utf-8-sig"))) if row["theme"] == "Service")
    assert service["reviews"] == "2"


async def test_a_review_needs_to_be_well_above_the_typical_similarity(call, db, seeded, use_embedder):
    await valet_reviews(db, seeded, use_embedder)
    # Review 0 shares one of its six words with the query: closer than the rest, but not by
    # MEANING_Z standard deviations, so it is found by its word, not its meaning.
    await db.execute(update(Review).where(Review.id == seeded.review_ids[0]).values(text="The man parked our car quickly"))
    await db.commit()
    assert MEANING_Z > 2
    body = (await call("GET", f"/entities/{seeded.entity_id}/themes", params=meaning())).json()
    assert body["total_reviews"] == 1


async def test_a_review_far_above_the_rest_still_needs_the_minimum_similarity(call, db, seeded, use_embedder):
    await valet_reviews(db, seeded, use_embedder)
    # Review 1 at a similarity just under the floor: far above the other reviews (near 0), but not
    # close enough to the search to count.
    query = use_embedder.vector("valet")
    other = [0.0] * len(query)
    other[next(index for index, value in enumerate(query) if value == 0)] = 1.0
    similarity = MEANING_MIN_SIMILARITY - 0.03
    below = [similarity * q + (1 - similarity**2) ** 0.5 * o for q, o in zip(query, other)]
    await db.execute(
        update(ReviewEmbedding).where(ReviewEmbedding.review_id == seeded.review_ids[1]).values(embedding=below)
    )
    await db.commit()
    body = (await call("GET", f"/entities/{seeded.entity_id}/themes", params=meaning())).json()
    assert body["total_reviews"] == 1   # review 0, by its word


async def test_meaning_needs_embeddings_and_a_configured_embedder(call, db, seeded, use_embedder):
    path = f"/entities/{seeded.entity_id}/themes"
    response = await call("GET", path, params=meaning())
    assert response.status_code == 409 and "indexed" in response.json()["detail"]

    app.dependency_overrides[get_embedder] = lambda: None
    assert (await call("GET", path, params=meaning())).status_code == 503
    assert (await call("GET", path, params={"q": "valet"})).status_code == 200   # words need no embedder
    assert (await call("GET", path, params={"q": "valet", "search_mode": "sideways"})).status_code == 422
