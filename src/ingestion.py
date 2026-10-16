"""Generic review ingestion: normalize rows, hash them, skip duplicates, and record an import.

This knows nothing about users or HTTP. The upload endpoint and scripts such as load_yelp.py
both call it, and the caller is responsible for checking that the entity may be written to.
"""
import csv
import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal, TextIO

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Import, ImportStatus, Review

BATCH_SIZE = 2000

TRUE_VALUES = {"1", "true", "yes", "y", "positive", "up", "+"}
FALSE_VALUES = {"0", "false", "no", "n", "negative", "down", "-"}


class NumericScale(BaseModel):
    """Ratings are numbers between min and max, such as 1-5 stars."""
    type: Literal["numeric"] = "numeric"
    min: float
    max: float


class BinaryScale(BaseModel):
    """Ratings are yes/no style values. Stored as 1 (positive) or 0 (negative)."""
    type: Literal["binary"] = "binary"


class LabelScale(BaseModel):
    """Ratings are ordered labels, lowest first. Stored as the label's index."""
    type: Literal["labels"] = "labels"
    labels: list[str] = Field(min_length=2)


RatingScale = Annotated[NumericScale | BinaryScale | LabelScale, Field(discriminator="type")]


class ColumnMapping(BaseModel):
    """Which source column feeds each review field. Only `text` is required."""
    text: str
    title: str | None = None
    rating: str | None = None
    review_date: str | None = None
    external_id: str | None = None
    metadata_columns: list[str] = []
    date_format: str | None = None  # strptime format; ISO 8601 is tried when unset

    def required_columns(self) -> list[str]:
        optional_columns = [self.title, self.rating, self.review_date, self.external_id]
        return [self.text, *[column for column in optional_columns if column], *self.metadata_columns]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def compute_content_hash(text: str, review_date: datetime | None) -> str:
    """sha256 over the normalized text, "|", and the UTC date as "YYYY-MM-DD HH:MM:SS"."""
    date_part = review_date.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if review_date else ""
    return hashlib.sha256(f"{normalize_text(text)}|{date_part}".encode()).hexdigest()


def parse_date(value: str, date_format: str | None) -> datetime | None:
    """Return a timezone-aware datetime (naive input is taken as UTC), or None if unparseable."""
    try:
        parsed = (
            datetime.strptime(value, date_format) if date_format else datetime.fromisoformat(value)
        )
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_rating(value: str, scale: NumericScale | BinaryScale | LabelScale) -> Decimal | None:
    """Convert a raw rating to a number on the scale, or None if it doesn't fit."""
    match scale:
        case NumericScale():
            try:
                rating = Decimal(value)
            except InvalidOperation:
                return None
            return rating if Decimal(str(scale.min)) <= rating <= Decimal(str(scale.max)) else None
        case BinaryScale():
            if value.lower() in TRUE_VALUES:
                return Decimal(1)
            if value.lower() in FALSE_VALUES:
                return Decimal(0)
            return None
        case LabelScale():
            lowered_labels = [label.lower() for label in scale.labels]
            return Decimal(lowered_labels.index(value.lower())) if value.lower() in lowered_labels else None


def read_cell(row: Mapping[str, Any], column: str | None) -> str | None:
    """A trimmed string value, or None when the column is unset or the cell is empty."""
    if column is None or row.get(column) is None:
        return None
    return str(row[column]).strip() or None


def build_review_record(
    row: Mapping[str, Any],
    mapping: ColumnMapping,
    rating_scale: NumericScale | BinaryScale | LabelScale | None,
    entity_id: int,
    import_id: int,
) -> dict[str, Any] | None:
    """Turn one source row into a reviews insert dict, or None if the row has no text."""
    text = read_cell(row, mapping.text)
    if text is None:
        return None

    date_value = read_cell(row, mapping.review_date)
    review_date = parse_date(date_value, mapping.date_format) if date_value else None
    rating_value = read_cell(row, mapping.rating)
    rating = parse_rating(rating_value, rating_scale) if rating_value and rating_scale else None
    metadata = {
        column: read_cell(row, column)
        for column in mapping.metadata_columns
        if read_cell(row, column) is not None
    }

    return {
        "entity_id": entity_id,
        "import_id": import_id,
        "external_id": read_cell(row, mapping.external_id),
        "content_hash": compute_content_hash(text, review_date),
        "text": text,
        "title": read_cell(row, mapping.title),
        "rating": rating,
        "review_date": review_date,
        "metadata_": metadata,
    }


async def insert_reviews(session: AsyncSession, records: list[dict[str, Any]]) -> int:
    """Insert a batch, skipping reviews already stored for the entity. Returns the number inserted."""
    statement = (
        postgres_insert(Review)
        .on_conflict_do_nothing(constraint="uq_reviews_entity_content_hash")
        .returning(Review.id)
    )
    result = await session.execute(statement, records)
    inserted_count = len(result.all())
    await session.commit()
    return inserted_count


async def ingest_rows(
    session: AsyncSession,
    entity_id: int,
    rows: Iterable[Mapping[str, Any]],
    mapping: ColumnMapping,
    rating_scale: NumericScale | BinaryScale | LabelScale | None = None,
    *,
    source: str,
    filename: str | None = None,
) -> Import:
    """Insert reviews for an entity and record the import. Commits as it goes, one batch at a time.

    Rows that are empty or duplicate an existing review (same normalized text and date) are
    counted as skipped. Unparseable dates and out-of-scale ratings are stored as null.
    """
    if mapping.rating and rating_scale is None:
        raise ValueError("A rating column was mapped but no rating scale was given.")

    import_row = Import(
        entity_id=entity_id,
        filename=filename,
        source=source,
        rating_scale=rating_scale.model_dump() if rating_scale and mapping.rating else None,
        status=ImportStatus.PROCESSING,
    )
    session.add(import_row)
    await session.commit()

    rows_received = 0
    rows_inserted = 0
    batch: list[dict[str, Any]] = []
    try:
        for row in rows:
            rows_received += 1
            record = build_review_record(row, mapping, rating_scale, entity_id, import_row.id)
            if record is not None:
                batch.append(record)
            if len(batch) >= BATCH_SIZE:
                rows_inserted += await insert_reviews(session, batch)
                batch.clear()
        if batch:
            rows_inserted += await insert_reviews(session, batch)
    except Exception as error:
        await session.rollback()
        import_row.status = ImportStatus.FAILED
        import_row.error = str(error)
        import_row.rows_received = rows_received
        import_row.rows_inserted = rows_inserted
        import_row.rows_skipped = rows_received - rows_inserted
        await session.commit()
        raise

    import_row.status = ImportStatus.COMPLETED
    import_row.rows_received = rows_received
    import_row.rows_inserted = rows_inserted
    import_row.rows_skipped = rows_received - rows_inserted
    await session.commit()
    return import_row


async def ingest_csv(
    session: AsyncSession,
    entity_id: int,
    file: TextIO,
    mapping: ColumnMapping,
    rating_scale: NumericScale | BinaryScale | LabelScale | None = None,
    *,
    filename: str | None = None,
    source: str = "csv",
) -> Import:
    """Ingest a CSV file (opened in text mode) after checking that the mapped columns exist."""
    reader = csv.DictReader(file)
    missing_columns = [
        column for column in mapping.required_columns() if column not in (reader.fieldnames or [])
    ]
    if missing_columns:
        raise ValueError(f"Columns not found in the file: {', '.join(missing_columns)}")
    return await ingest_rows(
        session, entity_id, reader, mapping, rating_scale, source=source, filename=filename
    )
