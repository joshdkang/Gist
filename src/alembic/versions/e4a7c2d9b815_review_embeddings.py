"""review and theme embeddings (pgvector)

Revision ID: e4a7c2d9b815
Revises: c7d2e9f4a1b6
Create Date: 2026-11-27

One embedding per review (`review_embeddings`), for search by meaning and the features built on it,
and one per theme name (`theme_embeddings`), for matching a new candidate title to an existing
candidate. Both are made by Voyage AI (embeddings.py) and have 1024 dimensions.

The `vector` type comes from the pgvector extension, created in Supabase's `extensions` schema,
which is on the search path. There is no approximate nearest-neighbour index: a search is always
within one entity (at most about 11,000 reviews), and an exact scan of those, found through the
`entity_id` index, takes milliseconds and is never wrong. Add HNSW if entities grow much larger.

Both tables get row level security like every other (see 415000255ba0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "e4a7c2d9b815"
down_revision: Union[str, Sequence[str], None] = "c7d2e9f4a1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUPABASE_ROLES_EXIST = "EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')"
DIMENSIONS = 1024


def create_embedding_table(name: str, key: str, target: str) -> None:
    op.create_table(
        name,
        sa.Column(key, sa.Integer(), sa.ForeignKey(f"{target}.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("embedding", Vector(DIMENSIONS), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(f"ix_{name}_entity_id", name, ["entity_id"])
    op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        DO $$
        BEGIN
            IF {SUPABASE_ROLES_EXIST} THEN
                REVOKE ALL ON public.{name} FROM anon, authenticated;
            END IF;
        END $$;
    """)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")
    create_embedding_table("review_embeddings", "review_id", "reviews")
    create_embedding_table("theme_embeddings", "theme_id", "themes")


def downgrade() -> None:
    op.drop_table("theme_embeddings")
    op.drop_table("review_embeddings")
    # The extension stays: dropping it could break anything else in the project that uses it.
