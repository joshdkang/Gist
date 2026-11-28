"""Review embeddings from Voyage AI, stored in pgvector (`review_embeddings`).

Anthropic has no embeddings API; Voyage is the provider it recommends. The API is called over HTTP
with httpx2 (which the Anthropic SDK already uses) rather than Voyage's SDK, which brings tokenizers,
langchain and pillow for one POST request.

A document (a review) and a query (a search) are embedded with different `input_type`s, which
Voyage uses to place a short query near the documents that answer it. Each vector is stored with the
model that made it, and only vectors of the configured model are read, so changing the model means
embedding again rather than mixing vectors that can't be compared.

`get_embedder` is the FastAPI dependency; tests replace it with a deterministic fake.
"""
import asyncio
import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

import httpx2
from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import EMBEDDING_DIMENSIONS, Review, ReviewEmbedding

logger = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
# Voyage accepts up to 1,000 inputs and, for the lite model, 1M tokens per request. A request is
# kept well under both, so a long review set never hits the limit and a failed request is cheap
# to retry. Tokens are estimated at four characters each, which overestimates for English.
MAX_INPUTS_PER_REQUEST = 500
MAX_CHARACTERS_PER_REQUEST = 400_000
# A review longer than this is cut before it is sent (the model reads 32,000 tokens; reviews are
# rarely a twentieth of that). Voyage would truncate anyway; cutting here keeps requests small.
MAX_REVIEW_CHARACTERS = 8_000
MAX_ATTEMPTS = 5
QUERY_CACHE_SIZE = 256
WRITE_BATCH = 500


class EmbeddingError(RuntimeError):
    """Voyage couldn't be reached or refused the request."""


@dataclass
class Embedded:
    vectors: list[list[float]]
    tokens: int


def review_input(title: str | None, text: str) -> str:
    """What is embedded for a review: its title and text, as search reads them."""
    document = f"{title}\n{text}" if title else text
    return document[:MAX_REVIEW_CHARACTERS]


def batches(texts: Sequence[str]) -> list[list[int]]:
    """Positions of `texts`, grouped into requests under both per-request limits."""
    groups: list[list[int]] = []
    current: list[int] = []
    characters = 0
    for position, text in enumerate(texts):
        if current and (len(current) >= MAX_INPUTS_PER_REQUEST or characters + len(text) > MAX_CHARACTERS_PER_REQUEST):
            groups.append(current)
            current, characters = [], 0
        current.append(position)
        characters += len(text)
    if current:
        groups.append(current)
    return groups


class Embedder:
    """Embeds documents and queries with one Voyage model."""

    def __init__(self, api_key: str, model: str, http: httpx2.AsyncClient | None = None):
        self.model = model
        self.http = http or httpx2.AsyncClient(timeout=httpx2.Timeout(60.0, connect=10.0))
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.query_cache: OrderedDict[str, list[float]] = OrderedDict()

    async def request(self, texts: list[str], input_type: str) -> Embedded:
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": EMBEDDING_DIMENSIONS,
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self.http.post(VOYAGE_URL, json=payload, headers=self.headers)
            except httpx2.TransportError as error:
                failure = f"Voyage couldn't be reached: {error}"
            else:
                if response.status_code == 200:
                    body = response.json()
                    ordered = sorted(body["data"], key=lambda item: item["index"])
                    return Embedded([item["embedding"] for item in ordered], body["usage"]["total_tokens"])
                failure = f"Voyage returned {response.status_code}: {response.text[:300]}"
                if response.status_code != 429 and response.status_code < 500:
                    raise EmbeddingError(failure)  # a bad request or key won't improve on retrying
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(min(2 ** attempt, 30))
        raise EmbeddingError(failure)

    async def embed_documents(self, texts: Sequence[str]) -> Embedded:
        vectors: list[list[float]] = [[] for _ in texts]
        tokens = 0
        for group in batches(texts):
            result = await self.request([texts[position] for position in group], "document")
            for position, vector in zip(group, result.vectors):
                vectors[position] = vector
            tokens += result.tokens
        return Embedded(vectors, tokens)

    async def embed_query(self, query: str) -> list[float]:
        """A search's vector. Cached, because one page asks several endpoints for the same search."""
        if query in self.query_cache:
            self.query_cache.move_to_end(query)
            return self.query_cache[query]
        vector = (await self.request([query], "query")).vectors[0]
        self.query_cache[query] = vector
        if len(self.query_cache) > QUERY_CACHE_SIZE:
            self.query_cache.popitem(last=False)
        return vector


_embedder: Embedder | None = None


def configured_embedder() -> Embedder | None:
    """The process's embedder, or None when no Voyage key is configured."""
    global _embedder
    if _embedder is None and settings.voyage_api_key is not None:
        _embedder = Embedder(settings.voyage_api_key.get_secret_value(), settings.embedding_model)
    return _embedder


def get_embedder() -> Embedder | None:
    """FastAPI dependency: the embedder, or None when embeddings aren't configured (search.py
    answers 503 then, and only to a request that needs it)."""
    return configured_embedder()


def missing_embedding(model: str):
    """The condition for a review with no vector from `model`."""
    return ~exists().where(ReviewEmbedding.review_id == Review.id, ReviewEmbedding.model == model)


async def count_missing_embeddings(db: AsyncSession, entity_id: int, model: str) -> int:
    return await db.scalar(
        select(func.count()).select_from(Review).where(Review.entity_id == entity_id, missing_embedding(model))
    )


async def embed_missing_reviews(
    db: AsyncSession, embedder: Embedder, entity_id: int, *, chunk: int = 2_000
) -> tuple[int, int]:
    """Embed the entity's reviews that have no vector from the embedder's model, a chunk at a time
    (each saved as it finishes, so an interrupted run resumes). A vector of an older model is
    replaced. Returns (reviews embedded, tokens)."""
    embedded = tokens = 0
    while True:
        rows = (
            await db.execute(
                select(Review.id, Review.title, Review.text)
                .where(Review.entity_id == entity_id, missing_embedding(embedder.model))
                .order_by(Review.id)
                .limit(chunk)
            )
        ).all()
        if not rows:
            return embedded, tokens
        result = await embedder.embed_documents([review_input(title, text) for _, title, text in rows])
        ids = [review_id for review_id, _, _ in rows]
        # An older model's vectors for these reviews are replaced, not kept beside the new ones.
        await db.execute(delete(ReviewEmbedding).where(ReviewEmbedding.review_id.in_(ids)))
        records = [
            {"review_id": review_id, "entity_id": entity_id, "model": embedder.model, "embedding": vector}
            for review_id, vector in zip(ids, result.vectors)
        ]
        for start in range(0, len(records), WRITE_BATCH):
            await db.execute(insert(ReviewEmbedding), records[start : start + WRITE_BATCH])
        await db.commit()
        embedded += len(rows)
        tokens += result.tokens
        logger.info("Entity %s: embedded %s reviews (%s tokens so far)", entity_id, embedded, tokens)
