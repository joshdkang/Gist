"""Upload a CSV of reviews for an entity, and list the entity's past uploads."""
import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_owned_entity, get_readable_entity
from auth import get_current_user
from config import settings
from database import get_db
from ingestion import ColumnMapping, RatingScale, ingest_csv
from models import Entity, Import, User
from queueing import count_runs_today, queue_analysis
from schemas import AnalysisQueued, ImportOut, RunOut, UploadResult

router = APIRouter(prefix="/entities/{entity_id}/imports", tags=["imports"])

rating_scale_adapter = TypeAdapter(RatingScale)


def parse_json_field(field_name: str, parse, raw: str):
    try:
        return parse(raw)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc']) or field_name}: {problem['msg']}"
            for problem in error.errors()
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid {field_name}: {problems}")


@router.post("", response_model=UploadResult, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    mapping: str = Form(..., description="JSON: which column feeds each review field. Only `text` is required."),
    rating_scale: str | None = Form(None, description="JSON scale for the rating column. Required with `mapping.rating`."),
    entity: Entity = Depends(get_owned_entity),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if await count_runs_today(db, user.id) >= settings.max_runs_per_user_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You have used your {settings.max_runs_per_user_per_day} analysis runs for the last 24 hours.",
        )

    column_mapping = parse_json_field("mapping", ColumnMapping.model_validate_json, mapping)
    scale = (
        parse_json_field("rating_scale", rating_scale_adapter.validate_json, rating_scale)
        if rating_scale
        else None
    )
    if column_mapping.rating and scale is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping.rating is set, so rating_scale is required."
        )

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file is over the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file must be UTF-8 text.")

    # Count the rows first so a file over the limit is refused before anything is inserted.
    try:
        row_count = sum(1 for _ in csv.DictReader(io.StringIO(text)))
    except csv.Error as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The file is not valid CSV: {error}")
    if row_count > settings.max_upload_rows:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file has {row_count} rows; the limit is {settings.max_upload_rows} per upload.",
        )

    try:
        import_row = await ingest_csv(
            db, entity.id, io.StringIO(text), column_mapping, scale, filename=file.filename
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))

    outcome = await queue_analysis(db, entity, user.id, import_row.rows_inserted)
    return UploadResult(
        import_=ImportOut.model_validate(import_row),
        analysis=AnalysisQueued(
            run=RunOut.model_validate(outcome.run) if outcome.run else None,
            reused=outcome.reused,
            message=outcome.message,
        ),
    )


@router.get("", response_model=list[ImportOut])
async def list_imports(entity: Entity = Depends(get_readable_entity), db: AsyncSession = Depends(get_db)):
    """The entity's uploads, newest first."""
    imports = await db.scalars(
        select(Import).where(Import.entity_id == entity.id).order_by(Import.id.desc())
    )
    return list(imports)
