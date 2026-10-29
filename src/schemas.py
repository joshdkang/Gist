"""Request and response models for the API."""
import json
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models import Entity, ImportStatus, RunStatus, RunType

MAX_METADATA_BYTES = 10 * 1024


def one_line(value: str | None) -> str | None:
    """Collapse whitespace so a value is safe to put in a prompt. Empty becomes None."""
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip() or None


class EntityFields(BaseModel):
    """The fields an entity's owner can set, with the cleanup both create and update share."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        cleaned = one_line(value)
        if value is not None and cleaned is None:
            raise ValueError("name can't be blank.")
        return cleaned

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return one_line(value)

    @field_validator("metadata")
    @classmethod
    def metadata_not_huge(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value).encode()) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must be under {MAX_METADATA_BYTES // 1024} KB.")
        return value


class EntityCreate(EntityFields):
    name: str = Field(min_length=1, max_length=200)


class EntityUpdate(EntityFields):
    """Every field is optional. A field that is left out is unchanged; a description of null clears it."""


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: RunType
    status: RunStatus
    reviews_processed: int
    input_tokens: int
    output_tokens: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EntityOut(BaseModel):
    id: int
    name: str
    description: str | None
    metadata: dict[str, Any]
    is_public: bool
    is_owner: bool
    review_count: int
    created_at: datetime

    @classmethod
    def build(cls, entity: Entity, user_id: uuid.UUID, review_count: int) -> "EntityOut":
        return cls(
            id=entity.id,
            name=entity.name,
            description=entity.description,
            metadata=entity.metadata_,
            is_public=entity.is_public,
            is_owner=entity.owner_id == user_id,
            review_count=review_count,
            created_at=entity.created_at,
        )


class EntityDetail(EntityOut):
    import_count: int
    latest_run: RunOut | None


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str | None
    source: str
    rating_scale: dict[str, Any] | None
    status: ImportStatus
    rows_received: int
    rows_inserted: int
    rows_skipped: int
    error: str | None
    created_at: datetime


class AnalysisQueued(BaseModel):
    """What an upload did about analysis. `run` is null when nothing needed to be queued."""
    run: RunOut | None
    reused: bool
    message: str | None


class UploadResult(BaseModel):
    # `import` is a keyword, so the attribute is `import_` and the JSON key is `import`.
    model_config = ConfigDict(populate_by_name=True)

    import_: ImportOut = Field(serialization_alias="import")
    analysis: AnalysisQueued
