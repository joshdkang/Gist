"""Results for an entity: its themes with counts and sentiment, the reviews behind a theme, and a
theme's trend over time. Read-only, so public entities are readable by anyone signed in."""
import enum
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_readable_entity
from database import get_db
from models import Entity, Review, Sentiment, Theme, ThemeKind
from schemas import (
    GeneralSummary,
    SentimentCounts,
    ThemeList,
    ThemeReviewOut,
    ThemeSummary,
    ThemeTrend,
    TrendPoint,
)
from theme_stats import (
    classified_review_count,
    per_review_theme_sentiment,
    theme_sentiment_counts,
    theme_trend,
)

router = APIRouter(prefix="/entities/{entity_id}/themes", tags=["themes"])

# A candidate is listed once this many reviews raise it, the same bar the classifier uses to show it.
CANDIDATE_MIN_REVIEWS = 10


class TrendInterval(enum.StrEnum):
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


def sentiment_counts(counter: Counter[Sentiment | None]) -> SentimentCounts:
    return SentimentCounts(
        positive=counter[Sentiment.POSITIVE],
        negative=counter[Sentiment.NEGATIVE],
        mixed=counter[Sentiment.MIXED],
        neutral=counter[Sentiment.NEUTRAL],
        total=sum(counter.values()),
    )


async def get_theme(db: AsyncSession, entity: Entity, theme_id: int) -> Theme:
    """The theme a merged candidate points at, or the theme itself. 404 for another entity's theme."""
    theme = await db.get(Theme, theme_id)
    if theme is None or theme.entity_id != entity.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Theme not found.")
    if theme.merged_into_id is not None:
        theme = await db.get(Theme, theme.merged_into_id)
    return theme


@router.get("", response_model=ThemeList)
async def list_themes(
    kind: ThemeKind = Query(ThemeKind.DISCOVERED, description="discovered or candidate"),
    recent_days: int = Query(180, ge=1, le=3650),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """Themes ranked by mentions in the recent window, then by total mentions.

    `general` is reported separately, not ranked. The recent window ends at the entity's latest
    review date, so an old dataset still has a recent window.
    """
    if kind == ThemeKind.GENERAL:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "general is reported separately.")
    total_reviews = await db.scalar(
        select(func.count()).select_from(Review).where(Review.entity_id == entity.id)
    )
    classified = await classified_review_count(db, entity.id)
    as_of = await db.scalar(select(func.max(Review.review_date)).where(Review.entity_id == entity.id))

    overall = await theme_sentiment_counts(db, entity.id)
    recent = (
        await theme_sentiment_counts(db, entity.id, since=as_of - timedelta(days=recent_days))
        if as_of is not None
        else {}
    )
    themes = await db.scalars(
        select(Theme).where(Theme.entity_id == entity.id, Theme.merged_into_id.is_(None))
    )
    summaries: list[ThemeSummary] = []
    general = GeneralSummary(review_count=0, share=0.0)
    for theme in themes:
        counter = overall.get(theme.id, Counter())
        review_count = sum(counter.values())
        share = review_count / classified if classified else 0.0
        if theme.kind == ThemeKind.GENERAL:
            general = GeneralSummary(review_count=review_count, share=share)
        elif theme.kind == kind and (kind == ThemeKind.DISCOVERED or review_count >= CANDIDATE_MIN_REVIEWS):
            recent_count = sum(recent.get(theme.id, Counter()).values())
            summaries.append(
                ThemeSummary(
                    id=theme.id,
                    kind=theme.kind,
                    name=theme.name,
                    description=theme.description,
                    review_count=review_count,
                    share=share,
                    sentiment=sentiment_counts(counter),
                    recent_count=recent_count,
                    quiet=recent_count == 0,
                )
            )
    summaries.sort(key=lambda summary: (-summary.recent_count, -summary.review_count))
    return ThemeList(
        total_reviews=total_reviews,
        classified_reviews=classified,
        as_of=as_of,
        recent_days=recent_days,
        general=general,
        themes=summaries,
    )


@router.get("/{theme_id}/reviews", response_model=list[ThemeReviewOut])
async def list_theme_reviews(
    theme_id: int,
    sentiment: Sentiment | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """The reviews that raise a theme, newest first, optionally only one sentiment. These are the
    example quotes and the drill-down. A merged candidate shows the theme it merged into."""
    theme = await get_theme(db, entity, theme_id)
    per_review, combined = per_review_theme_sentiment(entity.id, theme.id)
    query = (
        select(Review, combined)
        .join(per_review, per_review.c.review_id == Review.id)
        .order_by(Review.review_date.desc().nulls_last(), Review.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if sentiment is not None:
        query = query.where(combined == sentiment.value)
    rows = (await db.execute(query)).all()
    return [
        ThemeReviewOut(
            id=review.id,
            title=review.title,
            text=review.text,
            rating=float(review.rating) if review.rating is not None else None,
            review_date=review.review_date,
            sentiment=Sentiment(review_sentiment) if review_sentiment else None,
        )
        for review, review_sentiment in rows
    ]


@router.get("/{theme_id}/trend", response_model=ThemeTrend)
async def read_theme_trend(
    theme_id: int,
    interval: TrendInterval = TrendInterval.MONTH,
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """Reviews per period by sentiment, oldest first. Periods with no reviews are left out, and so
    are undated reviews."""
    theme = await get_theme(db, entity, theme_id)
    trend = await theme_trend(db, entity.id, theme.id, interval.value)
    return ThemeTrend(
        theme_id=theme.id,
        interval=interval.value,
        points=[
            TrendPoint(period_start=start, sentiment=sentiment_counts(counter)) for start, counter in trend
        ],
    )
