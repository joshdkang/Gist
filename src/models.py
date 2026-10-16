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
    DISCOVERED = "discovered"  # found by the model for this entity
    OTHER = "other"            # system theme: specific feedback that no discovered theme covers
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
        Index(
            "uq_themes_entity_system_kind",
            "entity_id",
            "kind",
            unique=True,
            postgresql_where=sql_text("kind <> 'discovered'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str] = mapped_column(Text)
    # Code finds the system themes by kind, not by name. Each entity has exactly one "other" and
    # one "general", created by code (not the model). The "other" share (not "general") drives
    # rediscovery. Themes are never deleted or retired: they accumulate across rediscoveries.
    kind: Mapped[ThemeKind] = mapped_column(
        string_enum(ThemeKind),
        default=ThemeKind.DISCOVERED,
        server_default=ThemeKind.DISCOVERED.value,
    )
    # The discovery run that introduced this theme. Null for the system themes.
    discovered_in_run_id: Mapped[int | None] = mapped_column(
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
    """One row per (review, theme). A review that fits no discovered theme gets a row for the
    "other" or "general" theme, so a review is unprocessed exactly when it has no rows here."""
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
