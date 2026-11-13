"""The All reviews page's endpoints, over the data seeded in conftest.py: the review list with each
review's themes, and the facet counts behind its filter column.

The seeded reviews (see Seeded): Service 0-29 (every third negative), Price 0-9 (positive), Parking
20-34 with Valet merged in (21-31 neutral, 20 positive, 32 mixed, 33 and 34 negative), Rare 36-39
(too few to be listed), General 35. Review i is rated (i % 5) + 1, and later reviews are newer.
"""
import csv
import io
import json

from tests.conftest import SEED_REVIEWS

SCALE = json.dumps({"type": "numeric", "min": 1, "max": 5})


async def facets(call, seeded, **params):
    response = await call("GET", f"/entities/{seeded.entity_id}/reviews/facets", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return body, {theme["name"]: theme for theme in body["themes"]}


async def test_the_review_list_shows_each_review_with_its_themes(call, seeded):
    path = f"/entities/{seeded.entity_id}/reviews"
    reviews = (await call("GET", path, params={"limit": 100})).json()
    assert len(reviews) == SEED_REVIEWS
    assert reviews[0]["id"] == seeded.review_ids[39]                   # newest first
    by_id = {review["id"]: review for review in reviews}
    # Merged candidates show under the theme they merged into, with the combined sentiment.
    assert [(tag["name"], tag["sentiment"]) for tag in by_id[seeded.review_ids[32]]["themes"]] == [("Parking", "mixed")]
    assert [(tag["name"], tag["sentiment"]) for tag in by_id[seeded.review_ids[20]]["themes"]] == [
        ("Service", "positive"), ("Parking", "positive")                # themes before emerging topics
    ]
    oldest = (await call("GET", path, params={"sort": "oldest", "limit": 1})).json()
    assert oldest[0]["id"] == seeded.review_ids[0]
    highest = (await call("GET", path, params={"sort": "highest", "limit": 8})).json()
    assert {review["rating"] for review in highest} == {5.0}
    page = (await call("GET", path, params={"limit": 15, "offset": 30})).json()
    assert len(page) == 10


async def test_theme_filters_combine_with_any_or_all(call, seeded):
    path = f"/entities/{seeded.entity_id}/reviews"
    service, price, valet = seeded.service.id, seeded.price.id, seeded.valet.id

    async def ids(**params):
        return {review["id"] for review in (await call("GET", path, params={**params, "limit": 100})).json()}

    index = {review_id: position for position, review_id in enumerate(seeded.review_ids)}
    assert sorted(index[i] for i in await ids(theme=[str(service), str(price)])) == list(range(30))
    assert sorted(index[i] for i in await ids(theme=[str(service), str(price)], match="all")) == list(range(10))
    # A theme's own sentiments: Service mentions that are negative.
    assert sorted(index[i] for i in await ids(theme=f"{service}:negative")) == list(range(0, 30, 3))
    # A merged candidate's id means the theme it merged into; Parking negative is 33 and 34.
    assert sorted(index[i] for i in await ids(theme=f"{valet}:negative")) == [33, 34]
    # Several sentiments, and the other filters.
    assert sorted(index[i] for i in await ids(theme=f"{valet}:negative,mixed")) == [32, 33, 34]
    rated = await ids(theme=f"{service}:negative", rating="1", rating_scale=SCALE)
    assert sorted(index[i] for i in rated) == [0, 15]


async def test_facets_count_what_each_choice_would_give(call, seeded):
    body, themes = await facets(call, seeded)
    assert body["total"] == SEED_REVIEWS
    # Rare is raised by too few reviews to be listed; General comes last.
    assert [theme["name"] for theme in body["themes"]] == ["Service", "Price", "Parking", "General"]
    assert (themes["Service"]["count"], themes["Parking"]["count"]) == (30, 15)
    assert themes["Service"]["sentiments"] is None                     # only chosen themes are split
    assert [bucket["count"] for bucket in body["ratings"]["scales"][0]["buckets"]] == [8, 8, 8, 8, 8]

    # Any: a theme's count ignores the theme selection, but a chosen theme keeps its own sentiments.
    service = seeded.service.id
    body, themes = await facets(call, seeded, theme=f"{service}:negative")
    assert body["total"] == 10
    assert (themes["Service"]["count"], themes["Price"]["count"]) == (10, 10)
    assert themes["Service"]["sentiments"] == {"positive": 20, "negative": 10, "mixed": 0, "neutral": 0, "total": 30}
    # The ratings of the ten: reviews 0, 3, ..., 27 are rated 1, 4, 2, 5, 3, 1, 4, 2, 5, 3.
    assert [bucket["count"] for bucket in body["ratings"]["scales"][0]["buckets"]] == [2, 2, 2, 2, 2]

    # All: each count is how many of the current results also raise the theme.
    body, themes = await facets(call, seeded, theme=[f"{service}:negative", str(seeded.parking.id)], match="all")
    assert body["total"] == 3                                          # reviews 21, 24 and 27
    assert (themes["Service"]["count"], themes["Parking"]["count"], themes["Price"]["count"]) == (3, 3, 0)
    # Service's split among the reviews that raise Parking: 20-29, three of them negative.
    assert themes["Service"]["sentiments"]["negative"] == 3 and themes["Service"]["sentiments"]["positive"] == 7


async def test_bad_filters_are_refused_and_other_entities_hidden(call, seeded, bob):
    path = f"/entities/{seeded.entity_id}/reviews"
    assert (await call("GET", path, params={"theme": "abc"})).status_code == 422
    assert (await call("GET", path, params={"theme": f"{seeded.service.id}:furious"})).status_code == 422
    assert (await call("GET", path, person=bob)).status_code == 404
    assert (await call("GET", f"{path}/facets", person=bob)).status_code == 404
    other = (await call("POST", "/entities", json={"name": "Other"})).json()
    response = await call("GET", f"/entities/{other['id']}/reviews", params={"theme": str(seeded.service.id)})
    assert response.status_code == 404


async def test_the_reviews_export_takes_the_theme_filters(call, seeded):
    params = {"theme": [f"{seeded.service.id}:negative", str(seeded.price.id)], "match": "all"}
    response = await call("GET", f"/entities/{seeded.entity_id}/exports/reviews.csv", params=params)
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8")[1:])))
    # Reviews 0, 3, 6 and 9, each with both of its themes; the file is named for the filter.
    assert len(rows) == 8 and {row["theme"] for row in rows} == {"Service", "Price"}
    assert "-reviews-2-themes-" in response.headers["content-disposition"]
    one = await call(
        "GET", f"/entities/{seeded.entity_id}/exports/reviews.csv", params={"theme": f"{seeded.service.id}:negative"}
    )
    assert "-reviews-service-negative-" in one.headers["content-disposition"]


async def test_facets_count_the_periods_under_the_other_filters(call, seeded):
    body, _ = await facets(call, seeded, period_interval="year")
    assert body["period_interval"] == "year"
    assert [(period["period_start"][:4], period["reviews"]) for period in body["periods"]] == [
        ("2020", 12), ("2021", 12), ("2022", 13), ("2023", 3)
    ]
    # Price is reviews 0-9, all in 2020; the period's own selection is left out of its counts.
    body, _ = await facets(
        call, seeded, period_interval="year", theme=str(seeded.price.id), interval="year", period="2021-01-01T00:00:00Z"
    )
    assert body["total"] == 0
    assert [(period["period_start"][:4], period["reviews"]) for period in body["periods"]] == [("2020", 10)]
