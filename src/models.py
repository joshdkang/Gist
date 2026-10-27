import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ImportStatus(enum.StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RunType(enum.StrEnum):
    DISCOVER = "discover"
    CLASSIFY = "classify"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ThemeKind(enum.StrEnum):
    DISCOVERED = "discovered"  # an established theme for this entity
    CANDIDATE = "candidate"    # an uncovered point the classifier found; may be merged or promoted
    GENERAL = "general"        # system theme: no discernible topic ("great", "would recommend")


class Sentiment(enum.StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"


def string_enum(enum_class: type[enum.StrEnum]) -> Enum:
    """Store enums as plain strings (not native Postgres enums) so adding a value needs no ALTER TYPE."""
    return Enum(
        enum_class,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        length=32,
    )


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """Mirrors a Supabase Auth user. The id is the Supabase user UUID, created on first sight of a valid JWT."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()


class Entity(Base):
    """The thing being reviewed (a restaurant, an app, a product). Public sample entities have no owner."""
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = created_at_column()


class Import(Base):
    """One row per file upload. Later imports add reviews to the same entity."""
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str | None]
    source: Mapped[str]  # adapter name, e.g. "csv" or "yelp"
    # How to read `rating` for every review in this file, supplied by the uploader, e.g.
    # {"type": "numeric", "min": 1, "max": 5}, {"type": "binary"}, or
    # {"type": "labels", "labels": ["bad", "ok", "good"]}. Null when the file has no ratings.
    rating_scale: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ImportStatus] = mapped_column(
        string_enum(ImportStatus), default=ImportStatus.PROCESSING
    )
    rows_received: Mapped[int] = mapped_column(default=0)
    rows_inserted: Mapped[int] = mapped_column(default=0)
    rows_skipped: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        # Re-uploading the same file must not duplicate reviews. The index also serves entity_id lookups.
        UniqueConstraint("entity_id", "content_hash", name="uq_reviews_entity_content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    import_id: Mapped[int] = mapped_column(
        ForeignKey("imports.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str | None]
    content_hash: Mapped[str]  # hash of normalized text plus review date
    text: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None]
    # Numeric value on the scale declared by the review's import (the label's index for ordered labels).
    rating: Mapped[Decimal | None] = mapped_column(Numeric)
    review_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )


class Theme(Base):
    __tablename__ = "themes"
    __table_args__ = (
        # Exactly one `general` theme per entity, but any number of candidates.
        Index(
            "uq_themes_entity_general",
            "entity_id",
            unique=True,
            postgresql_where=sql_text("kind = 'general'"),
        ),
        # One theme name per entity, whatever the kind and case.
        Index("uq_themes_entity_name", "entity_id", sql_text("lower(name)"), unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str] = mapped_column(Text)
    # Each entity has exactly one "general" theme, created by code. The classifier creates a
    # "candidate" for each uncovered point it finds. Consolidation merges overlapping candidates,
    # and promotion turns a candidate that recurs enough into a "discovered" theme. Themes are never
    # deleted or retired: they accumulate.
    kind: Mapped[ThemeKind] = mapped_column(
        string_enum(ThemeKind),
        default=ThemeKind.DISCOVERED,
        server_default=ThemeKind.DISCOVERED.value,
    )
    # The run that created this theme (discovery, classification, or consolidation). Null for general.
    discovered_in_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )
    # Set when consolidation folded this candidate into another theme. Tags stay on this row, and
    # counts follow merged_into_id. Always points at a root (a theme that isn't itself merged), and
    # clearing it undoes the merge. merged_in_run_id records the consolidation run.
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("themes.id", ondelete="SET NULL"))
    merged_in_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )


class AnalysisRun(Base):
    """Doubles as the job queue (status = queued) and the cost log (token counts)."""
    __tablename__ = "analysis_runs"
    __table_args__ = (
        # Lets the worker find the oldest queued job quickly.
        Index("ix_analysis_runs_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    type: Mapped[RunType] = mapped_column(string_enum(RunType))
    status: Mapped[RunStatus] = mapped_column(string_enum(RunStatus), default=RunStatus.QUEUED)
    model: Mapped[str | None]  # set when the worker picks a model
    reviews_processed: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewTheme(Base):
    """One row per (review, theme). A review that fits no established theme gets rows for candidate
    themes (one per uncovered point) or for the "general" theme, so a review is unprocessed
    exactly when it has no rows here."""
    __tablename__ = "review_themes"

    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), primary_key=True
    )
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("themes.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # Null only for code fallbacks (a review the model failed to classify, assigned to "general").
    sentiment: Mapped[Sentiment | None] = mapped_column(string_enum(Sentiment))
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
