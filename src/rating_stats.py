"""Read-side rating summaries: how many reviews have a rating, the average, and the distribution.

Each import declares its own rating scale, so an entity whose files used different scales gets one
summary per scale instead of an average that mixes 1-5 stars with 0-10 scores. Ratings never feed
the analysis (see CLAUDE.md); this is only shown alongside the results.
"""
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion import BinaryScale, LabelScale, NumericScale, RatingScale
from models import Import, Review, ReviewTheme, Theme
from schemas import RatingBucket, RatingScaleSummary, RatingSummary
from theme_stats import per_review_theme_sentiment

# A numeric scale with whole-number ends and at most this many levels gets one bucket per level
# (1-5 stars, 0-10 scores). A wider scale, or one with fractional ends, is split into
# NUMERIC_BINS equal ranges, so 1-100 doesn't get a hundred bars.
MAX_LEVELS = 11
NUMERIC_BINS = 5

scale_adapter: TypeAdapter[NumericScale | BinaryScale | LabelScale] = TypeAdapter(RatingScale)


def format_number(value: Decimal) -> str:
    return f"{float(value):g}"


@dataclass(frozen=True)
class BucketRange:
    """One bar of the distribution: ratings with low <= rating < high (no upper bound when high is None)."""
    label: str
    low: Decimal
    high: Decimal | None

    def holds(self, rating: Decimal) -> bool:
        return rating >= self.low and (self.high is None or rating < self.high)


def bucket_ranges(scale: NumericScale | BinaryScale | LabelScale) -> list[BucketRange]:
    """The buckets of a scale, lowest first. The top bucket has no upper bound, so the scale's
    maximum falls in it. Counting and filtering both use these, so a filtered list always matches
    the bar that was clicked."""
    match scale:
        case NumericScale():
            low, high = Decimal(str(scale.min)), Decimal(str(scale.max))
            if scale.min.is_integer() and scale.max.is_integer() and high - low + 1 <= MAX_LEVELS:
                # One bucket per whole level; a fractional rating (4.5) counts under the level below.
                edges = [low + step for step in range(int(high - low) + 1)]
                labels = [str(int(edge)) for edge in edges]
            else:
                width = (high - low) / NUMERIC_BINS
                edges = [low + width * step for step in range(NUMERIC_BINS)]
                labels = [f"{format_number(edge)}–{format_number(edge + width)}" for edge in edges]
        case BinaryScale():
            edges, labels = [Decimal(0), Decimal(1)], ["Negative", "Positive"]
        case LabelScale():
            edges = [Decimal(index) for index in range(len(scale.labels))]
            labels = list(scale.labels)
    return [
        BucketRange(label=label, low=edge, high=edges[index + 1] if index + 1 < len(edges) else None)
        for index, (label, edge) in enumerate(zip(labels, edges))
    ]


def summarize_scale(
    scale: NumericScale | BinaryScale | LabelScale, counts: dict[Decimal, int]
) -> RatingScaleSummary:
    """The count, average and distribution of the ratings given on one scale.

    `counts` maps each stored rating to the number of reviews with it. Stored values are the number
    itself for a numeric scale, 1 or 0 for binary, and the label's index for labels.
    """
    ranges = bucket_ranges(scale)
    tallies = [0] * len(ranges)
    for rating, count in counts.items():
        # A rating below the lowest edge can't be stored, but would count in the lowest bucket.
        index = next((index for index, bucket in enumerate(ranges) if bucket.holds(rating)), 0)
        tallies[index] += count
    total = sum(counts.values())
    average = float(sum(rating * count for rating, count in counts.items()) / total) if total else 0.0
    buckets = [RatingBucket(label=bucket.label, count=tally) for bucket, tally in zip(ranges, tallies)]
    return RatingScaleSummary(
        scale=scale.model_dump(), count=total, average=average, buckets=list(reversed(buckets))
    )


def parse_scale(raw: Any) -> NumericScale | BinaryScale | LabelScale:
    """A rating scale from its JSON form. Raises pydantic's ValidationError."""
    return scale_adapter.validate_python(raw)


def rating_condition(scale: NumericScale | BinaryScale | LabelScale, labels: list[str]):
    """A SQL condition on Review: rated on this scale (by an import that declared it) and in one of
    the named buckets. Raises ValueError for a label the scale has no bucket for."""
    ranges = {bucket.label: bucket for bucket in bucket_ranges(scale)}
    unknown = [label for label in labels if label not in ranges]
    if unknown:
        raise ValueError(f"No rating bucket named {', '.join(map(repr, unknown))} on this scale.")
    in_bucket = [
        and_(Review.rating >= ranges[label].low, Review.rating < ranges[label].high)
        if ranges[label].high is not None
        else Review.rating >= ranges[label].low
        for label in labels
    ]
    return and_(on_scale(scale), or_(*in_bucket))


def on_scale(scale: NumericScale | BinaryScale | LabelScale):
    """A SQL condition on Review: rated on this scale, that is, imported by an import that declared it."""
    # jsonb equality ignores key order and compares numbers by value, so 5 matches 5.0.
    return Review.import_id.in_(select(Import.id).where(Import.rating_scale == scale.model_dump()))


@dataclass(frozen=True)
class RatingMoments:
    """Enough to give the mean and sample variance of a set of ratings, and to subtract one set
    from a larger one that contains it."""
    count: int
    total: float
    squares: float

    def __sub__(self, other: "RatingMoments") -> "RatingMoments":
        return RatingMoments(self.count - other.count, self.total - other.total, self.squares - other.squares)

    @property
    def mean(self) -> float | None:
        return self.total / self.count if self.count else None

    @property
    def variance(self) -> float:
        """The sample variance (n - 1), 0 for fewer than 2 ratings or rounding below 0."""
        if self.count < 2:
            return 0.0
        return max(0.0, (self.squares - self.total * self.total / self.count) / (self.count - 1))


async def theme_rating_moments(
    db: AsyncSession, entity_id: int, scale: NumericScale | BinaryScale | LabelScale
) -> tuple[RatingMoments, dict[int, RatingMoments]]:
    """The ratings on `scale` of the entity's classified reviews: all of them, and those that raise
    each theme (merged candidates under the theme they merged into, each review once). Two queries:
    the themes, and the total over distinct reviews (a review raising several themes counts once).
    """
    per_review, _ = per_review_theme_sentiment(entity_id)
    rating = Review.rating
    rows = await db.execute(
        select(per_review.c.theme_id, func.count(), func.sum(rating), func.sum(rating * rating))
        .select_from(per_review)
        .join(Review, Review.id == per_review.c.review_id)
        .where(on_scale(scale), rating.is_not(None))
        .group_by(per_review.c.theme_id)
    )
    by_theme = {
        theme_id: RatingMoments(count, float(total), float(squares))
        for theme_id, count, total, squares in rows
    }
    classified = (
        select(ReviewTheme.review_id)
        .join(Theme, Theme.id == ReviewTheme.theme_id)
        .where(Theme.entity_id == entity_id)
    )
    count, total, squares = (
        await db.execute(
            select(
                func.count(), func.coalesce(func.sum(rating), 0), func.coalesce(func.sum(rating * rating), 0)
            ).where(Review.id.in_(classified), on_scale(scale), rating.is_not(None))
        )
    ).one()
    return RatingMoments(count, float(total), float(squares)), by_theme


async def rating_summary(db: AsyncSession, entity_id: int) -> RatingSummary:
    """Every rating scale used by the entity's imports, most ratings first, in one query."""
    rows = await db.execute(
        select(Import.rating_scale, Review.rating, func.count())
        .join(Import, Review.import_id == Import.id)
        .where(Review.entity_id == entity_id)
        .group_by(Import.rating_scale, Review.rating)
    )
    total_reviews = 0
    by_scale: dict[str, dict[Decimal, int]] = defaultdict(dict)
    scales: dict[str, Any] = {}
    for raw_scale, rating, count in rows:
        total_reviews += count
        if raw_scale is None or rating is None:
            continue
        try:
            scale = parse_scale(raw_scale)
        except ValidationError:
            continue                    # an import with an unreadable scale has no usable ratings
        key = json.dumps(scale.model_dump(), sort_keys=True)
        scales[key] = scale
        by_scale[key][rating] = by_scale[key].get(rating, 0) + count

    summaries = [summarize_scale(scales[key], counts) for key, counts in by_scale.items()]
    summaries.sort(key=lambda summary: -summary.count)
    return RatingSummary(
        total_reviews=total_reviews,
        rated_reviews=sum(summary.count for summary in summaries),
        scales=summaries,
    )
