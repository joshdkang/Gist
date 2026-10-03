from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from database import Base

class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(index=True)
    text: Mapped[str]
    rating: Mapped[int | None]
    review_date: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(default="yelp")