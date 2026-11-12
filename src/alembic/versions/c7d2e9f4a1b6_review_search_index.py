"""Full-text search index on reviews

Revision ID: c7d2e9f4a1b6
Revises: b2f5a8c1d704
Create Date: 2026-11-12

A GIN index on each review's title and text as an English tsvector, so searching an entity's
reviews (search.py) doesn't scan them all. The expression must match `review_document` in
search.py exactly. No table is created, so there is no row level security to enable.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c7d2e9f4a1b6"
down_revision: Union[str, Sequence[str], None] = "b2f5a8c1d704"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_reviews_search ON reviews USING gin "
        "(to_tsvector('english'::regconfig, coalesce(title, '') || ' ' || text))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_reviews_search")
