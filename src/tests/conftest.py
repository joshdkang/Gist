"""Fixtures for the tests that touch the API and the database.

These run against the database in DATABASE_URL, inside a transaction that is rolled back at the end
of each test, so nothing is left behind. Sign-in uses tokens signed with a key generated for the run,
so no Supabase request is made.
"""
import time
import types
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
from database import engine, get_db
from entities import create_entity
from main import app
from models import (
    AnalysisRun,
    Import,
    ImportStatus,
    Review,
    ReviewTheme,
    RunStatus,
    RunType,
    Sentiment,
    Theme,
    ThemeKind,
)

ISSUER = "https://test-project.supabase.co/auth/v1"


@pytest.fixture(scope="session")
def signing_key():
    return ec.generate_private_key(ec.SECP256R1())


class StubKeys:
    def __init__(self, private_key):
        self.private_key = private_key

    def get_signing_key_from_jwt(self, token):
        return types.SimpleNamespace(key=self.private_key.public_key())


@dataclass
class Person:
    id: uuid.UUID
    email: str
    headers: dict[str, str]


@pytest.fixture
def make_person(signing_key):
    def make(name: str) -> Person:
        person_id, email, now = uuid.uuid4(), f"{name}@example.com", int(time.time())
        token = jwt.encode(
            {"iss": ISSUER, "aud": "authenticated", "sub": str(person_id), "email": email,
             "iat": now, "exp": now + 3600},
            signing_key, algorithm="ES256", headers={"kid": "test"},
        )
        return Person(person_id, email, {"Authorization": f"Bearer {token}"})
    return make


@pytest.fixture
def alice(make_person) -> Person:
    return make_person("alice")


@pytest.fixture
def bob(make_person) -> Person:
    return make_person("bob")


@pytest_asyncio.fixture
async def db():
    """A session inside a transaction that is rolled back; the app's commits only release savepoints."""
    async with engine.connect() as connection:
        outer = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        yield session
        await session.close()
        await outer.rollback()


@pytest_asyncio.fixture
async def client(db, signing_key):
    verifier = auth.TokenVerifier(ISSUER, "unused", StubKeys(signing_key))

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[auth.get_verifier] = lambda: verifier
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest.fixture
def call(client, alice):
    """call(method, path, person=alice, **kwargs): a request signed in as that person."""
    async def send(method: str, path: str, person: Person | None = alice, **kwargs):
        headers = person.headers if person else {}
        return await client.request(method, path, headers=headers, **kwargs)
    return send


# --- seeded results -----------------------------------------------------------------------------

SEED_START = datetime(2020, 1, 15, tzinfo=UTC)
SEED_REVIEWS = 40


def seed_date(index: int) -> datetime:
    return SEED_START + timedelta(days=30 * index)


@dataclass
class Seeded:
    """An entity with 40 dated reviews, all tagged:

    - reviews 0-29: Service (every third one negative, the rest positive)
    - reviews 0-9: also Price (positive)
    - reviews 20-31: Parking, a candidate raised by enough reviews to be listed (neutral)
    - reviews 20 and 32-34: also Valet, a candidate merged into Parking (review 20 positive,
      the others negative), so Parking has 15 reviews. Review 20 is positive for Parking (neutral
      and positive), and review 32 is mixed (it also has a positive Parking tag next to Valet's
      negative one).
    - reviews 36-39: Rare, a candidate raised by too few reviews to be listed
    - review 35: General
    """
    entity_id: int
    review_ids: list[int]
    service: Theme
    price: Theme
    parking: Theme
    valet: Theme
    rare: Theme
    general: Theme


async def seed_results(db: AsyncSession, entity_id: int) -> Seeded:
    run = AnalysisRun(entity_id=entity_id, type=RunType.CLASSIFY, status=RunStatus.SUCCEEDED)
    data_import = Import(
        entity_id=entity_id, filename="seed.csv", source="csv", status=ImportStatus.COMPLETED
    )
    db.add_all([run, data_import])
    await db.flush()
    reviews = [
        Review(
            entity_id=entity_id, import_id=data_import.id, content_hash=f"seed-{index}",
            text=f"Seed review {index}", rating=(index % 5) + 1, review_date=seed_date(index),
        )
        for index in range(SEED_REVIEWS)
    ]
    db.add_all(reviews)

    def theme(name: str, kind: ThemeKind) -> Theme:
        return Theme(entity_id=entity_id, kind=kind, name=name, description=f"About {name.lower()}.")

    service, price = theme("Service", ThemeKind.DISCOVERED), theme("Price", ThemeKind.DISCOVERED)
    parking, valet = theme("Parking", ThemeKind.CANDIDATE), theme("Valet", ThemeKind.CANDIDATE)
    rare = theme("Rare", ThemeKind.CANDIDATE)
    db.add_all([service, price, parking, valet, rare])
    await db.flush()
    valet.merged_into_id = parking.id
    general = await db.scalar(
        select(Theme).where(Theme.entity_id == entity_id, Theme.kind == ThemeKind.GENERAL)
    )

    tags: list[tuple[int, Theme, Sentiment]] = []
    for index in range(30):
        tags.append((index, service, Sentiment.NEGATIVE if index % 3 == 0 else Sentiment.POSITIVE))
    for index in range(10):
        tags.append((index, price, Sentiment.POSITIVE))
    for index in range(20, 32):
        tags.append((index, parking, Sentiment.NEUTRAL))
    tags.append((20, valet, Sentiment.POSITIVE))
    for index in (32, 33, 34):
        tags.append((index, valet, Sentiment.NEGATIVE))
    tags.append((32, parking, Sentiment.POSITIVE))
    for index in range(36, 40):
        tags.append((index, rare, Sentiment.NEUTRAL))
    tags.append((35, general, Sentiment.NEUTRAL))
    db.add_all(
        ReviewTheme(review_id=reviews[index].id, theme_id=tagged.id, sentiment=sentiment, run_id=run.id)
        for index, tagged, sentiment in tags
    )
    await db.commit()
    return Seeded(entity_id, [review.id for review in reviews], service, price, parking, valet, rare, general)


@pytest_asyncio.fixture
async def seeded(db, client, call) -> Seeded:
    """Alice's entity, created through the API, with results seeded directly."""
    response = await call("POST", "/entities", json={"name": "Seeded"})
    assert response.status_code == 201
    return await seed_results(db, response.json()["id"])


@pytest_asyncio.fixture
async def public_entity(db):
    """A public sample entity with no owner, like the Yelp restaurants."""
    entity = await create_entity(db, name="Public sample", is_public=True)
    await db.commit()
    return entity
