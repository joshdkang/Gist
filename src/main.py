from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from auth import get_current_user
from database import engine
from models import User
from routers import entities, imports, runs

# Tables are created by Alembic migrations (alembic upgrade head), not by the app.
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
app.include_router(entities.router)
app.include_router(imports.router)
app.include_router(runs.router)


@app.get("/me")
async def read_current_user(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "created_at": user.created_at,
    }
