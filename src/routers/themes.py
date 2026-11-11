"""Results for an entity: its themes with counts and sentiment, what changed between two periods,
the reviews behind a theme, and a theme's trend over time. Read-only, so public entities are
readable by anyone signed in."""
import enum
from collections import Counter
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_readable_entity
from database import get_db
from models import Entity, Review, Sentiment, Theme, ThemeKind
from rating_stats import parse_scale, rating_condition, rating_summary, theme_rating_moments
from schemas import (
    GeneralSummary,
    SentimentCounts,
    PeriodCount,
    ThemeChange,
    ThemeChanges,
    ThemeList,
    ThemeRatingImpact,
    ThemeRatings,
    ThemeReviewOut,
    ThemeSummary,
    ThemeTrend,
    TrendPoint,
)
from significance import fisher_exact_p, significant_changes, welch_t_p
from theme_stats import (
    classified_review_count_query,
    per_review_theme_sentiment,
    period_review_counts,
    shift_period,
    theme_sentiment_counts_by,
    theme_trend,
)

router = APIRouter(prefix="/entities/{entity_id}/themes", tags=["themes"])

# A candidate is listed once this many reviews raise it, the same bar the classifier uses to show it.
CANDIDATE_MIN_REVIEWS = 10
# The recent window, in days before the entity's latest review: it ranks themes, counts recent
# mentions, and marks quiet themes.
RECENT_DAYS = 90
# The most trend periods one request for reviews may name (about 33 years of months).
MAX_PERIODS = 400
# With no period chosen, the changes compare the latest period with at least this many reviews, so
# a period that has only just begun isn't the default.
MIN_PERIOD_REVIEWS = 20


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


def rating_filter(rating_scale: str | None, ratings: list[str]) -> ColumnElement[bool] | None:
    """The condition for `rating` buckets on `rating_scale` (JSON, as the ratings summary gives it),
    or None when no rating was asked for. 422 for a bad scale or a bucket the scale doesn't have."""
    if not ratings:
        return None
    if rating_scale is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "rating_scale is required with rating.")
    try:
        scale = parse_scale(json.loads(rating_scale))
        return rating_condition(scale, ratings)
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Bad rating filter: {error}") from None


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
    include_candidates: bool = Query(
        False, description="Also return the listed candidates in `candidates`, so one call serves an overview"
    ),
    recent_days: int = Query(RECENT_DAYS, ge=1, le=3650),
    rating: list[str] = Query(
        [], description="Count only reviews in these rating buckets (labels from the ratings summary)"
    ),
    rating_scale: str | None = Query(None, description="The scale the rating buckets are on, as JSON"),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """Themes ranked by mentions in the recent window, then by total mentions.

    With a rating filter every count (totals, themes, sentiment, recent, general) covers only the
    reviews with those ratings, and a theme none of them raise is left out. The recent window
    still ends at the entity's latest review, and a candidate is listed by its unfiltered count.

    `general` is reported separately, not ranked. The recent window ends at the entity's latest
    review date, so an old dataset still has a recent window. Candidates are listed once
    CANDIDATE_MIN_REVIEWS reviews raise them.

    Every query is a round trip to the database, so this takes three: the entity's totals, the
    sentiment counts per theme (all time and recent together), and the themes themselves.
    """
    if kind == ThemeKind.GENERAL:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "general is reported separately.")
    match = rating_filter(rating_scale, rating)
    reviews = select(Review).where(Review.entity_id == entity.id)
    matching_reviews = (reviews.where(match) if match is not None else reviews).subquery()
    total_reviews, classified, as_of = (
        await db.execute(
            select(
                select(func.count()).select_from(matching_reviews).scalar_subquery(),
                classified_review_count_query(entity.id, match),
                select(func.max(Review.review_date)).where(Review.entity_id == entity.id).scalar_subquery(),
            )
        )
    ).one()
    since = as_of - timedelta(days=recent_days) if as_of is not None else None
    windows: dict[str, ColumnElement[bool] | None] = {"all": None, "matching": match}
    if since is not None:
        recent_window = Review.review_date >= since
        windows["recent"] = and_(recent_window, match) if match is not None else recent_window
    counts = await theme_sentiment_counts_by(db, entity.id, windows)
    themes = await db.scalars(
        select(Theme).where(Theme.entity_id == entity.id, Theme.merged_into_id.is_(None))
    )

    listed: dict[ThemeKind, list[ThemeSummary]] = {ThemeKind.DISCOVERED: [], ThemeKind.CANDIDATE: []}
    general = GeneralSummary(review_count=0, share=0.0)
    for theme in themes:
        counter = counts["matching"].get(theme.id, Counter())
        review_count = sum(counter.values())
        share = review_count / classified if classified else 0.0
        if theme.kind == ThemeKind.GENERAL:
            general = GeneralSummary(review_count=review_count, share=share)
            continue
        if match is not None and review_count == 0:
            continue
        unfiltered_count = sum(counts["all"].get(theme.id, Counter()).values())
        if theme.kind == ThemeKind.DISCOVERED or unfiltered_count >= CANDIDATE_MIN_REVIEWS:
            recent_count = sum(counts.get("recent", {}).get(theme.id, Counter()).values())
            listed[theme.kind].append(
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
    for summaries in listed.values():
        summaries.sort(key=lambda summary: (-summary.recent_count, -summary.review_count))
    return ThemeList(
        total_reviews=total_reviews,
        classified_reviews=classified,
        as_of=as_of,
        recent_days=recent_days,
        general=general,
        themes=listed[kind],
        candidates=listed[ThemeKind.CANDIDATE] if include_candidates else None,
    )


@router.get("/changes", response_model=ThemeChanges)
async def list_theme_changes(
    interval: TrendInterval = TrendInterval.QUARTER,
    period: datetime | None = Query(
        None,
        description="The period to look at (a period_start from `periods`); by default the latest with "
        f"at least {MIN_PERIOD_REVIEWS} reviews",
    ),
    compare_to: datetime | None = Query(
        None,
        description="The period to compare it with (a period_start from `periods`, earlier or later); "
        "by default the calendar period before it",
    ),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """How each theme changed from one period (`compare_to`, by default the one before) to another.

    Periods hold different numbers of reviews, so a theme is compared by its share of each period's
    classified reviews, and its negative share by the share of its own reviews that are negative.
    Each change has a p-value from Fisher's exact test, since a theme raised by 3 of 5 reviews is not
    news. Every theme is tested, and testing 30 at 5% would mark one or two by luck alone, so a
    change is marked significant by the Benjamini-Hochberg procedure, which keeps the share of
    flukes among the marked changes at 5%; mentions and negative shares are corrected separately.
    By default the comparison is the calendar period before, even when it has no reviews; a chosen
    `compare_to` needs reviews. Candidates are listed by the same rule as the theme list; `general`
    is left out.

    Three queries: the reviews per period, the theme counts in both periods, and the themes.
    """
    periods = await period_review_counts(db, entity.id, interval.value)
    if not periods:
        return ThemeChanges(
            interval=interval.value, period_start=None, previous_start=None,
            current_reviews=0, previous_reviews=0, periods=[], themes=[],
        )
    reviews_in = dict(periods)
    if period is None:
        chosen = next(
            (start for start, count in reversed(periods) if count >= MIN_PERIOD_REVIEWS), periods[-1][0]
        )
    elif period in reviews_in:
        chosen = period
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No reviews in that period.")
    if compare_to is None:
        previous = shift_period(chosen, interval.value, -1)
    elif compare_to == chosen:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick two different periods.")
    elif compare_to in reviews_in:
        previous = compare_to
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "No reviews in the period to compare with."
        )
    current_reviews, previous_reviews = reviews_in[chosen], reviews_in.get(previous, 0)

    period_of = func.date_trunc(interval.value, Review.review_date)
    counts = await theme_sentiment_counts_by(
        db, entity.id, {"all": None, "current": period_of == chosen, "previous": period_of == previous}
    )
    themes = await db.scalars(
        select(Theme).where(
            Theme.entity_id == entity.id, Theme.merged_into_id.is_(None), Theme.kind != ThemeKind.GENERAL
        )
    )
    compared: list[tuple[Theme, SentimentCounts, SentimentCounts]] = []
    for theme in themes:
        unfiltered_count = sum(counts["all"].get(theme.id, Counter()).values())
        if theme.kind == ThemeKind.CANDIDATE and unfiltered_count < CANDIDATE_MIN_REVIEWS:
            continue
        current = sentiment_counts(counts["current"].get(theme.id, Counter()))
        before = sentiment_counts(counts["previous"].get(theme.id, Counter()))
        if current.total == 0 and before.total == 0:
            continue
        compared.append((theme, before, current))

    share_p = [
        fisher_exact_p(before.total, previous_reviews, current.total, current_reviews)
        for _, before, current in compared
    ]
    negative_p = [
        fisher_exact_p(before.negative, before.total, current.negative, current.total)
        for _, before, current in compared
    ]
    share_significant, negative_significant = significant_changes(share_p), significant_changes(negative_p)
    changes = [
        ThemeChange(
            id=theme.id,
            kind=theme.kind,
            name=theme.name,
            description=theme.description,
            previous=before,
            current=current,
            previous_share=before.total / previous_reviews if previous_reviews else 0.0,
            current_share=current.total / current_reviews,
            share_change_p=share_p[index],
            share_change_clear=share_significant[index],
            negative_change_p=negative_p[index],
            negative_change_clear=negative_significant[index],
        )
        for index, (theme, before, current) in enumerate(compared)
    ]
    changes.sort(key=lambda change: (-abs(change.current_share - change.previous_share), change.name))
    return ThemeChanges(
        interval=interval.value,
        period_start=chosen,
        previous_start=previous,
        current_reviews=current_reviews,
        previous_reviews=previous_reviews,
        periods=[PeriodCount(period_start=start, reviews=count) for start, count in periods],
        themes=changes,
    )


@router.get("/ratings", response_model=ThemeRatings)
async def list_theme_ratings(
    rating_scale: str | None = Query(
        None, description="The scale to compare on, as JSON; by default the one with the most ratings"
    ),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """Which themes go with low or high ratings: for each theme, the average rating of the reviews
    that raise it against the reviews that don't.

    The effect weighs that difference by how common the theme is, so it says how far the theme's
    reviews pull the overall average: a theme a third of reviews raise, rated 0.5 lower, moves it
    more than one in 2% of reviews rated 2 lower. This is an association, not a cause: a theme can
    go with low ratings because unhappy customers write about it. Each difference is tested with
    Welch's t-test and marked significant with the Benjamini-Hochberg correction, as the period
    comparison does. Ratings never feed the analysis; this is only read from it.

    Candidates are listed once CANDIDATE_MIN_REVIEWS rated reviews raise them; `general` is left out.
    Four queries at most: the default scale, the ratings per theme, the total, and the themes.
    """
    if rating_scale is None:
        summary = await rating_summary(db, entity.id)
        if not summary.scales:
            return ThemeRatings(scale=None, rated_reviews=0, average=None, themes=[])
        scale = parse_scale(summary.scales[0].scale)
    else:
        try:
            scale = parse_scale(json.loads(rating_scale))
        except (json.JSONDecodeError, ValidationError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Bad rating scale: {error}") from None

    total, by_theme = await theme_rating_moments(db, entity.id, scale)
    themes = await db.scalars(
        select(Theme).where(
            Theme.entity_id == entity.id, Theme.merged_into_id.is_(None), Theme.kind != ThemeKind.GENERAL
        )
    )
    compared = []
    for theme in themes:
        with_theme = by_theme.get(theme.id)
        if with_theme is None:
            continue
        if theme.kind == ThemeKind.CANDIDATE and with_theme.count < CANDIDATE_MIN_REVIEWS:
            continue
        compared.append((theme, with_theme, total - with_theme))
    p_values = [
        welch_t_p(
            with_theme.count, with_theme.mean, with_theme.variance,
            rest.count, rest.mean or 0.0, rest.variance,
        )
        for _, with_theme, rest in compared
    ]
    significant = significant_changes(p_values)
    impacts = [
        ThemeRatingImpact(
            id=theme.id,
            kind=theme.kind,
            name=theme.name,
            rated_reviews=with_theme.count,
            average_with=with_theme.mean,
            average_without=rest.mean,
            effect=with_theme.count / total.count * (with_theme.mean - rest.mean) if rest.count else 0.0,
            p_value=p_values[index],
            significant=significant[index],
        )
        for index, (theme, with_theme, rest) in enumerate(compared)
    ]
    impacts.sort(key=lambda impact: (impact.effect, impact.name))
    return ThemeRatings(
        scale=scale.model_dump(), rated_reviews=total.count, average=total.mean, themes=impacts
    )


@router.get("/{theme_id}/reviews", response_model=list[ThemeReviewOut])
async def list_theme_reviews(
    theme_id: int,
    sentiment: Sentiment | None = None,
    period: list[datetime] = Query(
        [], description="Only reviews dated in these periods (period_start values from the trend)"
    ),
    interval: TrendInterval | None = Query(None, description="The trend interval the periods are from"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """The reviews that raise a theme, newest first, optionally only one sentiment or only some
    trend periods. These are the example quotes and the drill-down. A merged candidate shows the
    theme it merged into."""
    if period and interval is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "interval is required with period.")
    if len(period) > MAX_PERIODS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"At most {MAX_PERIODS} periods.")
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
    if period:
        # The same date_trunc the trend groups by, so a period holds exactly the reviews its bar counts.
        query = query.where(func.date_trunc(interval.value, Review.review_date).in_(period))
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
    """The theme's reviews per period by sentiment, oldest first, with the entity's reviews in each
    period so a client can show the theme's share. Every period in which the entity has classified
    reviews is listed, including those where none raise the theme; undated reviews are left out."""
    theme = await get_theme(db, entity, theme_id)
    periods = await period_review_counts(db, entity.id, interval.value)
    trend = dict(await theme_trend(db, entity.id, theme.id, interval.value))
    return ThemeTrend(
        theme_id=theme.id,
        interval=interval.value,
        points=[
            TrendPoint(
                period_start=start,
                sentiment=sentiment_counts(trend.get(start, Counter())),
                period_reviews=reviews,
            )
            for start, reviews in periods
        ],
    )
