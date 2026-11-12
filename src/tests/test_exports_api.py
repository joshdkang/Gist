"""The CSV exports, over the data seeded in conftest.py: they match the JSON endpoints, follow the
same filters, stream, and keep review text from running as a spreadsheet formula."""
import csv
import io
import json

import pytest
from sqlalchemy import update

from models import Review

SCALE = json.dumps({"type": "numeric", "min": 1, "max": 5})


def read_csv(response) -> list[dict[str, str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")            # Excel reads the file as UTF-8 only with the mark
    return list(csv.DictReader(io.StringIO(text[1:])))


async def test_the_themes_export_matches_the_theme_list(call, seeded):
    path = f"/entities/{seeded.entity_id}"
    response = await call("GET", f"{path}/exports/themes.csv")
    rows = read_csv(response)
    assert response.headers["content-disposition"].startswith('attachment; filename="seeded-themes-')

    listing = (await call("GET", f"{path}/themes", params={"include_candidates": "true"})).json()
    listed = [*listing["themes"], *listing["candidates"]]
    assert [row["theme"] for row in rows] == [theme["name"] for theme in listed] + ["General (no specific topic)"]
    by_name = {row["theme"]: row for row in rows}
    service = by_name["Service"]
    assert (service["kind"], service["reviews"], service["positive"], service["negative"]) == (
        "theme", "30", "20", "10"
    )
    assert float(service["share_of_reviews_pct"]) == pytest.approx(75.0)   # 30 of the 40 classified reviews
    assert by_name["Parking"]["kind"] == "emerging topic"
    assert by_name["General (no specific topic)"]["reviews"] == "1"
    # The seeded reviews have ratings, so the ratings-by-theme columns are there.
    assert float(service["average_rating_with"]) == pytest.approx(3.0)
    assert service["rating_difference_significant"] == "no"


async def test_the_themes_export_takes_the_rating_filter(call, seeded):
    response = await call(
        "GET", f"/entities/{seeded.entity_id}/exports/themes.csv", params={"rating": "1", "rating_scale": SCALE}
    )
    rows = {row["theme"]: row for row in read_csv(response)}
    assert "-rated-1-" in response.headers["content-disposition"]
    # Rated 1: reviews 0, 5, ..., 35; Service raises six of them, as the filtered list says.
    assert rows["Service"]["reviews"] == "6" and rows["Price"]["reviews"] == "2"


async def test_the_changes_export_matches_the_comparison(call, seeded):
    params = {"interval": "year", "period": "2022-01-01T00:00:00Z"}
    response = await call("GET", f"/entities/{seeded.entity_id}/exports/changes.csv", params=params)
    rows = read_csv(response)
    assert "-comparison-2021-vs-2022-" in response.headers["content-disposition"]
    assert [row["theme"] for row in rows] == ["Service", "Parking"]
    service = rows[0]
    assert (service["period"], service["compared_with"]) == ("2022", "2021")
    assert (service["reviews_in_period"], service["theme_reviews"], service["theme_reviews_compared"]) == (
        "13", "6", "12"
    )
    assert float(service["share_pct"]) == pytest.approx(600 / 13, abs=0.01)
    assert float(service["share_change_pts"]) == pytest.approx(600 / 13 - 100, abs=0.01)
    assert service["share_change_significant"] == "yes"
    assert float(service["negative_change_pts"]) == pytest.approx(0.0, abs=0.01)   # a third negative both years


async def test_the_reviews_export_lists_each_review_with_its_themes(call, seeded):
    path = f"/entities/{seeded.entity_id}/exports/reviews.csv"
    rows = read_csv(await call("GET", path))
    # Service 30, Price 10, Parking 15 (Valet merged in), Rare 4 and General 1.
    assert len(rows) == 60
    assert {row["theme"] for row in rows} == {"Service", "Price", "Parking", "Rare", "General"}
    assert not any(row["theme"] == "Valet" for row in rows)
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates, reverse=True)

    parking = read_csv(await call("GET", path, params={"theme_id": seeded.valet.id}))
    assert len(parking) == 15 and {row["theme"] for row in parking} == {"Parking"}
    by_id = {int(row["review_id"]): row for row in parking}
    assert by_id[seeded.review_ids[32]]["sentiment"] == "mixed"       # a positive tag beside a negative one

    filtered = {"theme_id": seeded.service.id, "rating": "1", "rating_scale": SCALE}
    assert len(read_csv(await call("GET", path, params=filtered))) == 6
    negative = read_csv(await call("GET", path, params={"theme_id": seeded.service.id, "sentiment": "negative"}))
    assert len(negative) == 10 and {row["sentiment"] for row in negative} == {"negative"}
    assert (await call("GET", path, params={"period": "2020-01-01T00:00:00Z"})).status_code == 422


async def test_the_reviews_export_streams_in_batches(call, seeded, monkeypatch):
    from routers import exports

    monkeypatch.setattr(exports, "STREAM_BATCH", 7)
    rows = read_csv(await call("GET", f"/entities/{seeded.entity_id}/exports/reviews.csv"))
    assert len(rows) == 60 and len({row["review_id"] for row in rows}) == 40


async def test_text_that_would_run_as_a_formula_stays_text(call, db, seeded):
    review_id = seeded.review_ids[0]
    await db.execute(
        update(Review).where(Review.id == review_id).values(text="=HYPERLINK(\"http://x\")", title="@cmd")
    )
    await db.commit()
    path = f"/entities/{seeded.entity_id}/exports/reviews.csv"
    rows = read_csv(await call("GET", path, params={"theme_id": seeded.price.id}))
    row = next(row for row in rows if row["review_id"] == str(review_id))
    assert row["text"] == "'=HYPERLINK(\"http://x\")" and row["title"] == "'@cmd"


async def test_exports_are_readable_like_the_entity(call, seeded, bob):
    for name in ("themes.csv", "changes.csv", "reviews.csv"):
        response = await call("GET", f"/entities/{seeded.entity_id}/exports/{name}", person=bob)
        assert response.status_code == 404
    other = (await call("POST", "/entities", json={"name": "Other"})).json()
    path = f"/entities/{other['id']}/exports/reviews.csv"
    assert (await call("GET", path, params={"theme_id": seeded.service.id})).status_code == 404


async def test_the_themes_export_takes_the_period_filter(call, seeded):
    params = {"interval": "year", "period": ["2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z"]}
    response = await call("GET", f"/entities/{seeded.entity_id}/exports/themes.csv", params=params)
    rows = {row["theme"]: row for row in read_csv(response)}
    assert "-themes-2020-to-2021-" in response.headers["content-disposition"]
    # Reviews 0-23: Service raises all of them, Price 0-9, Parking 20-23.
    assert (rows["Service"]["reviews"], rows["Price"]["reviews"], rows["Parking"]["reviews"]) == ("24", "10", "4")
