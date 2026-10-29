"""one queued run per entity and type

Revision ID: 7c1e4a9b2d35
Revises: 415000255ba0
Create Date: 2026-10-29

An upload that finds a run of the same type already waiting reuses it instead of queueing another,
because a run classifies every unprocessed review when it starts, whichever upload added it. This
partial unique index makes that safe when two uploads arrive together: the second insert fails and
the endpoint returns the first upload's run. It adds no table, so there is nothing new to protect
with row level security.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7c1e4a9b2d35'
down_revision: Union[str, Sequence[str], None] = '415000255ba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_analysis_runs_entity_type_queued",
        "analysis_runs",
        ["entity_id", "type"],
        unique=True,
        postgresql_where="status = 'queued'",
    )


def downgrade() -> None:
    op.drop_index("uq_analysis_runs_entity_type_queued", table_name="analysis_runs")
