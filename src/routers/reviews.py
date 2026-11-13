"""Every review of an entity, with the themes it raises: the All reviews page. Readable like the entity.

`GET /entities/{id}/reviews` lists classified reviews with their themes (merged candidates under the
theme they merged into, with the review's combined sentiment for each), and `GET .../reviews/facets`
counts them by theme, by sentiment within each chosen theme, and by rating, for the filter column.
Both take the same filters, which combine:

- `theme`: repeatable, a theme's id, optionally with the sentiments of its mentions to keep
  (`12:negative,mixed`). A review matches a theme if it raises it with one of those sentiments, or
  with any sentiment when none is given.
- `match`: `any` (the default) keeps reviews that match any chosen theme; `all` those that match every one.
- `rating` with `rating_scale`, `period` with `interval`, and `q`: as on the theme list.

Facet counts follow the usual rule that a facet's counts ignore its own selection, so they say what
choosing another option would give. The one exception is themes under `match=all`: there each count
is how many of the current results also raise the theme, which is what ticking it would leave.
"""
import enum
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_readable_entity
from database import get_db
from models import Entity, Review, ReviewTheme, Sentiment, Theme, ThemeKind
from rating_stats import rating_summary
from routers.themes import (
    CANDIDATE_MIN_REVIEWS,
    INTERVAL_DESCRIPTION,
    PERIOD_DESCRIPTION,
    RATING_DESCRIPTION,
    RATING_SCALE_DESCRIPTION,
    SEARCH_DESCRIPTION,
    TrendInterval,
    both,
    period_filter,
    rating_filter,
    sentiment_counts,
)
from schemas import PeriodCount, ReviewFacets, ReviewListItem, ReviewTag, ThemeFacet
from search import search_filter
from theme_stats import per_review_theme_sentiment

router = APIRouter(prefix="/entities/{entity_id}/reviews", tags=["reviews"])

# The most themes one request may choose.
MAX_THEMES = 50
THEME_DESCRIPTION = (
    "Only reviews that raise this theme: a theme id, optionally with the sentiments of its mentions to "
    "keep (12:negative,mixed). Repeatable; `match` says how several combine"
)


class Match(enum.StrEnum):
    ANY = "any"
    ALL = "all"


class Sort(enum.StrEnum):
    NEWEST = "newest"
    OLDEST = "oldest"
    HIGHEST = "highest"
    LOWEST = "lowest"


def parse_themes(raw: list[str]) -> dict[int, list[Sentiment]]:
    """`12` or `12:negative,mixed` into {12: [NEGATIVE, MIXED]} (an empty list is any sentiment).
    422 for anything unreadable or too many themes."""
    if len(raw) > MAX_THEMES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"At most {MAX_THEMES} themes.")
    themes: dict[int, list[Sentiment]] = {}
    for item in raw:
        theme_id, _, sentiments = item.partition(":")
        try:
            themes[int(theme_id)] = [Sentiment(value) for value in sentiments.split(",") if value]
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Bad theme filter {item!r}: use 12 or 12:negative,mixed."
            ) from None
    return themes


async def resolve_themes(db: AsyncSession, entity: Entity, themes: dict[int, list[Sentiment]]) -> dict[int, list[Sentiment]]:
    """The chosen themes under the ids reviews are counted by: a merged candidate becomes the theme it
    merged into. 404 for a theme of another entity, as everywhere else."""
    if not themes:
        return {}
    rows = (await db.execute(select(Theme.id, Theme.entity_id, Theme.merged_into_id).where(Theme.id.in_(themes)))).all()
    found = {row.id: row for row in rows}
    resolved: dict[int, list[Sentiment]] = {}
    for theme_id, sentiments in themes.items():
        row = found.get(theme_id)
        if row is None or row.entity_id != entity.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Theme not found.")
        root = row.merged_into_id or row.id
        resolved[root] = sorted(set(resolved.get(root, [])) | set(sentiments))
    return resolved


class ReviewFilters:
    """The filters of one request, as SQL conditions on Review."""

    def __init__(
        self,
        entity_id: int,
        themes: dict[int, list[Sentiment]],
        match: Match,
        by_rating: ColumnElement[bool] | None,
        by_period: ColumnElement[bool] | None,
        by_search: ColumnElement[bool] | None,
    ):
        self.per_review, self.combined = per_review_theme_sentiment(entity_id)
        self.entity_id = entity_id
        self.themes = themes
        self.match = match
        self.not_rating = both(by_period, by_search)    # what the rating facet keeps
        self.not_period = both(by_rating, by_search)    # what the period facet keeps
        self.others = both(by_rating, by_period, by_search)   # every filter but the themes

    def raises(self, theme_id: int, sentiments: list[Sentiment] | None = None) -> ColumnElement[bool]:
        """Review raises the theme, with one of `sentiments` (the theme's chosen ones by default)."""
        sentiments = self.themes.get(theme_id, []) if sentiments is None else sentiments
        query = select(self.per_review.c.review_id).where(self.per_review.c.theme_id == theme_id)
        if sentiments:
            query = query.where(self.combined.in_([sentiment.value for sentiment in sentiments]))
        return Review.id.in_(query)

    def theme_condition(self, theme_ids: list[int] | None = None) -> ColumnElement[bool] | None:
        """The chosen themes combined by `match` (only `theme_ids` of them when given)."""
        ids = list(self.themes) if theme_ids is None else theme_ids
        if not ids:
            return None
        conditions = [self.raises(theme_id) for theme_id in ids]
        return and_(*conditions) if self.match == Match.ALL else or_(*conditions)

    def classified(self) -> ColumnElement[bool]:
        """Review has been classified (has at least one tag); the page lists only those."""
        return Review.id.in_(
            select(ReviewTheme.review_id).join(Theme, Theme.id == ReviewTheme.theme_id).where(Theme.entity_id == self.entity_id)
        )

    def condition(self) -> ColumnElement[bool]:
        """Every filter."""
        return both(Review.entity_id == self.entity_id, self.classified(), self.theme_condition(), self.others)


async def review_filters(
    db: AsyncSession,
    entity: Entity,
    theme: list[str],
    match: Match,
    rating: list[str],
    rating_scale: str | None,
    period: list[datetime],
    interval: TrendInterval | None,
    q: str | None,
) -> ReviewFilters:
    """The request's filters. 422 for a bad filter, 404 for another entity's theme."""
    themes = await resolve_themes(db, entity, parse_themes(theme))
    return ReviewFilters(
        entity.id, themes, match, rating_filter(rating_scale, rating), period_filter(period, interval), search_filter(q)
    )


KIND_ORDER = {ThemeKind.DISCOVERED: 0, ThemeKind.CANDIDATE: 1, ThemeKind.GENERAL: 2}

ORDER = {
    Sort.NEWEST: (Review.review_date.desc().nulls_last(), Review.id.desc()),
    Sort.OLDEST: (Review.review_date.asc().nulls_last(), Review.id.asc()),
    Sort.HIGHEST: (Review.rating.desc().nulls_last(), Review.review_date.desc().nulls_last(), Review.id.desc()),
    Sort.LOWEST: (Review.rating.asc().nulls_last(), Review.review_date.desc().nulls_last(), Review.id.desc()),
}


@router.get("", response_model=list[ReviewListItem])
async def list_reviews(
    theme: list[str] = Query([], description=THEME_DESCRIPTION),
    match: Match = Query(Match.ANY, description="any: reviews matching any chosen theme; all: every one"),
    rating: list[str] = Query([], description=RATING_DESCRIPTION),
    rating_scale: str | None = Query(None, description=RATING_SCALE_DESCRIPTION),
    period: list[datetime] = Query([], description=PERIOD_DESCRIPTION),
    interval: TrendInterval | None = Query(None, description=INTERVAL_DESCRIPTION),
    q: str | None = Query(None, description=SEARCH_DESCRIPTION),
    sort: Sort = Query(Sort.NEWEST),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """Classified reviews matching the filters, each with the themes it raises. Two queries: the page of
    reviews, then their themes."""
    filters = await review_filters(db, entity, theme, match, rating, rating_scale, period, interval, q)
    reviews = (
        await db.scalars(select(Review).where(filters.condition()).order_by(*ORDER[sort]).limit(limit).offset(offset))
    ).all()
    if not reviews:
        return []
    tags = await db.execute(
        select(filters.per_review.c.review_id, Theme.id, Theme.name, Theme.kind, filters.combined)
        .join(Theme, Theme.id == filters.per_review.c.theme_id)
        .where(filters.per_review.c.review_id.in_([review.id for review in reviews]))
    )
    by_review: dict[int, list[ReviewTag]] = {}
    for review_id, theme_id, name, kind, sentiment in tags:
        by_review.setdefault(review_id, []).append(
            ReviewTag(id=theme_id, name=name, kind=kind, sentiment=Sentiment(sentiment) if sentiment else None)
        )
    # Themes first, then emerging topics, then General; alphabetical within each.
    return [
        ReviewListItem(
            id=review.id,
            title=review.title,
            text=review.text,
            rating=float(review.rating) if review.rating is not None else None,
            review_date=review.review_date,
            themes=sorted(by_review.get(review.id, []), key=lambda tag: (KIND_ORDER[tag.kind], tag.name.lower())),
        )
        for review in reviews
    ]


@router.get("/facets", response_model=ReviewFacets)
async def review_facets(
    theme: list[str] = Query([], description=THEME_DESCRIPTION),
    match: Match = Query(Match.ANY, description="any: reviews matching any chosen theme; all: every one"),
    rating: list[str] = Query([], description=RATING_DESCRIPTION),
    rating_scale: str | None = Query(None, description=RATING_SCALE_DESCRIPTION),
    period: list[datetime] = Query([], description=PERIOD_DESCRIPTION),
    interval: TrendInterval | None = Query(None, description=INTERVAL_DESCRIPTION),
    q: str | None = Query(None, description=SEARCH_DESCRIPTION),
    period_interval: TrendInterval = Query(TrendInterval.QUARTER, description="The interval `periods` counts by"),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """The counts behind the filter column, for the same filters as the list:

    - `total`: the reviews the filters select.
    - `themes`: every listed theme (established ones, candidates at least CANDIDATE_MIN_REVIEWS reviews
      raise, General, and any chosen theme), with how many reviews raise it. With `match=any` that is
      under the other filters, the theme selection left out (a chosen theme still with its own chosen
      sentiments); with `match=all` it is how many of the current results also raise it. A chosen
      theme also gets `sentiments`: its mentions by sentiment under the other filters (and with
      `match=all` the other chosen themes).
    - `ratings`: the rating summary (as for the entity) of the reviews the other filters select.
    - `periods`: the reviews per period on `period_interval` that the other filters select, oldest
      first; undated reviews and periods without reviews are left out.
    """
    filters = await review_filters(db, entity, theme, match, rating, rating_scale, period, interval, q)
    per_review, combined = filters.per_review, filters.combined
    base = both(Review.entity_id == entity.id, filters.others)

    total = await db.scalar(select(func.count()).select_from(Review).where(filters.condition())) or 0

    # Per theme: the chosen sentiments of a chosen theme apply to its own count.
    own_sentiments = [
        and_(per_review.c.theme_id == theme_id, combined.in_([sentiment.value for sentiment in sentiments]))
        for theme_id, sentiments in filters.themes.items()
        if sentiments
    ]
    with_sentiment = [theme_id for theme_id, sentiments in filters.themes.items() if sentiments]
    sentiment_ok = or_(per_review.c.theme_id.not_in(with_sentiment), *own_sentiments) if with_sentiment else None
    restrict = filters.theme_condition() if match == Match.ALL else None
    counts = dict(
        (
            await db.execute(
                select(per_review.c.theme_id, func.count(func.distinct(per_review.c.review_id)))
                .join(Review, Review.id == per_review.c.review_id)
                .where(both(base, sentiment_ok, restrict))
                .group_by(per_review.c.theme_id)
            )
        ).all()
    )
    unfiltered = dict(
        (await db.execute(select(per_review.c.theme_id, func.count()).group_by(per_review.c.theme_id))).all()
    )

    # A chosen theme's mentions by sentiment: the rating, period and search, and with "all" the other themes.
    sentiments_of: dict[int, Counter] = {}
    for theme_id in filters.themes:
        others = [other for other in filters.themes if other != theme_id]
        condition = both(base, per_review.c.theme_id == theme_id, filters.theme_condition(others) if match == Match.ALL else None)
        rows = await db.execute(
            select(combined, func.count()).select_from(per_review).join(Review, Review.id == per_review.c.review_id)
            .where(condition).group_by(combined)
        )
        sentiments_of[theme_id] = Counter({Sentiment(value) if value else None: count for value, count in rows})

    themes = (
        await db.execute(
            select(Theme.id, Theme.name, Theme.kind).where(Theme.entity_id == entity.id, Theme.merged_into_id.is_(None))
        )
    ).all()
    listed = [
        row for row in themes
        if row.kind != ThemeKind.CANDIDATE or unfiltered.get(row.id, 0) >= CANDIDATE_MIN_REVIEWS or row.id in filters.themes
    ]
    listed.sort(key=lambda row: (KIND_ORDER[row.kind], -unfiltered.get(row.id, 0), row.name.lower()))
    facets = [
        ThemeFacet(
            id=row.id,
            name=row.name,
            kind=row.kind,
            count=counts.get(row.id, 0),
            sentiments=sentiment_counts(sentiments_of[row.id]) if row.id in sentiments_of else None,
        )
        for row in listed
    ]

    rating_match = both(filters.classified(), filters.theme_condition(), filters.not_rating)
    ratings = await rating_summary(db, entity.id, rating_match)

    period = func.date_trunc(period_interval.value, Review.review_date).label("period")
    period_rows = await db.execute(
        select(period, func.count())
        .where(
            both(
                Review.entity_id == entity.id,
                Review.review_date.is_not(None),
                filters.classified(),
                filters.theme_condition(),
                filters.not_period,
            )
        )
        .group_by(period)
        .order_by(period)
    )
    periods = [PeriodCount(period_start=start, reviews=count) for start, count in period_rows]
    return ReviewFacets(total=total, themes=facets, ratings=ratings, period_interval=period_interval.value, periods=periods)
