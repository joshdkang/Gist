import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, Theme, ThemeKind

SYSTEM_THEMES = {
    ThemeKind.GENERAL: (
        "General",
        'Reviews with no discernible topic, such as "Great!" or "meh".',
    ),
}


async def create_entity(
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    owner_id: uuid.UUID | None = None,
    is_public: bool = False,
    metadata: dict[str, Any] | None = None,
) -> Entity:
    """Create an entity together with its two system themes, so "exactly one of each" always holds."""
    entity = Entity(
        name=name,
        description=description,
        owner_id=owner_id,
        is_public=is_public,
        metadata_=metadata or {},
    )
    session.add(entity)
    await session.flush()
    session.add_all(
        Theme(entity_id=entity.id, kind=kind, name=theme_name, description=theme_description)
        for kind, (theme_name, theme_description) in SYSTEM_THEMES.items()
    )
    await session.commit()
    return entity
