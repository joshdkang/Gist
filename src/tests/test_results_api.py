"""The theme list, a theme's reviews, and its trend, over the data seeded in conftest.py."""
from collections import Counter

from tests.conftest import SEED_REVIEWS, seed_date


async def test_theme_list_ranks_by_recent_mentions(call, seeded):
    response = await call("GET", f"/entities/{seeded.entity_id}/themes", params={"recent_days": 400})
    assert response.status_code == 200
    body = response.json()
    assert body["total_reviews"] == SEED_REVIEWS and body["classified_reviews"] == SEED_REVIEWS
    assert body["as_of"].startswith(seed_date(SEED_REVIEWS - 1).date().isoformat())
    assert body["general"] == {"review_count": 1, "share": 1 / SEED_REVIEWS}

    service, price = body["themes"]            # recent mentions first, whatever the totals
    assert service["name"] == "Service" and price["name"] == "Price"
    assert service["review_count"] == 30 and service["share"] == 30 / SEED_REVIEWS
    assert service["sentiment"] == {"positive": 20, "negative": 10, "mixed": 0, "neutral": 0, "total": 30}
    assert service["recent_count"] == 4 and not service["quiet"]    # reviews 26-29 are within 400 days
    assert price["review_count"] == 10 and price["recent_count"] == 0 and price["quiet"]


async def test_candidates_list_merged_ones_under_their_root_and_hide_rare_ones(call, seeded):
    response = await call(
        "GET", f"/entities/{seeded.entity_id}/themes", params={"kind": "candidate", "recent_days": 400}
    )
    candidates = response.json()["themes"]
    assert [theme["name"] for theme in candidates] == ["Parking"]   # Valet is merged, Rare has 4 reviews
    parking = candidates[0]
    assert parking["review_count"] == 15           # reviews 20-31 plus 32-34; review 20 counted once
    # Review 20 has Parking neutral and Valet positive, which combine to positive; review 32 has
    # Parking positive and Valet negative, which combine to mixed.
    assert parking["sentiment"] == {"positive": 1, "negative": 2, "mixed": 1, "neutral": 11, "total": 15}
    assert parking["recent_count"] == 9


async def test_general_is_not_a_listable_kind(call, seeded):
    response = await call("GET", f"/entities/{seeded.entity_id}/themes", params={"kind": "general"})
    assert response.status_code == 422


async def test_an_entity_without_reviews_has_empty_results(call, bob, public_entity):
    response = await call("GET", f"/entities/{public_entity.id}/themes", person=bob)
    assert response.status_code == 200
    body = response.json()
    assert body["total_reviews"] == 0 and body["as_of"] is None and body["themes"] == []
    assert body["general"] == {"review_count": 0, "share": 0.0}


async def test_results_are_private_to_the_owner(call, bob, seeded):
    entity_path = f"/entities/{seeded.entity_id}"
    paths = [
        f"{entity_path}/themes",
        f"{entity_path}/themes/{seeded.service.id}/reviews",
        f"{entity_path}/themes/{seeded.service.id}/trend",
    ]
    for path in paths:
        assert (await call("GET", path, person=bob)).status_code == 404, path
        assert (await call("GET", path, person=None)).status_code == 401, path


async def test_theme_reviews_newest_first_with_sentiment_filter_and_paging(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/{seeded.service.id}/reviews"
    negative = (await call("GET", path, params={"sentiment": "negative", "limit": 100})).json()
    assert len(negative) == 10 and {review["sentiment"] for review in negative} == {"negative"}
    dates = [review["review_date"] for review in negative]
    assert dates == sorted(dates, reverse=True)
    assert {"id", "title", "text", "rating", "review_date", "sentiment"} == set(negative[0])

    everything = (await call("GET", path, params={"limit": 100})).json()
    assert len(everything) == 30
    first_page = (await call("GET", path, params={"limit": 12})).json()
    second_page = (await call("GET", path, params={"limit": 12, "offset": 12})).json()
    assert first_page + second_page == everything[:24]
    assert (await call("GET", path, params={"limit": 0})).status_code == 422
    assert (await call("GET", path, params={"sentiment": "bogus"})).status_code == 422


async def test_a_merged_candidate_shows_the_theme_it_merged_into(call, seeded):
    entity_path = f"/entities/{seeded.entity_id}/themes"
    via_valet = (await call("GET", f"{entity_path}/{seeded.valet.id}/reviews", params={"limit": 100})).json()
    via_parking = (await call("GET", f"{entity_path}/{seeded.parking.id}/reviews", params={"limit": 100})).json()
    assert via_valet == via_parking and len(via_parking) == 15
    positive = (await call("GET", f"{entity_path}/{seeded.parking.id}/reviews", params={"sentiment": "positive"})).json()
    assert [review["text"] for review in positive] == ["Seed review 20"]
    trend = (await call("GET", f"{entity_path}/{seeded.valet.id}/trend")).json()
    assert trend["theme_id"] == seeded.parking.id


async def test_another_entitys_theme_is_not_found(call, seeded):
    other = (await call("POST", "/entities", json={"name": "Other"})).json()
    for suffix in ("reviews", "trend"):
        response = await call("GET", f"/entities/{other['id']}/themes/{seeded.service.id}/{suffix}")
        assert response.status_code == 404
    assert (await call("GET", f"/entities/{seeded.entity_id}/themes/999999999/reviews")).status_code == 404


async def test_theme_trend_counts_reviews_per_period(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/{seeded.service.id}/trend"
    yearly = (await call("GET", path, params={"interval": "year"})).json()
    assert yearly["interval"] == "year" and yearly["theme_id"] == seeded.service.id
    expected = Counter(seed_date(index).year for index in range(30))
    assert {point["period_start"][:4]: point["sentiment"]["total"] for point in yearly["points"]} == {
        str(year): count for year, count in expected.items()
    }
    starts = [point["period_start"] for point in yearly["points"]]
    assert starts == sorted(starts)

    monthly = (await call("GET", path, params={"interval": "month"})).json()["points"]
    assert sum(point["sentiment"]["total"] for point in monthly) == 30
    assert sum(point["sentiment"]["negative"] for point in monthly) == 10
    assert (await call("GET", path, params={"interval": "day"})).status_code == 422
