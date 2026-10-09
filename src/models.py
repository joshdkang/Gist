from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey
from database import Base

class Business(Base):
    __tablename__ = "businesses"

    business_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    city: Mapped[str | None]
    state: Mapped[str | None]
    categories: Mapped[str | None]
    stars: Mapped[float | None]
    review_count: Mapped[int | None]

class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.business_id"), index=True
    )
    text: Mapped[str]
    rating: Mapped[int | None]
    review_date: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(default="yelp")