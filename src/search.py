"""Full-text search over reviews, as a filter that combines with the rating and period filters.

Postgres full-text search with the English configuration, so a search for "waited" also finds "wait"
and "waiting", and `websearch_to_tsquery` reads the box the way people type into a search engine:
words are all required, "quoted phrases" are matched in order, `or` gives alternatives and a leading
`-` excludes a word. The review's title and text are searched together.

The document expression here must stay identical to the one the GIN index is built on (migration
c7d2e9f4a1b6), or Postgres can't use the index and every search scans the entity's reviews. The
constants are literal SQL rather than bound parameters for the same reason.
"""
from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, literal_column

from models import Review

# The longest search accepted, in characters: a search box, not a document.
MAX_SEARCH_LENGTH = 200

SEARCH_CONFIG = literal_column("'english'::regconfig")


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
