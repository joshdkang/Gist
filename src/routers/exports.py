"""CSV exports of an entity's results, for spreadsheets and decks. Readable like the entity itself.

Each export calls the JSON endpoint behind the page it comes from (the theme list, ratings by theme,
the period comparison) instead of recomputing, so a file's numbers are the ones on screen, filters
included. The reviews export streams, since an entity can have tens of thousands of tagged reviews.

Cells are written for Excel as much as for code: UTF-8 with a byte-order mark (so accents and emoji
open correctly), percentages as numbers, yes/no for flags, and text that could run as a formula
(starting with =, +, -, @, tab or carriage return) prefixed with an apostrophe, because review text
comes from uploads.
"""
import csv
import io
import re
import unicodedata
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from access import get_readable_entity
from database import get_db
from models import Entity, Review, Sentiment, Theme, ThemeKind
from routers.themes import (
    INTERVAL_DESCRIPTION,
    PERIOD_DESCRIPTION,
    RATING_DESCRIPTION,
    RATING_SCALE_DESCRIPTION,
    RECENT_DAYS,
    SEARCH_DESCRIPTION,
    TrendInterval,
    both,
    get_theme,
    list_theme_changes,
    list_theme_ratings,
    list_themes,
    period_filter,
    rating_filter,
)
from routers.reviews import THEME_DESCRIPTION, Match, ReviewFilters, parse_themes, resolve_themes
from search import search_filter
from theme_stats import per_review_theme_sentiment

router = APIRouter(prefix="/entities/{entity_id}/exports", tags=["exports"])

BYTE_ORDER_MARK = "﻿"
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
KIND_LABELS = {ThemeKind.DISCOVERED: "theme", ThemeKind.CANDIDATE: "emerging topic", ThemeKind.GENERAL: "general"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# Rows written per chunk of the reviews export.
STREAM_BATCH = 1000


def safe_cell(value: object) -> object:
    """A value ready for a spreadsheet: text that would start a formula gets a leading apostrophe,
    which Excel and Google Sheets treat as "this is text". Numbers pass through, negative ones too."""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def csv_lines(rows: Iterable[Iterable[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerows([safe_cell(value) for value in row] for row in rows)
    return buffer.getvalue()


def slug(text: str) -> str:
    """ASCII words joined by hyphens, for filenames: "Café Du Monde" becomes "cafe-du-monde"."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def filename(entity: Entity, *parts: str) -> str:
    words = [slug(entity.name) or "entity", *(slug(part) for part in parts if part)]
    return "-".join(word for word in words if word) + f"-{datetime.now(UTC):%Y-%m-%d}.csv"


def csv_response(body: str | AsyncIterator[str], name: str) -> StreamingResponse:
    async def once(text: str) -> AsyncIterator[str]:
        yield text

    return StreamingResponse(
        once(body) if isinstance(body, str) else body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def percent(value: float) -> float:
    return round(value * 100, 2)


def yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


def period_label(start: datetime | None, interval: str) -> str:
    """The period as the pages name it: "2019", "2019 Q1", "Jan 2019", or the week's first day."""
    if start is None:
        return ""
    if interval == "year":
        return f"{start.year}"
    if interval == "quarter":
        return f"{start.year} Q{(start.month - 1) // 3 + 1}"
    if interval == "month":
        return f"{MONTHS[start.month - 1]} {start.year}"
    return f"Week of {start:%Y-%m-%d}"


def rating_suffix(ratings: list[str]) -> str:
    return f"rated {' '.join(ratings)}" if ratings else ""


def search_suffix(query: str | None) -> str:
    """'mentions valet' for a search, so the file says what it was filtered by."""
    return f"mentions {query.strip()[:40]}" if query and query.strip() else ""


def period_suffix(periods: list[datetime], interval: TrendInterval | None) -> str:
    """"2019 Q1", or "2019 Q1 to 2019 Q4" for several periods (the first and last), or ""."""
    if not periods or interval is None:
        return ""
    first, last = period_label(min(periods), interval.value), period_label(max(periods), interval.value)
    return first if first == last else f"{first} to {last}"


@router.get("/themes.csv")
async def export_themes(
    rating: list[str] = Query([], description=RATING_DESCRIPTION),
    rating_scale: str | None = Query(None, description=RATING_SCALE_DESCRIPTION),
    period: list[datetime] = Query([], description=PERIOD_DESCRIPTION),
    interval: TrendInterval | None = Query(None, description=INTERVAL_DESCRIPTION),
    q: str | None = Query(None, description=SEARCH_DESCRIPTION),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """The theme list as a table: one row per theme and listed emerging topic, then `general`.

    With the rating and period filters and the search, the counts cover only those reviews, as on the page. When the entity
    has ratings, each theme also gets its ratings-by-theme columns, which are never filtered (they
    compare a theme's ratings with everyone else's).
    """
    listing = await list_themes(
        kind=ThemeKind.DISCOVERED,
        include_candidates=True,
        recent_days=RECENT_DAYS,
        rating=rating,
        rating_scale=rating_scale,
        period=period,
        interval=interval,
        q=q,
        entity=entity,
        db=db,
    )
    ratings = await list_theme_ratings(rating_scale=None, entity=entity, db=db)
    impacts = {impact.id: impact for impact in ratings.themes}
    with_ratings = ratings.scale is not None

    header = [
        "theme", "kind", "description", "reviews", "share_of_reviews_pct",
        "positive", "mixed", "neutral", "negative", f"reviews_last_{listing.recent_days}_days", "quiet",
    ]
    if with_ratings:
        header += [
            "average_rating_with", "average_rating_without", "effect_on_average_rating",
            "rating_p_value", "rating_difference_significant",
        ]
    rows: list[list[object]] = [header]
    for theme in [*listing.themes, *(listing.candidates or [])]:
        row: list[object] = [
            theme.name, KIND_LABELS[theme.kind], theme.description, theme.review_count, percent(theme.share),
            theme.sentiment.positive, theme.sentiment.mixed, theme.sentiment.neutral, theme.sentiment.negative,
            theme.recent_count, yes_no(theme.quiet),
        ]
        if with_ratings:
            impact = impacts.get(theme.id)
            row += (
                [
                    round(impact.average_with, 3),
                    round(impact.average_without, 3) if impact.average_without is not None else "",
                    round(impact.effect, 4),
                    f"{impact.p_value:.3g}" if impact.p_value is not None else "",
                    yes_no(impact.significant),
                ]
                if impact
                else ["", "", "", "", ""]
            )
        rows.append(row)
    general = [
        "General (no specific topic)", "general", "", listing.general.review_count, percent(listing.general.share)
    ]
    rows.append(general + [""] * (len(header) - len(general)))
    name = filename(entity, "themes", period_suffix(period, interval), rating_suffix(rating), search_suffix(q))
    return csv_response(BYTE_ORDER_MARK + csv_lines(rows), name)


@router.get("/changes.csv")
async def export_changes(
    interval: TrendInterval = TrendInterval.QUARTER,
    period: datetime | None = Query(None, description="As for the period comparison"),
    compare_to: datetime | None = Query(None, description="As for the period comparison"),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """The period comparison as a table: one row per theme, with both periods' counts and shares,
    the changes in points, their p-values, and whether each is significant (after the correction
    for testing every theme), biggest change in share first as on the page."""
    changes = await list_theme_changes(
        interval=interval, period=period, compare_to=compare_to, entity=entity, db=db
    )
    before = period_label(changes.previous_start, changes.interval)
    now = period_label(changes.period_start, changes.interval)

    def negative_pct(negative: int, total: int) -> float | str:
        return round(negative / total * 100, 2) if total else ""

    rows: list[list[object]] = [[
        "theme", "kind", "period", "compared_with",
        "reviews_in_period", "theme_reviews", "share_pct",
        "reviews_in_compared_period", "theme_reviews_compared", "share_compared_pct",
        "share_change_pts", "share_p_value", "share_change_significant",
        "negative_pct", "negative_compared_pct", "negative_change_pts",
        "negative_p_value", "negative_change_significant",
    ]]
    for change in changes.themes:
        negative_now = negative_pct(change.current.negative, change.current.total)
        negative_before = negative_pct(change.previous.negative, change.previous.total)
        rows.append([
            change.name, KIND_LABELS[change.kind], now, before,
            changes.current_reviews, change.current.total, percent(change.current_share),
            changes.previous_reviews, change.previous.total, percent(change.previous_share),
            round((change.current_share - change.previous_share) * 100, 2),
            f"{change.share_change_p:.3g}" if change.share_change_p is not None else "",
            yes_no(change.share_change_clear),
            negative_now, negative_before,
            round(negative_now - negative_before, 2) if negative_now != "" and negative_before != "" else "",
            f"{change.negative_change_p:.3g}" if change.negative_change_p is not None else "",
            yes_no(change.negative_change_clear),
        ])
    return csv_response(
        BYTE_ORDER_MARK + csv_lines(rows), filename(entity, "comparison", f"{before} vs {now}" if before else "")
    )


@router.get("/reviews.csv")
async def export_reviews(
    theme_id: int | None = Query(None, description="Only this theme's reviews; every theme when omitted"),
    sentiment: Sentiment | None = Query(None, description="Only reviews with this sentiment for the theme"),
    period: list[datetime] = Query([], description=PERIOD_DESCRIPTION),
    interval: TrendInterval | None = Query(None, description=INTERVAL_DESCRIPTION),
    rating: list[str] = Query([], description=RATING_DESCRIPTION),
    rating_scale: str | None = Query(None, description=RATING_SCALE_DESCRIPTION),
    q: str | None = Query(None, description=SEARCH_DESCRIPTION),
    themes: list[str] = Query([], alias="theme", description=THEME_DESCRIPTION),
    match_mode: Match = Query(Match.ANY, alias="match", description="How several `theme` filters combine"),
    entity: Entity = Depends(get_readable_entity),
    db: AsyncSession = Depends(get_db),
):
    """Tagged reviews: one row per review and theme it raises (merged candidates under the theme
    they merged into), with the review's sentiment for that theme, newest first. With `theme_id`,
    only that theme's reviews, filtered as a theme's page filters them; without it, every
    classified review with all its themes, `general` included. `theme` and `match` filter the reviews
    as the All reviews page does (each review still lists all its themes)."""
    chosen = await resolve_themes(db, entity, parse_themes(themes))
    by_theme = ReviewFilters(entity.id, chosen, match_mode, None, None, None).theme_condition()
    match = both(rating_filter(rating_scale, rating), period_filter(period, interval), search_filter(q), by_theme)
    theme = await get_theme(db, entity, theme_id) if theme_id is not None else None

    per_review, combined = per_review_theme_sentiment(entity.id, theme.id if theme else None)
    query = (
        select(
            Review.id, Review.review_date, Review.rating, Review.title, Review.text,
            Theme.name, Theme.kind, combined,
        )
        .select_from(per_review)
        .join(Review, Review.id == per_review.c.review_id)
        .join(Theme, Theme.id == per_review.c.theme_id)
        .order_by(Review.review_date.desc().nulls_last(), Review.id.desc(), Theme.name)
    )
    if sentiment is not None:
        query = query.where(combined == sentiment.value)
    if match is not None:
        query = query.where(match)

    async def body() -> AsyncIterator[str]:
        yield BYTE_ORDER_MARK + csv_lines(
            [["review_id", "date", "rating", "title", "text", "theme", "kind", "sentiment"]]
        )
        result = await db.stream(query.execution_options(yield_per=STREAM_BATCH))
        async for batch in result.partitions():
            yield csv_lines(
                [
                    review_id,
                    f"{review_date:%Y-%m-%d}" if review_date else "",
                    float(rating_value) if rating_value is not None else "",
                    title or "",
                    text,
                    name,
                    KIND_LABELS[ThemeKind(kind)],
                    review_sentiment or "",
                ]
                for review_id, review_date, rating_value, title, text, name, kind, review_sentiment in batch
            )

    parts = [
        "reviews",
        theme.name if theme else "",
        sentiment.value if sentiment else "",
        period_suffix(period, interval),
        rating_suffix(rating),
        search_suffix(q),
    ]
    return csv_response(body(), filename(entity, *parts))
