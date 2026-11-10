"""Entities, uploads, the analysis queue and run status, over HTTP with signed tokens."""
import json

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

import queueing
from config import settings
from models import (
    AnalysisRun,
    Import,
    Review,
    RunStatus,
    RunStep,
    RunType,
    StepKind,
    StepStatus,
    Theme,
    ThemeKind,
)

MAPPING = json.dumps({"text": "review", "rating": "stars", "review_date": "when"})
SCALE = json.dumps({"type": "numeric", "min": 1, "max": 5})


def csv_bytes(count: int, start: int = 0) -> bytes:
    lines = ["review,stars,when"]
    lines += [
        f'"Review {i}: the parking and the service were notable, visit {i}",{(i % 5) + 1},2026-01-{(i % 28) + 1:02d}'
        for i in range(start, start + count)
    ]
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def upload(call, alice):
    async def send(entity_id, content, person=alice, mapping=MAPPING, scale=SCALE, name="reviews.csv"):
        data = {"mapping": mapping}
        if scale is not None:
            data["rating_scale"] = scale
        return await call(
            "POST", f"/entities/{entity_id}/imports", person=person,
            files={"file": (name, content, "text/csv")}, data=data,
        )
    return send


@pytest.fixture
async def entity(call):
    return (await call("POST", "/entities", json={"name": "My app"})).json()


async def count(db, model, **filters) -> int:
    query = select(func.count()).select_from(model)
    for column, value in filters.items():
        query = query.where(getattr(model, column) == value)
    return await db.scalar(query)


# --- sign-in ---------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("POST", "/entities"), ("GET", "/entities"), ("GET", "/entities/1"), ("PATCH", "/entities/1"),
    ("DELETE", "/entities/1"), ("POST", "/entities/1/imports"), ("GET", "/entities/1/imports"),
    ("GET", "/entities/1/runs"), ("GET", "/entities/1/runs/1"), ("GET", "/entities/1/themes"),
])
async def test_every_endpoint_needs_a_sign_in(call, method, path):
    assert (await call(method, path, person=None)).status_code == 401


# --- create ----------------------------------------------------------------------------------

async def test_create_cleans_fields_and_adds_the_general_theme(call, db):
    response = await call("POST", "/entities", json={
        "name": "  My   app ", "description": "A budgeting\napp for iOS ", "metadata": {"k": 1}})
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "My app" and created["description"] == "A budgeting app for iOS"
    assert created["is_owner"] and not created["is_public"]
    assert created["review_count"] == 0 and created["metadata"] == {"k": 1}
    assert await count(db, Theme, entity_id=created["id"], kind=ThemeKind.GENERAL) == 1


@pytest.mark.parametrize("body", [
    {}, {"name": ""}, {"name": "   "}, {"name": "x" * 201},
    {"name": "ok", "description": "d" * 1001}, {"name": "ok", "metadata": {"big": "x" * 11000}},
])
async def test_create_rejects_bad_fields(call, body):
    assert (await call("POST", "/entities", json=body)).status_code == 422


async def test_a_blank_description_becomes_none(call):
    response = await call("POST", "/entities", json={"name": "ok", "description": "   "})
    assert response.json()["description"] is None


async def test_entity_limit_is_per_user(call, bob, monkeypatch):
    monkeypatch.setattr(settings, "max_entities_per_user", 2)
    for name in ("one", "two"):
        assert (await call("POST", "/entities", json={"name": name})).status_code == 201
    response = await call("POST", "/entities", json={"name": "three"})
    assert response.status_code == 403 and "at most 2" in response.json()["detail"]
    assert (await call("POST", "/entities", person=bob, json={"name": "bobs"})).status_code == 201


# --- list and get ----------------------------------------------------------------------------

async def test_listing_scopes_and_other_peoples_entities(call, bob, public_entity, entity):
    bobs = (await call("POST", "/entities", person=bob, json={"name": "bobs"})).json()
    mine = (await call("GET", "/entities?scope=mine")).json()
    public = (await call("GET", "/entities?scope=public")).json()
    everything = (await call("GET", "/entities")).json()
    assert [item["id"] for item in mine] == [entity["id"]] and mine[0]["is_owner"]
    assert public_entity.id in [item["id"] for item in public] and not any(item["is_owner"] for item in public)
    assert {item["id"] for item in everything} == {item["id"] for item in mine + public}
    assert bobs["id"] not in [item["id"] for item in everything]           # private entities are never listed


async def test_listing_pages_and_validates(call, entity):
    for name in ("two", "three"):
        await call("POST", "/entities", json={"name": name})
    assert len((await call("GET", "/entities?scope=mine&limit=2")).json()) == 2
    assert len((await call("GET", "/entities?scope=mine&limit=2&offset=2")).json()) == 1
    assert (await call("GET", "/entities?scope=nope")).status_code == 422
    assert (await call("GET", "/entities?limit=0")).status_code == 422


async def test_get_hides_entities_the_user_cannot_see(call, bob, entity, public_entity):
    detail = (await call("GET", f"/entities/{entity['id']}")).json()
    assert detail["import_count"] == 0 and detail["latest_run"] is None
    assert (await call("GET", f"/entities/{entity['id']}", person=bob)).status_code == 404
    assert (await call("GET", "/entities/999999999")).status_code == 404
    public = (await call("GET", f"/entities/{public_entity.id}", person=bob)).json()
    assert public["is_public"] and public["is_owner"] is False


# --- patch -----------------------------------------------------------------------------------

async def test_patch_changes_only_the_fields_sent(call, entity):
    path = f"/entities/{entity['id']}"
    response = await call("PATCH", path, json={"name": "Renamed", "description": None})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed" and response.json()["description"] is None
    response = await call("PATCH", path, json={"description": "Now described"})
    assert response.json()["name"] == "Renamed" and response.json()["description"] == "Now described"
    assert (await call("PATCH", path, json={"name": "  "})).status_code == 422


async def test_only_the_owner_can_patch(call, bob, entity, public_entity):
    assert (await call("PATCH", f"/entities/{entity['id']}", person=bob, json={"name": "x"})).status_code == 404
    assert (await call("PATCH", f"/entities/{public_entity.id}", json={"name": "x"})).status_code == 403


# --- upload errors ---------------------------------------------------------------------------

async def test_upload_needs_ownership(upload, bob, entity, public_entity):
    assert (await upload(entity["id"], csv_bytes(5), person=bob)).status_code == 404
    assert (await upload(public_entity.id, csv_bytes(5))).status_code == 403


async def test_bad_mappings_and_files_are_rejected_and_insert_nothing(call, upload, entity, db):
    entity_id = entity["id"]
    response = await upload(entity_id, csv_bytes(5), mapping="{not json")
    assert response.status_code == 422 and "mapping" in response.json()["detail"]
    assert (await upload(entity_id, csv_bytes(5), mapping=json.dumps({"title": "review"}))).status_code == 422
    response = await upload(entity_id, csv_bytes(5), scale=None)
    assert response.status_code == 422 and "rating_scale is required" in response.json()["detail"]
    assert (await upload(entity_id, csv_bytes(5), scale=json.dumps({"type": "stars"}))).status_code == 422
    response = await upload(entity_id, csv_bytes(5), mapping=json.dumps({"text": "body", "rating": "stars"}))
    assert response.status_code == 422 and "body" in response.json()["detail"]
    response = await upload(entity_id, "review\n\xe9\n".encode("latin-1"), mapping=json.dumps({"text": "review"}), scale=None)
    assert response.status_code == 422 and "UTF-8" in response.json()["detail"]
    assert (await call("POST", f"/entities/{entity_id}/imports", data={"mapping": MAPPING})).status_code == 422
    assert await count(db, Review, entity_id=entity_id) == 0


async def test_upload_size_and_row_limits(upload, entity, db, monkeypatch):
    entity_id = entity["id"]
    monkeypatch.setattr(settings, "max_upload_rows", 10)
    response = await upload(entity_id, csv_bytes(11))
    assert response.status_code == 413 and "limit is 10" in response.json()["detail"]
    assert (await upload(entity_id, csv_bytes(10))).status_code == 201     # exactly at the limit
    monkeypatch.setattr(settings, "max_upload_bytes", 200)
    assert (await upload(entity_id, csv_bytes(20))).status_code == 413
    assert await count(db, Review, entity_id=entity_id) == 10             # only the at-limit upload landed


# --- upload and the analysis queue -----------------------------------------------------------

async def test_duplicates_are_skipped_and_small_entities_queue_nothing(upload, entity):
    entity_id = entity["id"]
    await upload(entity_id, csv_bytes(10))
    again = (await upload(entity_id, csv_bytes(10))).json()                 # the same 10 reviews
    assert again["import"]["rows_received"] == 10 and again["import"]["rows_inserted"] == 0
    assert again["import"]["rows_skipped"] == 10
    assert again["analysis"]["run"] is None and "nothing to analyze" in again["analysis"]["message"]

    second = (await upload(entity_id, csv_bytes(10, start=10), name="second.csv")).json()
    assert second["import"]["rows_inserted"] == 10 and second["import"]["filename"] == "second.csv"
    assert second["import"]["status"] == "completed"
    assert second["import"]["rating_scale"] == {"type": "numeric", "min": 1.0, "max": 5.0}
    assert second["analysis"]["run"] is None
    assert "50 reviews" in second["analysis"]["message"] and "has 20" in second["analysis"]["message"]


async def test_enough_reviews_queue_one_job_for_the_user(upload, entity, db, alice):
    entity_id = entity["id"]
    response = await upload(entity_id, csv_bytes(60))
    assert response.status_code == 201
    job = response.json()["analysis"]
    assert job["run"]["type"] == "analyze" and job["run"]["status"] == "queued" and not job["reused"]
    run = await db.get(AnalysisRun, job["run"]["id"])
    assert run.requested_by == alice.id and run.entity_id == entity_id


async def test_a_waiting_job_is_reused_but_a_running_one_is_not(upload, call, entity, db):
    entity_id = entity["id"]
    first = (await upload(entity_id, csv_bytes(60))).json()["analysis"]["run"]["id"]
    again = (await upload(entity_id, csv_bytes(5, start=1000))).json()["analysis"]
    assert again["run"]["id"] == first and again["reused"] and "already queued" in again["message"]
    assert await count(db, AnalysisRun, entity_id=entity_id) == 1

    # A running job has already looked at the entity, so the next upload queues a new job behind it.
    await db.execute(update(AnalysisRun).where(AnalysisRun.id == first).values(status=RunStatus.RUNNING))
    await db.commit()
    second = (await upload(entity_id, csv_bytes(5, start=2000))).json()["analysis"]
    assert second["run"]["id"] != first and not second["reused"] and second["run"]["status"] == "queued"
    third = (await upload(entity_id, csv_bytes(5, start=2500))).json()["analysis"]
    assert third["run"]["id"] == second["run"]["id"] and third["reused"]
    assert await count(db, AnalysisRun, entity_id=entity_id) == 2
    assert (await call("DELETE", f"/entities/{entity_id}")).status_code == 409   # busy entities can't be deleted


async def test_losing_the_race_to_queue_reuses_the_winner(upload, entity, db, alice):
    entity_id = entity["id"]
    queued = (await upload(entity_id, csv_bytes(60))).json()["analysis"]["run"]["id"]
    lost = await queueing.queue_run(db, entity_id, alice.id)             # the unique index refuses a second
    assert lost.reused and lost.run.id == queued
    assert await count(db, AnalysisRun, entity_id=entity_id, status=RunStatus.QUEUED) == 1


async def test_an_entity_has_at_most_one_running_run(entity, db):
    db.add(AnalysisRun(entity_id=entity["id"], type=RunType.CLASSIFY, status=RunStatus.RUNNING))
    await db.commit()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(AnalysisRun(entity_id=entity["id"], type=RunType.CLASSIFY, status=RunStatus.RUNNING))
            await db.flush()


async def test_daily_run_limit_is_per_user_and_inserts_nothing(upload, entity, db, alice, bob, monkeypatch):
    entity_id = entity["id"]
    await upload(entity_id, csv_bytes(60))
    monkeypatch.setattr(settings, "max_runs_per_user_per_day", 1)
    assert await queueing.count_runs_today(db, alice.id) == 1
    response = await upload(entity_id, csv_bytes(5, start=7000))
    assert response.status_code == 429 and "1 analysis runs" in response.json()["detail"]
    assert await count(db, Review, entity_id=entity_id) == 60               # nothing inserted
    assert await queueing.count_runs_today(db, bob.id) == 0


# --- run status ------------------------------------------------------------------------------

async def test_run_list_and_detail(upload, call, entity, db, bob):
    entity_id = entity["id"]
    first = (await upload(entity_id, csv_bytes(60))).json()["analysis"]["run"]["id"]
    await db.execute(update(AnalysisRun).where(AnalysisRun.id == first).values(status=RunStatus.RUNNING))
    await db.commit()
    second = (await upload(entity_id, csv_bytes(5, start=2000))).json()["analysis"]["run"]["id"]

    runs = (await call("GET", f"/entities/{entity_id}/runs")).json()
    assert [run["id"] for run in runs] == [second, first]                  # newest first
    assert set(runs[0]) == {"id", "type", "status", "reviews_processed", "input_tokens", "output_tokens",
                            "error", "created_at", "started_at", "completed_at"}
    queued = (await call("GET", f"/entities/{entity_id}/runs?status=queued")).json()
    assert [run["id"] for run in queued] == [second]
    assert (await call("GET", f"/entities/{entity_id}/runs?status=bogus")).status_code == 422

    db.add(RunStep(run_id=first, kind=StepKind.CLASSIFY, status=StepStatus.RUNNING, model="m", reviews_processed=7))
    await db.commit()
    detail = (await call("GET", f"/entities/{entity_id}/runs/{first}")).json()
    assert len(detail["steps"]) == 1
    assert detail["steps"][0]["kind"] == "classify" and detail["steps"][0]["reviews_processed"] == 7
    assert (await call("GET", f"/entities/{entity_id}/runs/999999999")).status_code == 404
    assert (await call("GET", f"/entities/{entity_id}/runs", person=bob)).status_code == 404


async def test_a_run_of_another_entity_is_not_found(upload, call, entity, db):
    other = (await call("POST", "/entities", json={"name": "Other"})).json()
    run_id = (await upload(other["id"], csv_bytes(60))).json()["analysis"]["run"]["id"]
    assert (await call("GET", f"/entities/{entity['id']}/runs/{run_id}")).status_code == 404


async def test_latest_run_ignores_runs_that_changed_nothing(upload, call, entity, db):
    entity_id = entity["id"]
    queued = (await upload(entity_id, csv_bytes(60))).json()["analysis"]["run"]["id"]

    async def script_run(run_type, kind, outcome) -> int:
        run = AnalysisRun(entity_id=entity_id, type=run_type, status=RunStatus.SUCCEEDED)
        db.add(run)
        await db.flush()
        db.add(RunStep(run_id=run.id, kind=kind, status=StepStatus.SUCCEEDED, outcome=outcome))
        await db.commit()
        return run.id

    async def latest_id() -> int:
        return (await call("GET", f"/entities/{entity_id}")).json()["latest_run"]["id"]

    await script_run(RunType.CLASSIFY, StepKind.CLASSIFY, "dry run")
    await script_run(RunType.DISCOVER, StepKind.DISCOVER, "report only")
    await script_run(RunType.CONSOLIDATE, StepKind.CONSOLIDATE, "proposed")
    await script_run(RunType.CONSOLIDATE, StepKind.CONSOLIDATE, "applied")
    assert await latest_id() == queued
    assert len((await call("GET", f"/entities/{entity_id}/runs")).json()) == 5   # the list still shows them
    saved = await script_run(RunType.CLASSIFY, StepKind.CLASSIFY, "saved")
    assert await latest_id() == saved


async def test_imports_list_newest_first_and_readable_for_public_entities(upload, call, entity, bob, public_entity):
    entity_id = entity["id"]
    await upload(entity_id, csv_bytes(5))
    await upload(entity_id, csv_bytes(5, start=5), name="second.csv")
    imports = (await call("GET", f"/entities/{entity_id}/imports")).json()
    assert [item["filename"] for item in imports] == ["second.csv", "reviews.csv"]
    assert (await call("GET", f"/entities/{entity_id}/imports", person=bob)).status_code == 404
    assert (await call("GET", f"/entities/{public_entity.id}/imports", person=bob)).status_code == 200


# --- delete ----------------------------------------------------------------------------------

async def test_delete_cascades_and_is_owner_only(upload, call, entity, db, bob, public_entity):
    entity_id = entity["id"]
    first = (await upload(entity_id, csv_bytes(60))).json()["analysis"]["run"]["id"]
    db.add(RunStep(run_id=first, kind=StepKind.CLASSIFY, status=StepStatus.SUCCEEDED))
    await db.execute(update(AnalysisRun).where(AnalysisRun.id == first).values(status=RunStatus.SUCCEEDED))
    await db.commit()

    assert (await call("DELETE", f"/entities/{entity_id}", person=bob)).status_code == 404
    assert (await call("DELETE", f"/entities/{public_entity.id}")).status_code == 403
    response = await call("DELETE", f"/entities/{entity_id}")
    assert response.status_code == 204 and response.content == b""
    assert (await call("GET", f"/entities/{entity_id}")).status_code == 404
    for model in (Review, Import, Theme, AnalysisRun):
        assert await count(db, model, entity_id=entity_id) == 0, model
    assert await count(db, RunStep, run_id=first) == 0                      # steps go with their runs


async def test_entities_report_their_established_theme_count(call, seeded):
    # Service and Price are discovered; Parking, Valet and Rare are candidates; General is not counted.
    listed = (await call("GET", "/entities", params={"scope": "mine"})).json()
    assert [item["theme_count"] for item in listed if item["id"] == seeded.entity_id] == [2]
    assert (await call("GET", f"/entities/{seeded.entity_id}")).json()["theme_count"] == 2
    created = (await call("POST", "/entities", json={"name": "Fresh"})).json()
    assert created["theme_count"] == 0


async def test_the_frontend_origin_may_call_the_api(client):
    preflight = await client.options(
        "/entities",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = await client.options(
        "/entities", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in other.headers


async def test_ratings_on_different_scales_are_summarized_separately(upload, entity, call):
    entity_id = entity["id"]
    assert (await upload(entity_id, csv_bytes(10))).status_code == 201
    ten_point = json.dumps({"type": "numeric", "min": 1, "max": 10})
    assert (await upload(entity_id, csv_bytes(4, start=10), scale=ten_point)).status_code == 201
    unrated = json.dumps({"text": "review", "review_date": "when"})
    assert (await upload(entity_id, csv_bytes(3, start=20), mapping=unrated, scale=None)).status_code == 201

    body = (await call("GET", f"/entities/{entity_id}/ratings")).json()
    assert body["total_reviews"] == 17 and body["rated_reviews"] == 14
    five, ten = body["scales"]                     # most ratings first
    assert five["scale"]["max"] == 5 and five["count"] == 10
    assert ten["scale"]["max"] == 10 and ten["count"] == 4 and len(ten["buckets"]) == 10
