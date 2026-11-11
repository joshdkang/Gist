"""Read-side statistics over tagged reviews: how many reviews raise each theme, and how they feel.

A theme's numbers include every candidate merged into it (themes.merged_into_id), counted once per
review. When a review carries several tags that map to the same theme, its sentiment for that theme
is combined: see combine_sentiments.
"""
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Review, ReviewTheme, Sentiment, Theme


def combine_sentiments(sentiments: Iterable[Sentiment | None]) -> Sentiment | None:
    """One sentiment for several tags of the same review under one theme.

    Positive together with negative, or any mixed, is mixed. Neutral (mentioned without a clear
    feeling) gives way to positive or negative. Missing sentiments (code fallbacks) are ignored.
    """
    present = {sentiment for sentiment in sentiments if sentiment is not None}
    if Sentiment.MIXED in present or {Sentiment.POSITIVE, Sentiment.NEGATIVE} <= present:
        return Sentiment.MIXED
    for sentiment in (Sentiment.POSITIVE, Sentiment.NEGATIVE, Sentiment.NEUTRAL):
        if sentiment in present:
            return sentiment
    return None


def per_review_theme_sentiment(entity_id: int, theme_id: int | None = None):
    """A subquery with one row per (review, theme) and the review's combined sentiment for that
    theme (merged candidates counted under the theme they merged into). This is combine_sentiments
    computed in SQL. Restricted to one theme when theme_id is given."""
    root_id = func.coalesce(Theme.merged_into_id, Theme.id)

    def has(sentiment: Sentiment):
        return func.coalesce(func.bool_or(ReviewTheme.sentiment == sentiment), False)

    query = (
        select(
            ReviewTheme.review_id.label("review_id"),
            root_id.label("theme_id"),
            has(Sentiment.POSITIVE).label("positive"),
            has(Sentiment.NEGATIVE).label("negative"),
            has(Sentiment.MIXED).label("mixed"),
            has(Sentiment.NEUTRAL).label("neutral"),
        )
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id)
        .group_by(ReviewTheme.review_id, root_id)
    )
    if theme_id is not None:
        query = query.where(root_id == theme_id)
    per_review = query.subquery()
    combined = case(
        (per_review.c.mixed | (per_review.c.positive & per_review.c.negative), Sentiment.MIXED.value),
        (per_review.c.positive, Sentiment.POSITIVE.value),
        (per_review.c.negative, Sentiment.NEGATIVE.value),
        (per_review.c.neutral, Sentiment.NEUTRAL.value),
        else_=None,
    ).label("sentiment")
    return per_review, combined


def _to_counter(rows) -> Counter[Sentiment | None]:
    counter: Counter[Sentiment | None] = Counter()
    for sentiment, count in rows:
        counter[Sentiment(sentiment) if sentiment else None] += count
    return counter


async def theme_sentiment_counts(
    db: AsyncSession,
    entity_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[int, Counter[Sentiment | None]]:
    """For each theme: the number of reviews by combined sentiment. With since or until, only
    reviews dated in that range count (undated reviews are left out)."""
    per_review, combined = per_review_theme_sentiment(entity_id)
    query = select(per_review.c.theme_id, combined, func.count()).group_by(per_review.c.theme_id, combined)
    if since is not None or until is not None:
        query = query.join(Review, Review.id == per_review.c.review_id)
        if since is not None:
            query = query.where(Review.review_date >= since)
        if until is not None:
            query = query.where(Review.review_date <= until)
    counts: dict[int, Counter[Sentiment | None]] = {}
    for theme_id, sentiment, count in (await db.execute(query)).all():
        counts.setdefault(theme_id, Counter())[Sentiment(sentiment) if sentiment else None] += count
    return counts


async def theme_sentiment_counts_by(
    db: AsyncSession, entity_id: int, windows: dict[str, ColumnElement[bool] | None]
) -> dict[str, dict[int, Counter[Sentiment | None]]]:
    """theme_sentiment_counts for several sets of reviews in one query, each named in `windows` by
    a condition on Review (None for every review).

    The theme list needs the total, a recent window and a filter (such as a rating) together, and
    every query is a round trip to the database, so each set is a COUNT ... FILTER beside the others.
    """
    per_review, combined = per_review_theme_sentiment(entity_id)
    names = list(windows)
    columns = [func.count() if windows[name] is None else func.count().filter(windows[name]) for name in names]
    rows = await db.execute(
        select(per_review.c.theme_id, combined, *columns)
        .select_from(per_review)
        .join(Review, Review.id == per_review.c.review_id)
        .group_by(per_review.c.theme_id, combined)
    )
    counts: dict[str, dict[int, Counter[Sentiment | None]]] = {name: {} for name in names}
    for theme_id, sentiment, *window_counts in rows.all():
        key = Sentiment(sentiment) if sentiment else None
        for name, count in zip(names, window_counts):
            if count:
                counts[name].setdefault(theme_id, Counter())[key] += count
    return counts


async def theme_trend(
    db: AsyncSession, entity_id: int, theme_id: int, interval: str
) -> list[tuple[datetime, Counter[Sentiment | None]]]:
    """Reviews per period (a date_trunc unit such as month) by combined sentiment for one theme,
    oldest first. Undated reviews are left out. Periods with no reviews are not returned."""
    per_review, combined = per_review_theme_sentiment(entity_id, theme_id)
    period = func.date_trunc(interval, Review.review_date).label("period")
    rows = await db.execute(
        select(period, combined, func.count())
        .select_from(per_review)
        .join(Review, Review.id == per_review.c.review_id)
        .where(Review.review_date.is_not(None))
        .group_by(period, combined)
        .order_by(period)
    )
    trend: dict[datetime, list[tuple[str | None, int]]] = {}
    for start, sentiment, count in rows.all():
        trend.setdefault(start, []).append((sentiment, count))
    return [(start, _to_counter(items)) for start, items in trend.items()]


async def period_review_counts(db: AsyncSession, entity_id: int, interval: str) -> list[tuple[datetime, int]]:
    """Classified reviews per period (a date_trunc unit), oldest first: the denominator for a
    theme's share of a period. Undated reviews are left out, and so are periods with none."""
    period = func.date_trunc(interval, Review.review_date).label("period")
    rows = await db.execute(
        select(period, func.count(func.distinct(ReviewTheme.review_id)))
        .select_from(ReviewTheme)
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .join(Review, Review.id == ReviewTheme.review_id)
        .where(Theme.entity_id == entity_id, Review.review_date.is_not(None))
        .group_by(period)
        .order_by(period)
    )
    return [(start, count) for start, count in rows.all()]


# Months in each date_trunc unit that shift_period moves by month.
MONTHS_PER_PERIOD = {"month": 1, "quarter": 3, "year": 12}


def shift_period(start: datetime, interval: str, steps: int) -> datetime:
    """The start of the period `steps` periods from `start` (negative goes back). `start` must be a
    period start, as date_trunc gives it, so its day is the first of the month (or a Monday for weeks)."""
    if interval == "week":
        return start + timedelta(weeks=steps)
    months = start.year * 12 + start.month - 1 + steps * MONTHS_PER_PERIOD[interval]
    return start.replace(year=months // 12, month=months % 12 + 1)


def classified_review_count_query(entity_id: int, match: ColumnElement[bool] | None = None):
    """A scalar subquery: reviews with at least one tag (and matching `match`, a condition on Review)."""
    query = (
        select(func.count(func.distinct(ReviewTheme.review_id)))
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id)
    )
    if match is not None:
        query = query.join(Review, Review.id == ReviewTheme.review_id).where(match)
    return query.scalar_subquery()


async def classified_review_count(db: AsyncSession, entity_id: int) -> int:
    """Reviews with at least one tag."""
    return await db.scalar(select(classified_review_count_query(entity_id))) or 0
