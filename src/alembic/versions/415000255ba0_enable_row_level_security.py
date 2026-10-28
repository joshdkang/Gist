"""enable row level security

Revision ID: 415000255ba0
Revises: de22bf45d938
Create Date: 2026-10-27

Supabase exposes every table in the public schema through its Data API (PostgREST), and grants
the `anon` and `authenticated` roles full access to new tables. With row level security off, anyone
holding the project's public anon key could read, change, or delete every row, including users'
email addresses.

This turns row level security on for every table and removes the two roles' privileges. No
policies are added, so those roles can't touch the tables at all. The backend is unaffected: it
connects as `postgres`, which has BYPASSRLS, and does its own authorization in FastAPI.

Every migration that creates a table must do the same. `python check_rls.py` verifies it.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '415000255ba0'
down_revision: Union[str, Sequence[str], None] = 'de22bf45d938'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "alembic_version",
    "users",
    "entities",
    "imports",
    "reviews",
    "themes",
    "analysis_runs",
    "review_themes",
]

# The Supabase roles exist only on Supabase, so skip the privilege changes elsewhere (for example
# a local Postgres in docker-compose or CI).
SUPABASE_ROLES_EXIST = "EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')"


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        DO $$
        BEGIN
            IF {SUPABASE_ROLES_EXIST} THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"""
        DO $$
        BEGIN
            IF {SUPABASE_ROLES_EXIST} THEN
                GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;
                GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
            END IF;
        END $$;
    """)
