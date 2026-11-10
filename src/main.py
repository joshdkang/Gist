from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth import get_current_user
from config import settings
from database import engine
from models import User
from routers import entities, imports, ratings, runs, themes

# Tables are created by Alembic migrations (alembic upgrade head), not by the app.
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
# The frontend calls the API from the browser with a bearer token, so no cookies are involved.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(entities.router)
app.include_router(imports.router)
app.include_router(ratings.router)
app.include_router(runs.router)
app.include_router(themes.router)


@app.get("/me")
async def read_current_user(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "created_at": user.created_at,
    }
