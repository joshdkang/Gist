"""rename review_themes note to theme_title

Revision ID: 200f5246be61
Revises: 5f2b08dd23dd
Create Date: 2026-10-16 17:13:28.499107

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '200f5246be61'
down_revision: Union[str, Sequence[str], None] = '5f2b08dd23dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('review_themes', 'note', new_column_name='theme_title')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('review_themes', 'theme_title', new_column_name='note')
