"""candidate themes replace other

Revision ID: de22bf45d938
Revises: 200f5246be61
Create Date: 2026-10-26

Replaces the single `other` theme per entity with `candidate` themes: one themes row per
uncovered point the classifier found. Candidates can later be consolidated (merged_into_id,
merged_in_run_id) and promoted to `discovered`.

The data conversion turns every `other` tag that has a title into a tag on a candidate theme
named after the (normalized) title, then deletes the `other` tags and themes and drops
review_themes.theme_title. It is not reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de22bf45d938'
down_revision: Union[str, Sequence[str], None] = '200f5246be61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# lower-case, trimmed, whitespace collapsed
NORMALIZED_TITLE = r"lower(btrim(regexp_replace(theme_title, '\s+', ' ', 'g')))"
NORMALIZED_NAME = r"lower(btrim(regexp_replace(c.name, '\s+', ' ', 'g')))"


def upgrade() -> None:
    connection = op.get_bind()

    op.add_column('themes', sa.Column('merged_into_id', sa.Integer(), nullable=True))
    op.add_column('themes', sa.Column('merged_in_run_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_themes_merged_into_id', 'themes', 'themes', ['merged_into_id'], ['id'], ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_themes_merged_in_run_id', 'themes', 'analysis_runs', ['merged_in_run_id'], ['id'],
        ondelete='SET NULL',
    )
    # An entity still has exactly one `general` theme, but any number of candidates.
    op.drop_index(
        'uq_themes_entity_system_kind', table_name='themes', postgresql_where=sa.text("kind <> 'discovered'")
    )
    op.create_index(
        'uq_themes_entity_general', 'themes', ['entity_id'], unique=True,
        postgresql_where=sa.text("kind = 'general'"),
    )

    _convert_other_tags(connection)

    # One theme name per entity, whatever its kind. Created after the conversion so the old
    # `other` rows are gone.
    op.create_index(
        'uq_themes_entity_name', 'themes', ['entity_id', sa.text('lower(name)')], unique=True
    )
    op.drop_column('review_themes', 'theme_title')


def _convert_other_tags(connection) -> None:
    tagged_before = connection.execute(
        sa.text("SELECT COUNT(DISTINCT review_id) FROM review_themes")
    ).scalar_one()

    # Reviews that had an `other` tag, so none can end up with no tag at all.
    connection.execute(sa.text("""
        CREATE TEMP TABLE other_reviews ON COMMIT DROP AS
        SELECT rt.review_id, t.entity_id, rt.run_id
        FROM review_themes rt JOIN themes t ON t.id = rt.theme_id
        WHERE t.kind = 'other'
    """))
    connection.execute(sa.text(f"""
        CREATE TEMP TABLE other_titles ON COMMIT DROP AS
        SELECT rt.review_id, t.entity_id, rt.sentiment, rt.run_id,
               {NORMALIZED_TITLE} AS key, btrim(rt.theme_title) AS raw_title
        FROM review_themes rt JOIN themes t ON t.id = rt.theme_id
        WHERE t.kind = 'other' AND btrim(COALESCE(rt.theme_title, '')) <> ''
    """))
    # One candidate per entity and normalized title, named after its most common spelling. A title
    # that matches an existing theme's name maps to that theme instead of creating a candidate.
    connection.execute(sa.text("""
        CREATE TEMP TABLE title_targets ON COMMIT DROP AS
        SELECT g.entity_id, g.key, g.name, g.first_run,
               (SELECT t.id FROM themes t
                 WHERE t.entity_id = g.entity_id AND lower(t.name) = g.key AND t.kind <> 'other'
                 LIMIT 1) AS existing_id
        FROM (
            SELECT entity_id, key, mode() WITHIN GROUP (ORDER BY raw_title) AS name, MIN(run_id) AS first_run
            FROM other_titles GROUP BY entity_id, key
        ) g
    """))
    connection.execute(sa.text("""
        INSERT INTO themes (entity_id, kind, name, description, discovered_in_run_id)
        SELECT entity_id, 'candidate', name,
               'Candidate theme from uncovered points titled "' || name || '".', first_run
        FROM title_targets WHERE existing_id IS NULL
    """))
    connection.execute(sa.text(f"""
        INSERT INTO review_themes (review_id, theme_id, sentiment, run_id)
        SELECT o.review_id, COALESCE(tt.existing_id, c.id), o.sentiment, o.run_id
        FROM other_titles o
        JOIN title_targets tt ON tt.entity_id = o.entity_id AND tt.key = o.key
        LEFT JOIN themes c ON tt.existing_id IS NULL AND c.entity_id = tt.entity_id
                          AND c.kind = 'candidate' AND {NORMALIZED_NAME} = tt.key
        ON CONFLICT (review_id, theme_id) DO NOTHING
    """))

    connection.execute(sa.text("""
        DELETE FROM review_themes USING themes t
        WHERE review_themes.theme_id = t.id AND t.kind = 'other'
    """))
    # Any review left with no tag (an `other` tag without a title) goes to `general`.
    connection.execute(sa.text("""
        INSERT INTO review_themes (review_id, theme_id, sentiment, run_id)
        SELECT o.review_id, g.id, NULL, o.run_id
        FROM other_reviews o JOIN themes g ON g.entity_id = o.entity_id AND g.kind = 'general'
        WHERE NOT EXISTS (SELECT 1 FROM review_themes rt WHERE rt.review_id = o.review_id)
    """))
    connection.execute(sa.text("DELETE FROM themes WHERE kind = 'other'"))

    # Stop (rolling the whole migration back) rather than lose a review's tags.
    tagged_after = connection.execute(
        sa.text("SELECT COUNT(DISTINCT review_id) FROM review_themes")
    ).scalar_one()
    if tagged_after != tagged_before:
        raise RuntimeError(
            f"Conversion changed the number of tagged reviews: {tagged_before} before, {tagged_after} after."
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Converting `other` tags to candidate themes can't be reversed: the `other` themes and "
        "their tags are gone."
    )
