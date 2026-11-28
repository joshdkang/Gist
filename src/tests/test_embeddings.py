"""Review embeddings: batching, the Voyage client, and storing the vectors."""
import json

import httpx2
import pytest
from sqlalchemy import func, select

import embeddings
from embeddings import Embedder, EmbeddingError, batches, count_missing_embeddings, embed_missing_reviews, review_input
from models import ReviewEmbedding


def test_batches_respect_both_limits(monkeypatch):
    monkeypatch.setattr(embeddings, "MAX_INPUTS_PER_REQUEST", 3)
    monkeypatch.setattr(embeddings, "MAX_CHARACTERS_PER_REQUEST", 10)
    assert batches(["a", "b", "c", "d"]) == [[0, 1, 2], [3]]
    assert batches(["123456", "123456", "1"]) == [[0], [1, 2]]
    assert batches(["12345678901"]) == [[0]]  # one long text still gets a request of its own
    assert batches([]) == []


def test_review_input_puts_the_title_first_and_cuts_long_reviews():
    assert review_input("Great", "Loved it") == "Great\nLoved it"
    assert review_input(None, "Loved it") == "Loved it"
    assert len(review_input(None, "x" * 20_000)) == embeddings.MAX_REVIEW_CHARACTERS


def voyage(handler) -> Embedder:
    return Embedder("test-key", "voyage-test", http=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


async def test_the_client_sends_the_model_and_type_and_orders_the_vectors():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.headers["authorization"], body))
        data = [{"embedding": [float(index)], "index": index} for index in range(len(body["input"]))]
        return httpx2.Response(200, json={"data": list(reversed(data)), "usage": {"total_tokens": 7}})

    result = await voyage(handler).embed_documents(["one", "two"])
    assert result.vectors == [[0.0], [1.0]] and result.tokens == 7
    authorization, body = seen[0]
    assert authorization == "Bearer test-key"
    assert body["model"] == "voyage-test" and body["input_type"] == "document"
    assert body["output_dimension"] == 1024


async def test_a_rate_limit_is_retried_and_a_bad_request_is_not(monkeypatch):
    async def no_wait(_):
        pass

    monkeypatch.setattr(embeddings.asyncio, "sleep", no_wait)
    responses = [httpx2.Response(429, text="slow down"),
                 httpx2.Response(200, json={"data": [{"embedding": [1.0], "index": 0}], "usage": {"total_tokens": 1}})]
    assert (await voyage(lambda request: responses.pop(0)).embed_documents(["x"])).vectors == [[1.0]]

    calls = []

    def bad(request):
        calls.append(request)
        return httpx2.Response(400, text="bad input")

    with pytest.raises(EmbeddingError, match="400"):
        await voyage(bad).embed_documents(["x"])
    assert len(calls) == 1


async def test_a_query_is_embedded_once_and_cached():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["input_type"])
        return httpx2.Response(200, json={"data": [{"embedding": [0.5], "index": 0}], "usage": {"total_tokens": 1}})

    embedder = voyage(handler)
    assert await embedder.embed_query("slow service") == [0.5]
    assert await embedder.embed_query("slow service") == [0.5]
    assert calls == ["query"]


async def test_missing_reviews_are_embedded_once_and_a_new_model_replaces_the_old(db, seeded, fake_embedder):
    assert await count_missing_embeddings(db, seeded.entity_id, fake_embedder.model) == 40
    embedded, tokens = await embed_missing_reviews(db, fake_embedder, seeded.entity_id, chunk=15)
    assert (embedded, tokens) == (40, 40)
    assert [len(texts) for _, texts in fake_embedder.calls] == [15, 15, 10]
    assert await count_missing_embeddings(db, seeded.entity_id, fake_embedder.model) == 0
    assert await embed_missing_reviews(db, fake_embedder, seeded.entity_id) == (0, 0)

    stored = await db.scalar(select(ReviewEmbedding).where(ReviewEmbedding.review_id == seeded.review_ids[0]))
    assert len(stored.embedding) == 1024 and stored.entity_id == seeded.entity_id

    fake_embedder.model = "fake-embedder-2"
    assert await count_missing_embeddings(db, seeded.entity_id, fake_embedder.model) == 40
    await embed_missing_reviews(db, fake_embedder, seeded.entity_id)
    rows = await db.scalar(select(func.count()).select_from(ReviewEmbedding).where(ReviewEmbedding.entity_id == seeded.entity_id))
    assert rows == 40  # replaced, not added beside the old vectors
