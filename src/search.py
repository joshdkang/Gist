"""Full-text search over reviews, as a filter that combines with the rating and period filters.

Postgres full-text search with the English configuration, so a search for "waited" also finds "wait"
and "waiting", and `websearch_to_tsquery` reads the box the way people type into a search engine:
words are all required, "quoted phrases" are matched in order, `or` gives alternatives and a leading
`-` excludes a word. The review's title and text are searched together.

In `meaning` mode (`review_search`), a review also matches when it is close in meaning to the
search: its embedding's cosine similarity to the search's is at least MEANING_Z standard deviations
above the mean similarity of all the entity's reviews to it, and at least MEANING_MIN_SIMILARITY. The cutoff is relative because the
scores of one query sit higher or lower than another's, and meaning adds to the keywords rather than
replacing them, because on dish names and other exact items the words are the better search (see
"Search by meaning" in docs/decisions.md for the calibration on the labelled sets).

The document expression here must stay identical to the one the GIN index is built on (migration
c7d2e9f4a1b6), or Postgres can't use the index and every search scans the entity's reviews. The
constants are literal SQL rather than bound parameters for the same reason.
"""
import enum

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, exists, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from embeddings import Embedder, EmbeddingError
from models import Review, ReviewEmbedding

# The longest search accepted, in characters: a search box, not a document.
MAX_SEARCH_LENGTH = 200

SEARCH_CONFIG = literal_column("'english'::regconfig")

# How far above the mean similarity of the entity's reviews a review must be to match by meaning, in
# standard deviations. Chosen on the dev sets with evals/search_cutoff.py (mean F1 is flat from about
# 2.0 to 2.6).
MEANING_Z = 2.3
# And how close it must be at least, whatever the spread. A relative cutoff always admits the top one
# or two percent, so a search for something few reviews mention ("parking" at a restaurant) drifted
# into loosely related reviews (getting there, the neighbourhood) below about 0.28, while the weakest
# matches of a common topic ("slow service") were still on it. The labelled sets can't measure this:
# their searches are topics the reviews raise. On them the floor costs half a point of F1.
MEANING_MIN_SIMILARITY = 0.28


class SearchMode(enum.StrEnum):
    WORDS = "words"      # the search's words, as keyword search reads them
    MEANING = "meaning"  # the words, or close in meaning


def review_document() -> ColumnElement:
    """A review's title and text as one searchable document (the indexed expression)."""
    text = func.coalesce(Review.title, literal_column("''")).op("||")(literal_column("' '")).op("||")(Review.text)
    return func.to_tsvector(SEARCH_CONFIG, text)


def search_filter(query: str | None) -> ColumnElement[bool] | None:
    """The condition for reviews matching `query`, or None when there is nothing to search for.
    422 for a search longer than MAX_SEARCH_LENGTH. A search made only of stop words ("the") or
    punctuation matches nothing, as Postgres reads it as an empty query."""
    query = (query or "").strip()
    if not query:
        return None
    if len(query) > MAX_SEARCH_LENGTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Search for at most {MAX_SEARCH_LENGTH} characters."
        )
    return review_document().op("@@")(func.websearch_to_tsquery(SEARCH_CONFIG, query))


async def meaning_filter(
    db: AsyncSession, entity_id: int, model: str, vector: list[float]
) -> ColumnElement[bool] | None:
    """The condition for the entity's reviews close in meaning to `vector`, or None when none of its
    reviews has an embedding from `model`.

    The matching reviews are found in one query (every embedded review's similarity, with their mean
    and spread over the entity as window functions) and the condition is a list of their IDs: a page
    request counts the same filter in several queries, and comparing IDs is cheaper than comparing
    every review's vector again in each.
    """
    similarity = (1 - ReviewEmbedding.embedding.cosine_distance(vector)).label("similarity")
    scored = (
        select(ReviewEmbedding.review_id, similarity)
        .where(ReviewEmbedding.entity_id == entity_id, ReviewEmbedding.model == model)
        .subquery()
    )
    with_spread = select(
        scored.c.review_id,
        scored.c.similarity,
        func.avg(scored.c.similarity).over().label("mean"),
        func.stddev_pop(scored.c.similarity).over().label("spread"),
    ).subquery()
    ids = list(
        await db.scalars(
            select(with_spread.c.review_id).where(
                with_spread.c.similarity >= with_spread.c.mean + MEANING_Z * with_spread.c.spread,
                with_spread.c.similarity >= MEANING_MIN_SIMILARITY,
            )
        )
    )
    if not ids and not await db.scalar(
        select(exists().where(ReviewEmbedding.entity_id == entity_id, ReviewEmbedding.model == model))
    ):
        return None
    return Review.id.in_(ids)


async def review_search(
    db: AsyncSession, entity_id: int, query: str | None, mode: "SearchMode", embedder: Embedder | None
) -> ColumnElement[bool] | None:
    """The search condition for a request: the keywords, or in meaning mode the keywords or close in
    meaning. 503 when meaning is asked for and embeddings aren't configured or Voyage can't be
    reached, and 409 while none of the entity's reviews are embedded. Reviews uploaded since the last
    embedding are found by their words until the worker embeds them."""
    words = search_filter(query)
    if words is None or mode == SearchMode.WORDS:
        return words
    if embedder is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search by meaning isn't configured on this server.")
    try:
        vector = await embedder.embed_query(query.strip())
    except EmbeddingError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search by meaning is unavailable; try again shortly.")
    close = await meaning_filter(db, entity_id, embedder.model, vector)
    if close is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This entity's reviews aren't indexed for search by meaning yet. Search by words, or try again after its next analysis.",
        )
    return or_(words, close)
