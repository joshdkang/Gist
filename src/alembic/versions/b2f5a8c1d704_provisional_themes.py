"""provisional themes

Revision ID: b2f5a8c1d704
Revises: 9d3b6e1f4a82
Create Date: 2026-11-02

Discovery judges its themes from a sample that over-represents rare ratings, so the themes it saves
are provisional until the first classification has tagged every review. `themes.provisional` marks
them. The `confirm` step then counts each one's real share and demotes the ones too few reviews
raise to candidates. Existing themes are not provisional: they were already classified.

No new table, so there is nothing to add to row level security.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2f5a8c1d704'
down_revision: Union[str, Sequence[str], None] = '9d3b6e1f4a82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "themes", sa.Column("provisional", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("themes", "provisional")
