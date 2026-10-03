from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Depends
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
import pandas as pd

from database import engine, Base, get_db
from models import Feedback

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)

@app.post("/upload")
async def upload_feedback(file: UploadFile, db: AsyncSession = Depends(get_db)):
    df = pd.read_csv(file.file)
    df = df.rename(columns={"business_id": "business_id", "text": "text",
                             "stars": "rating", "date": "review_date"})
    df["review_date"] = pd.to_datetime(df["review_date"], errors="coerce")
    df = df.dropna(subset=["text"])

    records = [
        {
            "business_id": row.business_id,
            "text": row.text,
            "rating": int(row.rating) if pd.notna(row.rating) else None,
            "review_date": row.review_date.to_pydatetime() if pd.notna(row.review_date) else None,
            "source": "yelp",
        }
        for row in df.itertuples()
    ]

    await db.execute(insert(Feedback), records)
    await db.commit()

    return {"rows_ingested": len(records)}