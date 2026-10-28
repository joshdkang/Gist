"""Who may see or change an entity. Authorization lives here, in the API.

The backend connects to Postgres with a role that bypasses row level security, so these checks are
the only thing between a signed-in user and another user's data. Endpoints that take an entity
depend on one of these two helpers instead of repeating the rules.

- get_readable_entity: the user owns the entity, or it is public.
- get_owned_entity: the user owns it (needed to upload, analyze, edit, or delete).

An entity the user can't see gets a 404 whether it exists or not, so IDs can't be probed. A public
entity the user can see but doesn't own gets a 403 on writes.
"""
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models import Entity, User


async def get_readable_entity(
    entity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entity:
    entity = await db.get(Entity, entity_id)
    if entity is None or not (entity.is_public or entity.owner_id == user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found.")
    return entity


async def get_owned_entity(
    entity_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entity:
    entity = await get_readable_entity(entity_id, user, db)
    if entity.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change this entity.")
    return entity
