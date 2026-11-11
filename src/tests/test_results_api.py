"""The theme list, what changed between periods, ratings by theme, a theme's reviews, and its trend,
over the data seeded in conftest.py."""
import json
from collections import Counter

import pytest
from sqlalchemy import event, update

from database import engine
from models import Review
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


async def test_the_recent_window_defaults_to_90_days(call, seeded):
    body = (await call("GET", f"/entities/{seeded.entity_id}/themes")).json()
    assert body["recent_days"] == 90
    # Reviews are 30 days apart and the latest is index 39, so the window holds reviews 36-39: only
    # Rare (a candidate) has recent mentions, and Service and Price are quiet.
    assert all(theme["quiet"] for theme in body["themes"])


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
    entity_years = Counter(seed_date(index).year for index in range(40))
    # Every year the entity has reviews is listed, including 2023, where none raise Service.
    assert {point["period_start"][:4]: point["sentiment"]["total"] for point in yearly["points"]} == {
        str(year): expected[year] for year in entity_years
    }
    assert [point["period_reviews"] for point in yearly["points"]] == [
        entity_years[year] for year in sorted(entity_years)
    ]
    starts = [point["period_start"] for point in yearly["points"]]
    assert starts == sorted(starts)

    monthly = (await call("GET", path, params={"interval": "month"})).json()["points"]
    assert sum(point["sentiment"]["total"] for point in monthly) == 30
    assert sum(point["sentiment"]["negative"] for point in monthly) == 10
    assert (await call("GET", path, params={"interval": "day"})).status_code == 422


async def test_one_call_can_return_the_themes_and_the_candidates(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes"
    params = {"recent_days": 400}
    both = (await call("GET", path, params={**params, "include_candidates": "true"})).json()
    themes = (await call("GET", path, params=params)).json()
    candidates = (await call("GET", path, params={**params, "kind": "candidate"})).json()
    assert both["themes"] == themes["themes"] and both["candidates"] == candidates["themes"]
    assert themes["candidates"] is None


async def test_the_theme_list_takes_few_queries(call, seeded):
    # Every query is a round trip to the database: signing in and finding the entity take one each,
    # and the list itself takes three (totals, sentiment counts, themes).
    selects: list[str] = []

    def count_selects(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        response = await call(
            "GET", f"/entities/{seeded.entity_id}/themes", params={"include_candidates": "true"}
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200
    assert len(selects) <= 5, selects


async def test_ratings_give_the_count_average_and_distribution(call, seeded, bob, public_entity):
    response = await call("GET", f"/entities/{seeded.entity_id}/ratings")
    assert response.status_code == 200
    body = response.json()
    assert body["total_reviews"] == SEED_REVIEWS and body["rated_reviews"] == SEED_REVIEWS
    (summary,) = body["scales"]
    assert summary["scale"] == {"type": "numeric", "min": 1.0, "max": 5.0}
    assert summary["count"] == SEED_REVIEWS and summary["average"] == 3.0     # ratings cycle 1-5
    assert summary["buckets"] == [{"label": str(level), "count": 8} for level in (5, 4, 3, 2, 1)]

    assert (await call("GET", f"/entities/{seeded.entity_id}/ratings", person=bob)).status_code == 404
    empty = (await call("GET", f"/entities/{public_entity.id}/ratings", person=bob)).json()
    assert empty == {"total_reviews": 0, "rated_reviews": 0, "scales": []}


async def test_the_theme_list_can_count_only_some_ratings(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes"
    scale = (await call("GET", f"/entities/{seeded.entity_id}/ratings")).json()["scales"][0]["scale"]
    params = {"rating": "1", "rating_scale": json.dumps(scale), "include_candidates": "true", "recent_days": 400}
    body = (await call("GET", path, params=params)).json()
    # Ratings cycle 1-5, so rating 1 is reviews 0, 5, 10, ... 35.
    assert body["total_reviews"] == 8 and body["classified_reviews"] == 8
    assert body["general"] == {"review_count": 1, "share": 1 / 8}          # review 35
    themes = {theme["name"]: theme for theme in body["themes"]}
    assert themes["Service"]["sentiment"] == {"positive": 4, "negative": 2, "mixed": 0, "neutral": 0, "total": 6}
    # The window is still the 400 days to review 39 (reviews 26-39), where no Service review is rated 1.
    assert themes["Service"]["share"] == 6 / 8 and themes["Service"]["recent_count"] == 0
    assert themes["Price"]["review_count"] == 2                            # reviews 0 and 5
    # Parking is listed by its unfiltered count (15) but counts only reviews 20, 25 and 30 here.
    assert [(theme["name"], theme["review_count"]) for theme in body["candidates"]] == [("Parking", 3)]

    two = (await call("GET", path, params={**params, "rating": ["4", "5"]})).json()
    assert two["total_reviews"] == 16
    # Rating 5 is reviews 4, 9, 14 ...: Price has reviews 0-9, so 3, 4, 8 and 9 with 4 or 5.
    assert {theme["name"]: theme["review_count"] for theme in two["themes"]}["Price"] == 4


async def test_a_bad_rating_filter_is_refused(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes"
    scale = json.dumps({"type": "numeric", "min": 1, "max": 5})
    assert (await call("GET", path, params={"rating": "5"})).status_code == 422             # no scale
    assert (await call("GET", path, params={"rating": "6", "rating_scale": scale})).status_code == 422
    assert (await call("GET", path, params={"rating": "5", "rating_scale": "{oops"})).status_code == 422
    # A scale no import used matches nothing, so no theme is listed.
    other = json.dumps({"type": "numeric", "min": 1, "max": 10})
    body = (await call("GET", path, params={"rating": "5", "rating_scale": other})).json()
    assert body["total_reviews"] == 0 and body["themes"] == []


async def test_theme_reviews_can_be_limited_to_trend_periods(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/{seeded.service.id}"
    points = (await call("GET", f"{path}/trend", params={"interval": "year"})).json()["points"]
    chosen = [points[0], points[2]]
    params = {"interval": "year", "period": [point["period_start"] for point in chosen], "limit": 100}
    reviews = (await call("GET", f"{path}/reviews", params=params)).json()
    assert len(reviews) == sum(point["sentiment"]["total"] for point in chosen)
    assert {review["review_date"][:4] for review in reviews} == {point["period_start"][:4] for point in chosen}
    negative = (await call("GET", f"{path}/reviews", params={**params, "sentiment": "negative"})).json()
    assert len(negative) == sum(point["sentiment"]["negative"] for point in chosen)
    assert (await call("GET", f"{path}/reviews", params={"period": params["period"]})).status_code == 422


async def test_changes_compare_a_period_with_the_one_before(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/changes"
    # Yearly, the seeded reviews fall 12 in 2020, 12 in 2021, 13 in 2022 and 3 in 2023.
    body = (await call("GET", path, params={"interval": "year", "period": "2022-01-01T00:00:00Z"})).json()
    assert body["period_start"][:4] == "2022" and body["previous_start"][:4] == "2021"
    assert (body["previous_reviews"], body["current_reviews"]) == (12, 13)
    assert [(period["period_start"][:4], period["reviews"]) for period in body["periods"]] == [
        ("2020", 12), ("2021", 12), ("2022", 13), ("2023", 3)
    ]
    changes = {change["name"]: change for change in body["themes"]}
    # Price (2020 only) and Rare (too few reviews to be listed) are left out, and so is General.
    assert set(changes) == {"Service", "Parking"}

    service = changes["Service"]                # reviews 12-23 in 2021, 24-29 in 2022
    assert service["previous"] == {"positive": 8, "negative": 4, "mixed": 0, "neutral": 0, "total": 12}
    assert service["current"] == {"positive": 4, "negative": 2, "mixed": 0, "neutral": 0, "total": 6}
    assert service["previous_share"] == 1.0 and service["current_share"] == pytest.approx(6 / 13)
    assert service["share_change_clear"]
    assert not service["negative_change_clear"]  # a third negative in both years

    parking = changes["Parking"]                # with Valet merged in: 20-23 in 2021, 24-34 in 2022
    assert parking["previous"]["total"] == 4 and parking["current"]["total"] == 11
    assert parking["current"]["mixed"] == 1 and parking["current"]["negative"] == 2
    assert parking["share_change_clear"]
    # Biggest change in share first: Service moved 54 points, Parking 51.
    assert [change["name"] for change in body["themes"]] == ["Service", "Parking"]


async def test_changes_default_to_a_period_with_enough_reviews(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/changes"
    # No year has 20 reviews, so the latest year is used, against a 2022 with Parking but no Service.
    body = (await call("GET", path, params={"interval": "year"})).json()
    assert body["period_start"][:4] == "2023" and body["current_reviews"] == 3
    changes = {change["name"]: change for change in body["themes"]}
    assert changes["Service"]["current"]["total"] == 0 and changes["Service"]["previous"]["total"] == 6
    # The previous period is the calendar one before, even with no reviews in it.
    first = (await call("GET", path, params={"interval": "year", "period": "2020-01-01T00:00:00Z"})).json()
    assert first["previous_start"][:4] == "2019" and first["previous_reviews"] == 0
    assert all(change["previous_share"] == 0 for change in first["themes"])
    missing = await call("GET", path, params={"interval": "year", "period": "2019-01-01T00:00:00Z"})
    assert missing.status_code == 422


async def test_changes_on_an_entity_without_dates(call):
    entity = (await call("POST", "/entities", json={"name": "Undated"})).json()
    body = (await call("GET", f"/entities/{entity['id']}/themes/changes")).json()
    assert body["period_start"] is None and body["periods"] == [] and body["themes"] == []


async def test_changes_can_compare_any_two_periods(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/changes"
    params = {"interval": "year", "period": "2022-01-01T00:00:00Z", "compare_to": "2020-01-01T00:00:00Z"}
    body = (await call("GET", path, params=params)).json()
    assert body["period_start"][:4] == "2022" and body["previous_start"][:4] == "2020"
    assert (body["previous_reviews"], body["current_reviews"]) == (12, 13)
    changes = {change["name"]: change for change in body["themes"]}
    # Price (reviews 0-9) is back: it was raised in 2020, and by nobody in 2022.
    assert changes["Price"]["previous"]["total"] == 10 and changes["Price"]["current"]["total"] == 0
    assert changes["Service"]["previous"]["total"] == 12      # reviews 0-11
    assert changes["Parking"]["previous"]["total"] == 0

    # A later period works as the comparison too; the same period, or one with no reviews, doesn't.
    later = (await call("GET", path, params={**params, "compare_to": "2023-01-01T00:00:00Z"})).json()
    assert later["previous_start"][:4] == "2023" and later["previous_reviews"] == 3
    same = await call("GET", path, params={**params, "compare_to": params["period"]})
    empty = await call("GET", path, params={**params, "compare_to": "2019-01-01T00:00:00Z"})
    assert same.status_code == 422 and empty.status_code == 422


async def test_theme_ratings_compare_reviews_with_and_without_each_theme(call, db, seeded):
    # Seeded ratings run 1-5 in turn, so every theme averages 3; rate Price's reviews (0-9) 1 instead.
    await db.execute(update(Review).where(Review.id.in_(seeded.review_ids[:10])).values(rating=1))
    await db.commit()
    body = (await call("GET", f"/entities/{seeded.entity_id}/themes/ratings")).json()
    assert body["scale"] == {"type": "numeric", "min": 1.0, "max": 5.0}
    # 40 rated reviews: ten 1s, then reviews 10-39 cycle 1-5 six times (sum 90), so 100 / 40.
    assert body["rated_reviews"] == 40 and body["average"] == pytest.approx(100 / 40)
    impacts = {impact["name"]: impact for impact in body["themes"]}
    assert set(impacts) == {"Service", "Price", "Parking"}   # Rare has too few reviews; General is left out

    price = impacts["Price"]
    assert price["rated_reviews"] == 10
    assert price["average_with"] == 1.0 and price["average_without"] == pytest.approx(3.0)
    assert price["effect"] == pytest.approx(10 / 40 * -2.0)
    assert price["significant"] and price["p_value"] < 1e-6
    service = impacts["Service"]                     # reviews 0-29: ten 1s, then 10-29 (sum 60)
    assert service["average_with"] == pytest.approx(70 / 30) and service["average_without"] == pytest.approx(3.0)
    # Most negative effect first.
    effects = [impact["effect"] for impact in body["themes"]]
    assert effects == sorted(effects) and body["themes"][0]["name"] == "Price"

    # The scale can be named; one no import used has no ratings, and a bad one is refused.
    named = {"rating_scale": json.dumps({"type": "numeric", "min": 1, "max": 5})}
    assert (await call("GET", f"/entities/{seeded.entity_id}/themes/ratings", params=named)).json() == body
    other = {"rating_scale": json.dumps({"type": "binary"})}
    empty = (await call("GET", f"/entities/{seeded.entity_id}/themes/ratings", params=other)).json()
    assert empty["rated_reviews"] == 0 and empty["themes"] == []
    bad = await call("GET", f"/entities/{seeded.entity_id}/themes/ratings", params={"rating_scale": "{"})
    assert bad.status_code == 422


async def test_theme_ratings_without_ratings(call):
    entity = (await call("POST", "/entities", json={"name": "Unrated"})).json()
    body = (await call("GET", f"/entities/{entity['id']}/themes/ratings")).json()
    assert body == {"scale": None, "rated_reviews": 0, "average": None, "themes": []}


async def test_a_themes_reviews_and_trend_take_the_rating_filter(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes/{seeded.service.id}"
    scale = json.dumps({"type": "numeric", "min": 1, "max": 5})
    # Seeded ratings are (index % 5) + 1, so Service's reviews 0-29 rated 1 are 0, 5, ..., 25.
    rated_1 = {"rating": "1", "rating_scale": scale}
    reviews = (await call("GET", f"{path}/reviews", params={**rated_1, "limit": 100})).json()
    assert len(reviews) == 6 and {review["rating"] for review in reviews} == {1.0}

    trend = (await call("GET", f"{path}/trend", params={**rated_1, "interval": "year"})).json()["points"]
    assert sum(point["sentiment"]["total"] for point in trend) == 6
    # The entity's rated-1 reviews (0, 5, ..., 35) per year: the denominator follows the filter too.
    expected = Counter(seed_date(index).year for index in range(0, 40, 5))
    assert {point["period_start"][:4]: point["period_reviews"] for point in trend} == {
        str(year): count for year, count in expected.items()
    }
    # The filter combines with periods, and a bad one is refused.
    first = trend[0]["period_start"]
    in_first = (
        await call("GET", f"{path}/reviews", params={**rated_1, "interval": "year", "period": first})
    ).json()
    assert len(in_first) == trend[0]["sentiment"]["total"]
    assert (await call("GET", f"{path}/reviews", params={"rating": "1"})).status_code == 422
    assert (await call("GET", f"{path}/trend", params={"rating": "9", "rating_scale": scale})).status_code == 422


async def test_a_themes_rating_summary_counts_only_its_reviews(call, seeded):
    path = f"/entities/{seeded.entity_id}/themes"
    # Parking with Valet merged in: reviews 20-34, rated (index % 5) + 1, so three of each rating.
    body = (await call("GET", f"{path}/{seeded.parking.id}/ratings")).json()
    assert body["total_reviews"] == 15 and body["rated_reviews"] == 15
    (summary,) = body["scales"]
    assert summary["average"] == pytest.approx(3.0)
    assert [(bucket["label"], bucket["count"]) for bucket in summary["buckets"]] == [
        ("5", 3), ("4", 3), ("3", 3), ("2", 3), ("1", 3)
    ]
    # The merged candidate's id gives the same summary, and another entity's theme is a 404.
    assert (await call("GET", f"{path}/{seeded.valet.id}/ratings")).json() == body
    other = (await call("POST", "/entities", json={"name": "Other"})).json()
    assert (await call("GET", f"/entities/{other['id']}/themes/{seeded.parking.id}/ratings")).status_code == 404
