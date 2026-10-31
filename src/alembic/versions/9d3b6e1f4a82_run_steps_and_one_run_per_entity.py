"""run steps, a heartbeat, and one waiting and one running run per entity

Revision ID: 9d3b6e1f4a82
Revises: 7c1e4a9b2d35
Create Date: 2026-10-30

The worker runs one job per entity, `analyze`, which looks at the entity and does whatever it needs:
discovery, classification, consolidation, promotion. A job uses several models, so its cost is
recorded per step in a new table, `run_steps`. The table also records what a step did with its
result (`outcome`), which replaces the markers that were kept in `analysis_runs.error`.

- Existing runs each get one step. Consolidation runs, which were logged as `discover` runs marked
  in `error`, become `consolidate` runs, and the markers move to the step's outcome.
- `analysis_runs.heartbeat_at` lets the worker tell a running run from one whose worker stopped.
- The per-type unique index on queued runs becomes one waiting and one running run per entity.

The new table gets row level security like every other (see 415000255ba0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d3b6e1f4a82'
down_revision: Union[str, Sequence[str], None] = '7c1e4a9b2d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUPABASE_ROLES_EXIST = "EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')"

# The markers the scripts kept in analysis_runs.error, in all the wordings used so far.
CONSOLIDATION_MARKER = "error LIKE 'Consolidation%'"
CONSOLIDATION_APPLIED = "error = 'Consolidation applied.'"
CLASSIFY_DRY_RUN = "error LIKE '%Dry run: tags were not saved.%'"


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("reviews_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])
    op.execute("ALTER TABLE public.run_steps ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        DO $$
        BEGIN
            IF {SUPABASE_ROLES_EXIST} THEN
                REVOKE ALL ON public.run_steps FROM anon, authenticated;
                REVOKE ALL ON SEQUENCE public.run_steps_id_seq FROM anon, authenticated;
            END IF;
        END $$;
    """)

    # Consolidation runs were `discover` runs with a marker in `error`.
    op.execute(f"UPDATE analysis_runs SET type = 'consolidate' WHERE type = 'discover' AND {CONSOLIDATION_MARKER}")

    # One step for each existing run. The markers become outcomes and leave `error`.
    op.execute(f"""
        INSERT INTO run_steps
            (run_id, kind, status, model, outcome, reviews_processed, input_tokens, output_tokens,
             error, created_at, started_at, completed_at)
        SELECT
            r.id, r.type, r.status, r.model,
            CASE
                WHEN r.type = 'consolidate' AND r.{CONSOLIDATION_APPLIED} THEN 'applied'
                WHEN r.type = 'consolidate' AND r.status = 'succeeded' THEN 'proposed'
                WHEN r.type = 'classify' AND r.status = 'succeeded' AND r.{CLASSIFY_DRY_RUN} THEN 'dry run'
                WHEN r.type = 'classify' AND r.status = 'succeeded' THEN 'saved'
                WHEN r.type = 'discover' AND r.status = 'succeeded' THEN
                    CASE WHEN EXISTS (SELECT 1 FROM themes t WHERE t.discovered_in_run_id = r.id)
                         THEN 'saved' ELSE 'report only' END
            END,
            r.reviews_processed, r.input_tokens, r.output_tokens,
            CASE WHEN r.type = 'consolidate' AND r.{CONSOLIDATION_MARKER} THEN NULL ELSE r.error END,
            r.created_at, r.started_at, r.completed_at
        FROM analysis_runs r
    """)
    op.execute(f"UPDATE analysis_runs SET error = NULL WHERE type = 'consolidate' AND {CONSOLIDATION_MARKER}")

    op.drop_index("uq_analysis_runs_entity_type_queued", table_name="analysis_runs")
    op.create_index(
        "uq_analysis_runs_entity_queued", "analysis_runs", ["entity_id"],
        unique=True, postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "uq_analysis_runs_entity_running", "analysis_runs", ["entity_id"],
        unique=True, postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_analysis_runs_entity_running", table_name="analysis_runs")
    op.drop_index("uq_analysis_runs_entity_queued", table_name="analysis_runs")
    op.create_index(
        "uq_analysis_runs_entity_type_queued", "analysis_runs", ["entity_id", "type"],
        unique=True, postgresql_where=sa.text("status = 'queued'"),
    )
    # Put the markers back so the earlier scripts find the last applied consolidation.
    op.execute("""
        UPDATE analysis_runs r SET error = CASE s.outcome
            WHEN 'applied' THEN 'Consolidation applied.'
            ELSE 'Consolidation proposed, not applied.' END
        FROM run_steps s WHERE s.run_id = r.id AND r.type = 'consolidate' AND r.status = 'succeeded'
    """)
    op.execute("UPDATE analysis_runs SET type = 'discover' WHERE type = 'consolidate'")
    op.execute("DELETE FROM analysis_runs WHERE type = 'analyze'")
    op.drop_table("run_steps")
    op.drop_column("analysis_runs", "heartbeat_at")
