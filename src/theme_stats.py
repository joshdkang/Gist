"""Read-side statistics over tagged reviews: how many reviews raise each theme, and how they feel.

A theme's numbers include every candidate merged into it (themes.merged_into_id), counted once per
review. When a review carries several tags that map to the same theme, its sentiment for that theme
is combined: see combine_sentiments.
"""
from collections import Counter
from collections.abc import Iterable

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ReviewTheme, Sentiment, Theme


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


async def theme_sentiment_counts(
    db: AsyncSession, entity_id: int
) -> dict[int, Counter[Sentiment | None]]:
    """For each theme (merged candidates counted under the theme they merged into): the number of
    reviews by combined sentiment. This is combine_sentiments computed in SQL."""
    root_id = func.coalesce(Theme.merged_into_id, Theme.id)

    def has(sentiment: Sentiment):
        return func.coalesce(func.bool_or(ReviewTheme.sentiment == sentiment), False)

    per_review = (
        select(
            root_id.label("theme_id"),
            has(Sentiment.POSITIVE).label("positive"),
            has(Sentiment.NEGATIVE).label("negative"),
            has(Sentiment.MIXED).label("mixed"),
            has(Sentiment.NEUTRAL).label("neutral"),
        )
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id)
        .group_by(ReviewTheme.review_id, root_id)
        .subquery()
    )
    combined = case(
        (per_review.c.mixed | (per_review.c.positive & per_review.c.negative), Sentiment.MIXED.value),
        (per_review.c.positive, Sentiment.POSITIVE.value),
        (per_review.c.negative, Sentiment.NEGATIVE.value),
        (per_review.c.neutral, Sentiment.NEUTRAL.value),
        else_=None,
    )
    rows = await db.execute(
        select(per_review.c.theme_id, combined, func.count()).group_by(per_review.c.theme_id, combined)
    )
    counts: dict[int, Counter[Sentiment | None]] = {}
    for theme_id, sentiment, count in rows.all():
        counts.setdefault(theme_id, Counter())[Sentiment(sentiment) if sentiment else None] += count
    return counts
